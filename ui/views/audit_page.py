"""Audit and system monitoring: traceability and measured performance."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core.audit import read_audit_logs
from core.metrics import compute_metrics
from core.tools import ToolGateway, tool_catalog
from ui import data
from ui.components import (
    KPI,
    agent_latency_bars,
    badge,
    esc,
    note,
    page_header,
    render_chart,
    render_kpis,
    section,
)
from ui.context import UIContext, source_badges

METRIC_ORDER = [
    ("validation_rate", "Data validation rate"),
    ("rejected_records", "Rejected records"),
    ("duplicates", "Duplicate detections"),
    ("agent_latency", "Average agent latency"),
    ("parallel_latency", "Parallel stage latency"),
    ("sequential_latency", "Sequential stage latency"),
    ("workflow_success", "Workflow success rate"),
    ("tool_success", "Tool execution success rate"),
    ("anomalies", "Anomalies detected"),
    ("alert_resolution", "Average alert resolution"),
    ("sla_compliance", "SLA compliance"),
    ("review_completion", "Human review completion"),
]


def _performance_tab(store, ctx) -> None:
    metrics = compute_metrics(store, config=ctx.config)
    counts = metrics["counts"]

    render_kpis(
        [
            KPI("Validated transactions", f"{counts['transactions']:,}", "Stored records"),
            KPI("Workflow runs", f"{counts['workflows']}", "Recorded executions"),
            KPI("Audit events", f"{counts['audit_events']:,}", "Append-only trail"),
            KPI("Open alerts", f"{counts['alerts_open']}", f"{counts['alerts_breached']} past SLA", "warning" if counts["alerts_open"] else "positive"),
            KPI("Tool calls", f"{len(metrics['recent_tool_calls'])}", "Most recent 25 shown below"),
        ]
    )

    st.write("")
    section("Measured performance", "Computed from execution records only — no synthetic values")
    entries = metrics["metrics"]
    keys = [key for key, _ in METRIC_ORDER if key in entries]
    for start in range(0, len(keys), 4):
        row = keys[start:start + 4]
        columns = st.columns(len(row), gap="medium")
        for column, key in zip(columns, row):
            entry = entries[key]
            with column:
                st.markdown(
                    f"""
                    <div class="au-card tight">
                      <div class="au-kpi-top">
                        <span class="au-kpi-label">{esc(entry['label'])}</span>
                        {badge("measured", "positive") if entry["available"] else badge("insufficient data", "warning")}
                      </div>
                      <div class="au-kpi-value neutral" style="font-size:1.35rem;margin-top:6px">{esc(entry['display'])}</div>
                      <div class="au-kpi-sub">{esc(entry['note'] or '')}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    st.write("")
    left, right = st.columns([1.15, 1], gap="large")
    with left:
        with st.container(border=True):
            section("Agent latency", "Average measured duration per agent")
            figure = agent_latency_bars(metrics["agent_table"])
            if figure:
                render_chart(figure, height=320, key="audit_agent_latency")
            else:
                note("No agent executions recorded yet.")
    with right:
        with st.container(border=True):
            section("Agent detail", "Runs and latency range")
            if metrics["agent_table"]:
                st.dataframe(
                    pd.DataFrame(metrics["agent_table"]),
                    hide_index=True,
                    width="stretch",
                    column_config={
                        "agent": "Agent",
                        "average_ms": st.column_config.NumberColumn("Average ms", format="%.1f"),
                        "runs": "Runs",
                        "max_ms": st.column_config.NumberColumn("Max ms", format="%.1f"),
                    },
                )
            else:
                note("Nothing to show yet.")
            if metrics["recent_failures"]:
                st.markdown('<div class="au-kpi-label" style="margin-top:12px">Recent failures</div>', unsafe_allow_html=True)
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Time": row.get("timestamp", "")[:19].replace("T", " "),
                                "Actor": row.get("actor", ""),
                                "Event": row.get("event_type", ""),
                                "Error": (row.get("error") or "")[:90],
                            }
                            for row in metrics["recent_failures"]
                        ]
                    ),
                    hide_index=True,
                    width="stretch",
                )


