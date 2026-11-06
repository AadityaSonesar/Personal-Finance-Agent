"""Session context, flash messaging and shared helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import streamlit as st

from core.config import AppConfig, get_config
from core.llm import ai_status
from core.services import current_store
from core.storage import DatasetStore, list_datasets, set_active_dataset


@dataclass
class UIContext:
    config: AppConfig
    store: DatasetStore | None
    datasets: list[dict[str, Any]] = field(default_factory=list)
    ai: dict[str, Any] = field(default_factory=dict)

    @property
    def is_demo(self) -> bool:
        return bool(self.store and self.store.is_demo)


def build_context() -> UIContext:
    config = get_config()
    store = current_store(config)
    datasets = list_datasets(config)
    if store is not None:
        set_active_dataset(store.dataset_id, config=config)
    try:
        ai = ai_status(config)
    except Exception:
        ai = {"mode": "deterministic", "available": False, "reason": "AI status unavailable."}
    return UIContext(config=config, store=store, datasets=datasets, ai=ai)


def flash(kind: str, message: str) -> None:
    st.session_state.setdefault("au_flash", []).append({"kind": kind, "message": message})


def render_flash() -> None:
    messages = st.session_state.pop("au_flash", [])
    for item in messages:
        kind = item.get("kind", "info")
        renderer = {
            "success": st.success,
            "warning": st.warning,
            "error": st.error,
        }.get(kind, st.info)
        renderer(item.get("message", ""))


def source_badges(ctx: UIContext) -> list[tuple[str, str]]:
    badges: list[tuple[str, str]] = []
    if ctx.store is None:
        return badges
    if ctx.store.is_demo:
        badges.append(("DEMONSTRATION DATA", "warning"))
    else:
        badges.append(("Uploaded statements", "info"))
    badges.append((f"Rule v{ctx.config.rule_version}", "neutral"))
    return badges


def ai_chip(ctx: UIContext) -> tuple[str, str]:
    if ctx.ai.get("available"):
        return (f"AI · {ctx.ai.get('model', 'configured')}", "violet")
    return ("AI · deterministic mode", "neutral")
