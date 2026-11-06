"""MCP client: persistent STDIO session with discovery, retries and fallback.

The runtime owns one long-lived worker coroutine that enters the MCP session and
serves requests from a thread-safe queue, so sessions are never entered and
exited from different tasks (which breaks anyio cancel scopes).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import sys
import threading
import time
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Any

from core.config import PROJECT_ROOT, AppConfig, get_config
from core.observability import get_logger
from core.tools import ToolCallFailed, available_tools, call_tool

logger = get_logger("aurelia.mcp")


class MCPUnavailable(RuntimeError):
    pass


class MCPTimeout(RuntimeError):
    pass


class MCPTransportError(RuntimeError):
    pass


def _build_server_params(config: AppConfig):
    from mcp import StdioServerParameters

    env = dict(os.environ)
    env.setdefault("PYTHONPATH", str(PROJECT_ROOT))
    env["PYTHONUNBUFFERED"] = "1"
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", config.mcp.server_module],
        cwd=str(PROJECT_ROOT),
        env=env,
    )


class MCPRuntime:
    """Thread + event loop + MCP STDIO session lifecycle."""

    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config or get_config()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._state = "stopped"
        self._reason = ""
        self._tools: list[dict[str, Any]] = []
        self._failed_at = 0.0

    # -- lifecycle ---------------------------------------------------------
    def ensure_ready(self) -> tuple[bool, str]:
        with self._lock:
            if self._state == "ready":
                return True, ""
            if not self.config.mcp.enabled:
                return False, "MCP is disabled in configuration."
            if self._state == "failed" and (time.time() - self._failed_at) < 20:
                return False, self._reason
            self._state = "starting"
            self._ready.clear()
            self._thread = threading.Thread(
                target=self._thread_main, name="mcp-runtime", daemon=True
            )
            self._thread.start()

        if not self._ready.wait(self.config.mcp.startup_timeout_seconds):
            with self._lock:
                self._state = "failed"
                self._reason = "MCP server did not become ready within the startup timeout."
                self._failed_at = time.time()
            return False, self._reason

        with self._lock:
            return (True, "") if self._state == "ready" else (False, self._reason)

    def _thread_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._worker())
        except BaseException as exc:  # pragma: no cover - defensive
            with self._lock:
                self._state = "failed"
                self._reason = f"{type(exc).__name__}: {exc}"[:240]
                self._failed_at = time.time()
            self._ready.set()
            logger.warning("MCP worker stopped: %s", self._reason)
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    async def _worker(self) -> None:
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        self._queue = asyncio.Queue()
        try:
            async with AsyncExitStack() as stack:
                params = _build_server_params(self.config)
                read_stream, write_stream = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(
                    ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=timedelta(seconds=max(30, self.config.mcp.call_timeout_seconds)),
                    )
                )
                await session.initialize()
                listed = await session.list_tools()
                self._tools = [
                    {
                        "name": tool.name,
                        "description": tool.description or "",
                        "parameters": tool.inputSchema,
                    }
                    for tool in listed.tools
                ]
                with self._lock:
                    self._state = "ready"
                    self._reason = ""
                self._ready.set()

                while True:
                    item = await self._queue.get()
                    if item is None:
                        break
                    kind, payload, future = item
                    try:
                        if kind == "call":
                            result = await session.call_tool(
                                payload["name"],
                                payload.get("arguments") or {},
                                read_timeout_seconds=timedelta(seconds=self.config.mcp.call_timeout_seconds),
                            )
                            future.set_result(result)
                        elif kind == "list":
                            future.set_result(await session.list_tools())
                        else:
                            future.set_exception(MCPTransportError(f"Unknown request kind '{kind}'."))
                    except Exception as exc:
                        future.set_exception(exc)
        except Exception as exc:  # transport or handshake failure
            with self._lock:
                if self._state != "stopped":
                    self._state = "failed"
                self._reason = f"{type(exc).__name__}: {exc}"[:240]
                self._failed_at = time.time()
            self._ready.set()
            logger.warning("MCP session unavailable: %s", self._reason)
        finally:
            with self._lock:
                if self._state == "ready":
                    self._state = "stopped"

    def shutdown(self, timeout: float = 5.0) -> None:
        loop = self._loop
        if loop is not None and loop.is_running() and self._queue is not None:
            try:
                self._enqueue(None, timeout=2.0)
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        with self._lock:
            self._state = "stopped"

    # -- requests ----------------------------------------------------------
    def _enqueue(self, item: Any, timeout: float = 5.0) -> None:
        loop = self._loop
        if loop is None or not loop.is_running() or self._queue is None:
            raise MCPUnavailable("MCP event loop is not running.")

        async def _put() -> None:
            await self._queue.put(item)

        asyncio.run_coroutine_threadsafe(_put(), loop).result(timeout=timeout)

    def _request(self, kind: str, payload: dict[str, Any], timeout: float | None = None):
        ready, reason = self.ensure_ready()
        if not ready:
            raise MCPUnavailable(reason)
        future: concurrent.futures.Future = concurrent.futures.Future()
        self._enqueue((kind, payload, future), timeout=5.0)
        try:
            return future.result(timeout or self.config.mcp.call_timeout_seconds + 5)
        except concurrent.futures.TimeoutError as exc:
            raise MCPTimeout(f"MCP {kind} request timed out.") from exc

    @staticmethod
    def _extract(result: Any) -> dict[str, Any]:
        is_error = bool(getattr(result, "isError", False))
        structured = getattr(result, "structuredContent", None)
        if isinstance(structured, dict):
            payload: dict[str, Any] = structured
            if set(payload) == {"result"} and isinstance(payload["result"], dict):
                payload = payload["result"]
        else:
            texts = []
            for item in getattr(result, "content", []) or []:
                text = getattr(item, "text", None)
                if text:
                    texts.append(text)
            joined = "\n".join(texts).strip()
            try:
                payload = json.loads(joined) if joined else {}
            except json.JSONDecodeError:
                payload = {"ok": False, "error": {"type": "protocol_error", "message": joined[:400]}}

        if is_error:
            raise MCPTransportError(
                str(payload.get("error", {}).get("message") if isinstance(payload.get("error"), dict) else payload)[:240]
            )
        if not isinstance(payload, dict):
            raise MCPTransportError("MCP tool returned an unexpected payload type.")
        return payload

    def call_tool(self, name: str, arguments: dict[str, Any], *, authorized: bool = False) -> dict[str, Any]:
        args = dict(arguments)
        if authorized:
            args.setdefault("authorized", True)

        attempts = max(0, self.config.mcp.retries) + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                result = self._request("call", {"name": name, "arguments": args})
                return self._extract(result)
            except (MCPTimeout, MCPTransportError, MCPUnavailable) as exc:
                last_error = exc
                with self._lock:
                    if isinstance(exc, (MCPTimeout, MCPTransportError)):
                        self._state = "stopped"
                if attempt + 1 < attempts:
                    delay = min(
                        self.config.mcp.max_delay_ms,
                        self.config.mcp.base_delay_ms * (2 ** attempt),
                    )
                    time.sleep(delay / 1000.0)
        raise MCPTransportError(f"MCP call '{name}' failed after {attempts} attempt(s): {last_error}")

    def list_tools(self) -> list[dict[str, Any]]:
        if self._tools:
            return self._tools
        result = self._request("list", {})
        tools = [
            {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.inputSchema,
            }
            for tool in getattr(result, "tools", []) or []
        ]
        self._tools = tools
        return tools

    def status(self) -> dict[str, Any]:
        with self._lock:
            state = self._state
            reason = self._reason
            tools = list(self._tools)
        return {
            "available": state == "ready",
            "state": state if state != "starting" else "starting",
            "reason": reason,
            "tools": [t["name"] for t in tools],
            "tool_count": len(tools),
            "server": f"{sys.executable} -m {self.config.mcp.server_module}",
            "transport": "stdio",
            "prefer_mcp": self.config.mcp.prefer_for_workflow,
        }


_runtimes: dict[str, MCPRuntime] = {}
_runtime_lock = threading.Lock()


def get_runtime(config: AppConfig | None = None) -> MCPRuntime:
    config = config or get_config()
    key = str(config.data_root)
    with _runtime_lock:
        runtime = _runtimes.get(key)
        if runtime is None:
            runtime = MCPRuntime(config)
            _runtimes[key] = runtime
        return runtime


def probe(config: AppConfig | None = None) -> dict[str, Any]:
    """Connect (or reuse) the MCP server and report discovery results."""
    config = config or get_config()
    runtime = get_runtime(config)
    ready, reason = runtime.ensure_ready()
    info = runtime.status()
    info["local_tools"] = available_tools()
    if ready:
        try:
            discovered = runtime.list_tools()
            info["discovered"] = [t["name"] for t in discovered]
            info["mismatch"] = sorted(
                set(available_tools()) ^ {t["name"] for t in discovered}
            )
        except Exception as exc:
            info["available"] = False
            info["reason"] = f"Tool discovery failed: {type(exc).__name__}: {exc}"
    else:
        info["available"] = False
        info["reason"] = reason
    return info


def call_via_mcp(name: str, params: dict[str, Any], *, authorized: bool = False, config: AppConfig | None = None) -> dict[str, Any]:
    """Call a tool through MCP, falling back to the local registry on failure."""
    config = config or get_config()
    try:
        runtime = get_runtime(config)
        ready, reason = runtime.ensure_ready()
        if ready:
            return runtime.call_tool(name, params, authorized=authorized)
        logger.warning("MCP unavailable (%s); using local tool registry.", reason)
    except Exception as exc:
        logger.warning("MCP call failed (%s); using local tool registry.", type(exc).__name__)
    return call_tool(name, params, authorized=authorized, config=config)


__all__ = [
    "MCPRuntime",
    "MCPTimeout",
    "MCPTransportError",
    "MCPUnavailable",
    "ToolCallFailed",
    "call_via_mcp",
    "get_runtime",
    "probe",
]