def _workflows_tab(store, ctx) -> None:
    workflows = data.workflows(store)
    if not workflows:
        note("No workflow runs recorded for this dataset yet.")
        return

    options = {
        f"{flow.get('workflow_id')} · {str(flow.get('started_at', ''))[:19].replace('T', ' ')} · {flow.get('status')}": flow
        for flow in workflows[:40]
    }
    selection = st.selectbox("Workflow run", list(options.keys()), key="audit_workflow_select")
    flow = options[selection]

    tone = "positive" if flow.get("status") == "completed" else ("warning" if flow.get("status") == "partial" else "critical")
    render_kpis(
        [
            KPI("Status", str(flow.get("status", "")).title(), flow.get("workflow_id", ""), tone),
            KPI("Total latency", f"{flow.get('total_ms', 0):.0f} ms", "Measured wall clock"),
            KPI("Parallel stages", f"{flow.get('parallel_ms', 0):.0f} ms", "Concurrent analysis"),
            KPI("Sequential stages", f"{flow.get('sequential_ms', 0):.0f} ms", "Dependent stages"),
            KPI("Tool layer", str(flow.get("tool_layer", "—")), "Where tool calls executed"),
            KPI("Health score", f"{flow.get('health_score'):.1f}" if flow.get("health_score") is not None else "—", "At completion"),
        ]
    )

    st.write("")
    with st.container(border=True):
        section("Agent execution", "Status, latency and summary")
        agents = flow.get("agents", []) or []
        if agents:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Agent": agent.get("agent"),
                            "Status": agent.get("status"),
                            "Latency (ms)": agent.get("duration_ms"),
                            "Summary": agent.get("summary", ""),
                            "Error": agent.get("error", ""),
                            "Skipped reason": agent.get("skipped_reason", ""),
                        }
                        for agent in agents
                    ]
                ),
                hide_index=True,
                width="stretch",
                column_config={"Latency (ms)": st.column_config.NumberColumn(format="%.1f")},
            )
        else:
            note("No agent records in this run.")

    if flow.get("errors"):
        st.warning("Errors: " + "; ".join(flow["errors"][:5]))
    if flow.get("notes"):
        st.markdown("")
        with st.container(border=True):
            section("Run notes", "Warnings and data-quality observations")
            st.markdown(
                '<ul class="au-facts">' + "".join(f"<li>{esc(item)}</li>" for item in flow["notes"]) + "</ul>",
                unsafe_allow_html=True,
            )
    st.caption(
        "A failed stage skips its dependants rather than allowing them to compute from missing inputs, "
        "which is why a run can be marked partial."
    )


def _trail_tab(store, ctx) -> None:
    rows = read_audit_logs(limit=0, dataset_id=store.dataset_id, config=ctx.config)
    if not rows:
        note("No audit events recorded for this dataset yet.")
        return

    event_types = sorted({row.get("event_type", "") for row in rows})
    workflows = sorted({row.get("workflow_id", "") for row in rows if row.get("workflow_id")}, reverse=True)
    col1, col2, col3 = st.columns([1.2, 1.4, 1])
    event_filter = col1.multiselect("Event type", event_types, default=event_types, key="audit_event_filter")
    flow_filter = col2.multiselect(
        "Workflow", workflows, default=workflows[:3], key="audit_flow_filter", help="Leave empty for all runs."
    )
    status_filter = col3.multiselect(
        "Status", ["ok", "running", "pending", "skipped", "failed", "clear"], default=["ok", "skipped", "failed", "pending"], key="audit_status_filter"
    )

    filtered = []
    for row in rows:
        if row.get("event_type") not in event_filter:
            continue
        if flow_filter and row.get("workflow_id") not in flow_filter:
            continue
        if status_filter and row.get("status") not in status_filter:
            continue
        filtered.append(row)

    st.caption(f"{len(filtered)} of {len(rows)} events for this dataset · newest first")
    frame = pd.DataFrame(
        [
            {
                "Timestamp": row.get("timestamp", "")[:19].replace("T", " "),
                "Workflow": row.get("workflow_id", ""),
                "Event": row.get("event_type", ""),
                "Actor": row.get("actor", ""),
                "Type": row.get("actor_type", ""),
                "Status": row.get("status", ""),
                "Priority": row.get("priority", ""),
                "Health": row.get("health_score", ""),
                "Latency (ms)": row.get("latency_ms", ""),
                "Reviewer action": row.get("reviewer_action", ""),
                "Error": (row.get("error") or "")[:60],
                "Rules": row.get("rule_version", ""),
            }
            for row in filtered[:400]
        ]
    )
    st.dataframe(frame, hide_index=True, width="stretch", height=460)
    st.download_button(
        "Download filtered audit trail (CSV)",
        data=frame.to_csv(index=False).encode("utf-8"),
        file_name=f"audit-{store.dataset_id}.csv",
        mime="text/csv",
    )
    st.caption(
        "Each row is append-only. Uploaded data, analysis results, recommendations and reviewer decisions share "
        "the workflow identifier so the whole chain can be reconstructed."
    )


