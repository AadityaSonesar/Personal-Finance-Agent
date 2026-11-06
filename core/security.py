"""Trust-boundary primitives: path confinement, sanitization, redaction.

Every external string (merchant, description, category, uploaded filename) is
untrusted data. Nothing in this module ever interprets such text as an
instruction; it only cleans, bounds and flags it.
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
_DRIVE_RE = re.compile(r"^[a-zA-Z]:")


class PathAccessError(PermissionError):
    """Raised when a requested path escapes the permitted area."""


def _within(candidate: Path, base: Path) -> bool:
    cand = os.path.normcase(os.path.abspath(str(candidate)))
    root = os.path.normcase(os.path.abspath(str(base)))
    if cand == root:
        return True
    return cand.startswith(root.rstrip(os.sep) + os.sep)


def safe_project_path(
    relative: str | Path,
    *,
    must_exist: bool = False,
    allowed_prefix: str | None = None,
    root: Path | None = None,
) -> Path:
    """Resolve ``relative`` inside the project (or an allowed subdirectory).

    Rejects absolute paths, drive qualifiers, UNC paths, ``..`` segments,
    null bytes, reserved device names, encoded traversal and symlink escapes.
    """
    if relative is None:
        raise PathAccessError("A path is required.")
    raw = unquote(str(relative)).strip()
    if not raw:
        raise PathAccessError("An empty path is not allowed.")
    if "\x00" in raw:
        raise PathAccessError("Null bytes are not allowed in paths.")

    normalized = raw.replace("\\", "/")
    if normalized.startswith("//"):
        raise PathAccessError("UNC paths are not allowed.")
    if _DRIVE_RE.match(normalized) or normalized.startswith("/") or normalized.startswith("~"):
        raise PathAccessError("Absolute paths are not allowed.")

    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if not parts:
        raise PathAccessError("An empty path is not allowed.")
    for part in parts:
        if part == "..":
            raise PathAccessError("Parent directory traversal is not allowed.")
        if part.split(".")[0].casefold() in _WINDOWS_RESERVED:
            raise PathAccessError("Reserved device names are not allowed in paths.")

    base = (root or PROJECT_ROOT).resolve()
    candidate = (base / Path(*parts)).resolve()

    if not _within(candidate, base):
        raise PathAccessError("Path escapes the permitted project area.")
    if allowed_prefix:
        allowed = (base / allowed_prefix).resolve()
        if not _within(candidate, allowed):
            raise PathAccessError(f"Path is outside the permitted area ({allowed_prefix}).")
    if must_exist and not candidate.exists():
        raise FileNotFoundError(f"File not found: {relative}")
    return candidate


def safe_data_path(relative: str | Path, *, data_root: Path, must_exist: bool = False) -> Path:
    """Confine a path to the configured data directory.

    ``relative`` is resolved from the data root itself, so callers pass paths such
    as ``staging/report.csv`` and can never reach outside the data directory.
    """
    return safe_project_path(relative, must_exist=must_exist, root=Path(data_root).resolve())


# --------------------------------------------------------------------------- #
# Sanitization
# --------------------------------------------------------------------------- #

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")
_ANGLE = str.maketrans({"<": "‹", ">": "›"})
_FENCE_TOKENS = re.compile(r"(```+|~~~+|<\|[^|]*\|>|\[/?INST\]|\[/?SYS\]|\{\{|\}\}|</?script)", re.IGNORECASE)
_WS = re.compile(r"\s+")

INJECTION_SIGNATURES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("instruction_override", re.compile(r"\b(ignore|disregard|forget|override)\b[^.]{0,40}\b(instruction|rule|prompt|direction|guideline)s?\b", re.IGNORECASE)),
    ("role_manipulation", re.compile(r"\b(you are now|act as|pretend to be|new system prompt|system\s*:)\b", re.IGNORECASE)),
    ("balance_tampering", re.compile(r"\b(set|change|reset|force|update)\b[^.]{0,30}\b(balance|score|limit|budget|amount)\b", re.IGNORECASE)),
    ("approval_bypass", re.compile(r"\b(auto[m ]?approve|mark all (transactions )?approved|skip (the )?(review|approval|validation))\b", re.IGNORECASE)),
    ("secret_exfiltration", re.compile(r"\b(reveal|print|show|leak|dump)\b[^.]{0,30}\b(api[ _-]?key|secret|password|token|credential)s?\b", re.IGNORECASE)),
    ("tool_invocation", re.compile(r"\b(call|invoke|execute|run)\b[^.]{0,30}\b(tool|function|shell|command|python)\b", re.IGNORECASE)),
)


@dataclass
class SanitizedText:
    """Result of cleaning one untrusted field."""

    text: str
    flags: list[str] = field(default_factory=list)
    truncated: bool = False

    @property
    def suspicious(self) -> bool:
        return bool(self.flags)


def sanitize_text(value: object, *, max_length: int = 280) -> SanitizedText:
    """Clean an untrusted string and report instruction-injection signatures."""
    if value is None:
        return SanitizedText("")
    text = str(value)
    flags: list[str] = []

    if _CONTROL.search(text) or _ZERO_WIDTH.search(text):
        flags.append("control_characters")
        text = _ZERO_WIDTH.sub("", _CONTROL.sub(" ", text))

    text = unicodedata.normalize("NFKC", text)
    if _FENCE_TOKENS.search(text):
        flags.append("boundary_markers")
        text = _FENCE_TOKENS.sub(" ", text)

    if re.search(r"[<>]", text):
        flags.append("markup")
        text = text.translate(_ANGLE)

    for label, pattern in INJECTION_SIGNATURES:
        if pattern.search(text):
            flags.append(label)

    text = _WS.sub(" ", text).strip()
    truncated = False
    if len(text) > max_length:
        text = text[: max(0, max_length - 1)].rstrip() + "…"
        truncated = True
    return SanitizedText(text=text, flags=sorted(set(flags)), truncated=truncated)


def detect_instruction_override(value: object) -> list[str]:
    """Return the injection signature labels present in ``value``."""
    if value is None:
        return []
    text = _ZERO_WIDTH.sub("", _CONTROL.sub(" ", str(value)))
    return sorted({label for label, pattern in INJECTION_SIGNATURES if pattern.search(text)})


def redact_account(value: object) -> str:
    """Mask any digit run longer than 7 characters, keeping the last four."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""

    def _mask(match: re.Match[str]) -> str:
        digits = match.group(0)
        return "•" * max(0, len(digits) - 4) + digits[-4:]

    masked = re.sub(r"\d{8,}", _mask, text)
    return masked[:64]


def sanitize_filename(name: object) -> str:
    """Reduce an uploaded filename to a safe display label."""
    text = sanitize_text(name, max_length=120).text
    # Drop every directory component before cleaning: only the base name survives.
    parts = [p for p in re.split(r"[\\/]+", text) if p not in ("", ".")]
    text = parts[-1] if parts else ""
    text = re.sub(r"[^A-Za-z0-9._ -]", "", text).strip(" .")
    if not text:
        text = "statement"
    return text[:120]


def hash_identifier(*parts: object, prefix: str = "ID", length: int = 12) -> str:
    """Deterministic identifier derived from its parts (never random)."""
    payload = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length].upper()
    return f"{prefix}-{digest}"


def assert_safe_json(value: object, *, depth: int = 0) -> None:
    """Defensive guard against non-serializable tool output."""
    if depth > 12:
        raise ValueError("Payload nesting too deep.")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("Payload keys must be strings.")
            assert_safe_json(item, depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            assert_safe_json(item, depth=depth + 1)
    elif isinstance(value, (str, int, float, bool)) or value is None:
        return
    else:
        raise ValueError(f"Unsupported payload type: {type(value).__name__}")
