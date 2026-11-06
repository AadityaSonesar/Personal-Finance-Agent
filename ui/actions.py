"""User-triggered actions shared by sidebar and pages."""

from __future__ import annotations

from typing import Any

import streamlit as st

from core.config import AppConfig
from core.services import (
    create_dataset,
    demo_store,
    run_analysis,
    stage_demo_rows,
    stage_uploaded_file,
)
from core.storage import DatasetStore, set_active_dataset
from ui.context import flash


def analysis_settings(config: AppConfig) -> tuple[bool, bool]:
    use_ai = bool(st.session_state.get("au_use_ai", config.ai.enabled))
    use_mcp = bool(st.session_state.get("au_use_mcp", config.mcp.prefer_for_workflow and config.mcp.enabled))
    return use_ai, use_mcp


def run_analysis_flow(
    store: DatasetStore,
    *,
    config: AppConfig,
    pending_file: str | None = None,
    caption: str = "Running the multi-agent analysis…",
    redirect: bool = True,
) -> dict[str, Any] | None:
    use_ai, use_mcp = analysis_settings(config)
    try:
        with st.spinner(caption):
            result = run_analysis(
                store,
                pending_file=pending_file,
                use_ai=use_ai,
                prefer_mcp=use_mcp,
                config=config,
            )
    except Exception as exc:  # surfaced to the user, never swallowed
        flash("error", f"Analysis failed: {type(exc).__name__}: {exc}")
        st.rerun()
        return None

    st.session_state["au_last_result"] = result
    score = result.get("health_score")
    score_text = f"health {score:.1f}/100" if isinstance(score, (int, float)) else "health score unavailable"
    flash(
        "success" if result.get("status") == "completed" else "warning",
        f"Analysis {result.get('status')} in {result.get('total_ms', 0) / 1000:.1f}s · "
        f"{result.get('recommendation_count', 0)} recommendation(s) · {score_text} · "
        f"{result.get('tool_layer', 'local')} tool layer",
    )
    if redirect:
        st.rerun()
    return result


def load_demo_flow(config: AppConfig) -> None:
    try:
        store = demo_store(config)
        info = stage_demo_rows(store, config=config)
        set_active_dataset(store.dataset_id, config=config)
    except Exception as exc:
        flash("error", f"Could not load demonstration data: {type(exc).__name__}: {exc}")
        st.rerun()
        return
    if info.get("staged"):
        flash("info", f"Loaded {info.get('rows', 0)} demonstration transactions into an isolated dataset.")
    run_analysis_flow(store, config=config)


def create_dataset_flow(name: str, config: AppConfig) -> None:
    clean = (name or "").strip()
    if not clean:
        flash("error", "Enter a name for the dataset.")
        st.rerun()
        return
    store = create_dataset(clean, kind="upload", config=config)
    set_active_dataset(store.dataset_id, config=config)
    flash("success", f"Created dataset “{clean}”. Upload a statement to begin.")
    st.rerun()


def upload_flow(store: DatasetStore, uploaded: Any, *, config: AppConfig) -> None:
    if uploaded is None:
        flash("warning", "Choose a statement file first.")
        st.rerun()
        return
    try:
        info = stage_uploaded_file(store, uploaded.name, uploaded.getvalue(), config=config)
    except Exception as exc:
        flash("error", f"Upload rejected: {type(exc).__name__}: {exc}")
        st.rerun()
        return
    flash("info", f"Stored {info['file_name']} ({info['bytes'] / 1024:.0f} KB) and started the analysis.")
    run_analysis_flow(store, config=config, pending_file=info["relative_path"])