def _tools_tab(store, ctx) -> None:
    section("MCP tool layer", "FastMCP server over STDIO with an approved in-process fallback")

    probe_info = st.session_state.get("au_mcp_probe")
    if st.button("Verify MCP connection", type="primary", key="audit_probe"):
        with st.spinner("Starting the MCP server and discovering tools…"):
            try:
                from mcp_server.client import probe

                probe_info = probe(ctx.config)
            except Exception as exc:
                probe_info = {"available": False, "state": "failed", "reason": f"{type(exc).__name__}: {exc}"}
            st.session_state["au_mcp_probe"] = probe_info

    if probe_info:
        available = bool(probe_info.get("available"))
        st.markdown(
            f"""
            <div class="au-card tight">
              <div class="au-kpi-top">
                <span class="au-kpi-label">Connection</span>
                {badge("connected" if available else "unavailable", "positive" if available else "critical")}
              </div>
              <div class="au-note" style="margin-top:6px">
                State: {esc(probe_info.get('state', 'unknown'))} · transport {esc(probe_info.get('transport', 'stdio'))} ·
                {esc(probe_info.get('tool_count', 0))} tools discovered<br>
                Server: {esc(probe_info.get('server', '—'))}<br>
                {esc(probe_info.get('reason', '') or 'Handshake complete; tool schemas validated against the local registry.')}
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        discovered = probe_info.get("discovered") or probe_info.get("tools") or []
        if discovered:
            st.write("")
            st.markdown(
                "".join(badge(name, "info") + " " for name in discovered),
                unsafe_allow_html=True,
            )
        if probe_info.get("mismatch"):
            st.warning(f"Schema mismatch between server and registry: {probe_info['mismatch']}")
    else:
        note("Verify the connection to inspect discovery, the server command and schema parity.")

    st.write("")
    with st.container(border=True):
        section("Registered tools", "Typed parameters, risk level and authorization requirement")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Tool": spec["name"],
                        "Risk": spec["risk"],
                        "Authorization": "required" if spec["requires_authorization"] else "automatic",
                        "Description": spec["description"],
                    }
                    for spec in tool_catalog()
                ]
            ),
            hide_index=True,
            width="stretch",
        )

    if probe_info and probe_info.get("available"):
        st.write("")
        with st.container(border=True):
            section("Live tool call", "Read-only audit retrieval executed through the MCP session")
            if st.button("Run retrieve_audit_logs via MCP", key="audit_run_tool"):
                gateway = ToolGateway(ctx.config, prefer_mcp=True)
                envelope = gateway.call(
                    "retrieve_audit_logs",
                    {"limit": 5, "dataset_id": store.dataset_id},
                    dataset_id=store.dataset_id,
                )
                st.markdown(
                    f'{badge("executed via " + str(envelope.get("meta", {}).get("mode")), "info")} '
                    f'{badge("ok" if envelope.get("ok") else "error", "positive" if envelope.get("ok") else "critical")}',
                    unsafe_allow_html=True,
                )
                st.json(envelope, expanded=False)

    st.write("")
    with st.container(border=True):
        section("Recent tool executions", "Every call is recorded with mode, risk and latency")
        rows = read_audit_logs(limit=40, event_type="tool_call", dataset_id=store.dataset_id, config=ctx.config)
        if rows:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Time": row.get("timestamp", "")[:19].replace("T", " "),
                            "Tool": row.get("actor", ""),
                            "Status": row.get("status", ""),
                            "Latency (ms)": row.get("latency_ms", ""),
                            "Error": (row.get("error") or "")[:70],
                        }
                        for row in rows
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
        else:
            note("No tool calls recorded yet.")

    with st.container(border=True):
        section("Security controls", "Enforced at the tool boundary")
        st.markdown(
            '<ul class="au-facts">'
            "<li>Paths are resolved with safe_project_path: absolute paths, traversal, encoded traversal, reserved "
            "device names and symlink escapes are rejected.</li>"
            "<li>High-risk tools require an explicit authorization flag; the report tool refuses to run without it.</li>"
            "<li>Tool inputs are validated against typed Pydantic models before execution.</li>"
            "<li>Failures are classified (validation, not-found, authorization, security, internal) and retried with "
            "bounded exponential backoff only when retryable.</li>"
            "<li>If the MCP session is unavailable, the approved in-process registry runs the identical implementation "
            "and the fallback is recorded in the audit trail.</li>"
            "</ul>",
            unsafe_allow_html=True,
        )


def render(ctx: UIContext) -> None:
    store = ctx.store
    if store is None:
        return

    page_header(
        "Audit & Monitoring",
        "Workflow traceability, tool execution, performance measured from real runs and the audit trail.",
        source_badges(ctx),
    )

    tabs = st.tabs(["Performance", "Workflow history", "Audit trail", "Tool layer"])
    with tabs[0]:
        _performance_tab(store, ctx)
    with tabs[1]:
        _workflows_tab(store, ctx)
    with tabs[2]:
        _trail_tab(store, ctx)
    with tabs[3]:
        _tools_tab(store, ctx)
