"""Reusable presentation components and Plotly chart builders."""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Sequence

import plotly.graph_objects as go
import streamlit as st

from core.formatting import compact_money, money, month_label, percent
from core.models import Alert

EMERALD = "#10b981"
EMERALD_LIGHT = "#34d399"
RED = "#ef4444"
RED_LIGHT = "#f87171"
AMBER = "#f59e0b"
AMBER_LIGHT = "#fbbf24"
SKY = "#38bdf8"
VIOLET = "#a78bfa"
SLATE = "#94a3b8"

PALETTE = [
    "#34d399", "#60a5fa", "#fbbf24", "#f472b6", "#a78bfa", "#22d3ee",
    "#fb923c", "#4ade80", "#f87171", "#c084fc", "#94a3b8", "#38bdf8",
]

PRIORITY_TONE = {"P1": "p1", "P2": "p2", "P3": "p3", "P4": "p4"}
RESOLUTION_LABELS = {
    "pending_review": ("Pending review", "warning"),
    "approved": ("Approved", "positive"),
    "edited": ("Approved with edits", "positive"),
    "held": ("On hold", "warning"),
    "info_requested": ("Info requested", "info"),
}


def esc(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def badge(text: str, tone: str = "neutral") -> str:
    return f'<span class="au-chip {esc(tone)}">{esc(text)}</span>'


def priority_chip(priority: str) -> str:
    return f'<span class="au-chip {PRIORITY_TONE.get(priority, "p4")}">{esc(priority)}</span>'


def status_chip(status: str) -> str:
    label, tone = RESOLUTION_LABELS.get(status, (status.replace("_", " ").title(), "neutral"))
    return badge(label, tone)


def severity_chip(severity: str) -> str:
    tone = {"critical": "critical", "high": "warning", "medium": "info", "low": "neutral"}.get(severity, "neutral")
    return badge(severity.upper(), tone)


def alert_status_chip(alert: Alert | dict[str, Any]) -> str:
    payload = alert if isinstance(alert, dict) else alert.model_dump(mode="json")
    if payload.get("resolved_at"):
        return badge("Resolved", "positive")
    if payload.get("acknowledged_at"):
        return badge("Acknowledged", "info")
    return badge("Open", "warning")


def sla_chip(alert: Alert | dict[str, Any]) -> str:
    state = payload_state(alert)
    label = {
        "met": "SLA met",
        "breached": "SLA breached",
        "approaching": "SLA approaching",
        "on_track": "On track",
        "unknown": "SLA unknown",
    }.get(state, "SLA unknown")
    tone = {
        "met": "positive",
        "breached": "critical",
        "approaching": "warning",
        "on_track": "info",
        "unknown": "neutral",
    }.get(state, "neutral")
    return badge(label, tone)


def payload_state(alert: Alert | dict[str, Any]) -> str:
    from core.alerts import sla_state

    payload = alert if isinstance(alert, dict) else alert.model_dump(mode="json")
    return str(sla_state(payload)["state"])


# --------------------------------------------------------------------------- #
# Layout blocks
# --------------------------------------------------------------------------- #


def page_header(title: str, subtitle: str = "", badges: Sequence[tuple[str, str]] | None = None) -> None:
    chips = "".join(badge(text, tone) for text, tone in (badges or []))
    st.markdown(
        f"""
        <div class="au-page-head">
          <div>
            <div class="au-page-title">{esc(title)}</div>
            <div class="au-page-sub">{esc(subtitle)}</div>
          </div>
          <div class="au-page-badges">{chips}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def section(title: str, caption: str = "") -> None:
    caption_html = f'<div class="au-section-caption">{esc(caption)}</div>' if caption else ""
    st.markdown(
        f'<div class="au-section"><div class="au-section-title">{esc(title)}</div>{caption_html}</div>',
        unsafe_allow_html=True,
    )


@dataclass
class KPI:
    label: str
    value: str
    sub: str = ""
    tone: str = "neutral"
    chip: tuple[str, str] | None = None


def _kpi_html(item: KPI) -> str:
    chip = badge(*item.chip) if item.chip else ""
    sub = f'<div class="au-kpi-sub">{esc(item.sub)}</div>' if item.sub else ""
    return f"""
    <div class="au-card tight">
      <div class="au-kpi">
        <div class="au-kpi-top"><span class="au-kpi-label">{esc(item.label)}</span>{chip}</div>
        <div class="au-kpi-value {esc(item.tone)}">{esc(item.value)}</div>
        {sub}
      </div>
    </div>
    """


def render_kpis(items: Sequence[KPI]) -> None:
    columns = st.columns(len(items), gap="medium")
    for column, item in zip(columns, items):
        with column:
            st.markdown(_kpi_html(item), unsafe_allow_html=True)


def panel(title: str, body_html: str, *, chip: str = "", tone: str = "") -> None:
    header = (
        f'<div class="au-kpi-top"><span class="au-kpi-label">{esc(title)}</span>{chip}</div>'
        if title
        else ""
    )
    st.markdown(
        f'<div class="au-card {esc(tone)}">{header}{body_html}</div>',
        unsafe_allow_html=True,
    )


def bullet_list(items: Iterable[str], tone: str = "positive") -> str:
    rows = "".join(
        f'<li><span class="au-dot" style="background:{_tone_color(tone)}"></span><span>{esc(item)}</span></li>'
        for item in items
        if item
    )
    return f'<ul class="au-list">{rows}</ul>' if rows else '<div class="au-note">Nothing to report.</div>'


def _tone_color(tone: str) -> str:
    return {
        "positive": EMERALD,
        "warning": AMBER,
        "critical": RED,
        "info": SKY,
        "neutral": SLATE,
    }.get(tone, SLATE)


def facts_list(facts: Iterable[str]) -> str:
    rows = "".join(f"<li>{esc(fact)}</li>" for fact in facts if fact)
    return f'<ul class="au-facts">{rows}</ul>' if rows else ""


def note(text: str) -> None:
    st.markdown(f'<div class="au-note">{esc(text)}</div>', unsafe_allow_html=True)


def muted(text: str) -> None:
    st.markdown(f'<div class="au-section-caption">{esc(text)}</div>', unsafe_allow_html=True)


def divider() -> None:
    st.markdown('<div class="au-divider"></div>', unsafe_allow_html=True)


def empty_state(title: str, body: str, hint: str = "") -> None:
    hint_html = f'<div class="au-note" style="margin-top:10px">{esc(hint)}</div>' if hint else ""
    st.markdown(
        f"""
        <div class="au-card" style="text-align:left; padding: 26px 24px;">
          <div style="font-size:1rem;font-weight:600;margin-bottom:6px">{esc(title)}</div>
          <div class="au-note" style="max-width:70ch">{esc(body)}</div>
          {hint_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def meter(value: float, tone: str = "positive") -> str:
    pct = max(0.0, min(1.0, value)) * 100
    css = {"positive": "", "warning": "warning", "critical": "critical"}.get(tone, "")
    return f'<div class="au-meter {css}"><span style="width:{pct:.1f}%"></span></div>'


# --------------------------------------------------------------------------- #
# Charts
# --------------------------------------------------------------------------- #


def base_figure(fig: go.Figure, *, height: int = 300, showlegend: bool = False) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=12, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, sans-serif", color="#cbd5e1", size=12),
        showlegend=showlegend,
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, x=0,
            bgcolor="rgba(0,0,0,0)", font=dict(size=11, color="#cbd5e1"),
        ),
        hoverlabel=dict(bgcolor="#0f172a", bordercolor="#1e293b", font=dict(color="#e2e8f0", size=12)),
        bargap=0.35,
    )
    fig.update_xaxes(showgrid=False, zeroline=False, linecolor="rgba(148,163,184,0.15)", tickfont=dict(size=11))
    fig.update_yaxes(gridcolor="rgba(148,163,184,0.10)", zeroline=False, tickfont=dict(size=11))
    return fig


def render_chart(fig: go.Figure, *, height: int = 300, key: str | None = None) -> None:
    st.plotly_chart(
        fig,
        width="stretch",
        config={"displayModeBar": False, "scrollZoom": False},
        key=key,
    )


def category_donut(items: list[dict[str, Any]], *, height: int = 300) -> go.Figure | None:
    items = [item for item in items if item.get("amount", 0) > 0]
    if not items:
        return None
    labels = [str(item["category"]) for item in items]
    values = [float(item["amount"]) for item in items]
    fig = go.Figure(
        go.Pie(
            labels=labels,
            values=values,
            hole=0.64,
            sort=False,
            marker=dict(colors=PALETTE[: len(labels)], line=dict(color="#0b1220", width=2)),
            textinfo="none",
            hovertemplate="<b>%{label}</b><br>%{value:,.0f}<br>%{percent}<extra></extra>",
        )
    )
    fig.add_annotation(
        text=f"<b>{html.escape(compact_money(sum(values)))}</b><br><span style='font-size:10px;color:#94a3b8'>TOTAL SPEND</span>",
        showarrow=False,
        font=dict(color="#e2e8f0", size=17),
    )
    return base_figure(fig, height=height)


def monthly_bars(monthly: list[dict[str, Any]], *, height: int = 300) -> go.Figure | None:
    if not monthly:
        return None
    labels = [month_label(row["month"]) for row in monthly][-8:]
    expenses = [row["expense"] for row in monthly][-8:]
    incomes = [row["income"] for row in monthly][-8:]
    fig = go.Figure()
    fig.add_bar(name="Income", x=labels, y=incomes, marker_color=EMERALD, marker_line_width=0)
    fig.add_bar(name="Expense", x=labels, y=expenses, marker_color="#3b4a63", marker_line_width=0)
    fig.update_layout(barmode="group")
    return base_figure(fig, height=height, showlegend=True)


def daily_spend_line(daily: list[dict[str, Any]], *, height: int = 260) -> go.Figure | None:
    if not daily:
        return None
    x = [datetime.strptime(row["date"], "%Y-%m-%d").strftime("%d %b") for row in daily]
    y = [row["amount"] for row in daily]
    fig = go.Figure(
        go.Scatter(
            x=x, y=y, mode="lines+markers",
            line=dict(color=EMERALD_LIGHT, width=2, shape="spline"),
            marker=dict(size=5, color=EMERALD_LIGHT),
            fill="tozeroy", fillcolor="rgba(16,185,129,0.10)",
            hovertemplate="%{x}<br>%{y:,.0f}<extra></extra>",
            name="Daily spend",
        )
    )
    return base_figure(fig, height=height)


def merchant_bars(merchants: list[dict[str, Any]], *, height: int = 300) -> go.Figure | None:
    if not merchants:
        return None
    rows = list(reversed(merchants[:8]))
    fig = go.Figure(
        go.Bar(
            x=[row["amount"] for row in rows],
            y=[row["merchant"][:26] for row in rows],
            orientation="h",
            marker_color=SKY,
            hovertemplate="%{y}<br>%{x:,.0f}<extra></extra>",
        )
    )
    return base_figure(fig, height=height)


def budget_bars(budgets: list[dict[str, Any]], *, height: int = 340) -> go.Figure | None:
    if not budgets:
        return None
    rows = sorted(
        [b for b in budgets if b.get("limit") or b.get("actual")],
        key=lambda b: b.get("actual", 0),
    )[-9:]
    labels = [b["category"] for b in rows]
    fig = go.Figure()
    fig.add_bar(
        name="Budget", y=labels, x=[b.get("limit", 0) for b in rows], orientation="h",
        marker_color="rgba(148,163,184,0.28)", marker_line_width=0,
        hovertemplate="%{y} budget<br>%{x:,.0f}<extra></extra>",
    )
    colors = []
    for b in rows:
        if b.get("status") == "over":
            colors.append(RED)
        elif b.get("status") in {"warning", "unbudgeted"}:
            colors.append(AMBER)
        else:
            colors.append(EMERALD)
    fig.add_bar(
        name="Actual", y=labels, x=[b.get("actual", 0) for b in rows], orientation="h",
        marker_color=colors, marker_line_width=0,
        hovertemplate="%{y} actual<br>%{x:,.0f}<extra></extra>",
    )
    fig.update_layout(barmode="group")
    return base_figure(fig, height=height, showlegend=True)


def health_gauge(score: float | None, *, height: int = 250) -> go.Figure:
    if score is None:
        fig = go.Figure()
        fig.add_annotation(
            text="<b>Not scored</b><br><span style='font-size:11px;color:#94a3b8'>required inputs missing</span>",
            showarrow=False, font=dict(color="#94a3b8", size=15),
        )
        return base_figure(fig, height=height)
    color = EMERALD if score >= 70 else (AMBER if score >= 50 else RED)
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=score,
            number=dict(suffix="/100", font=dict(size=34, color="#f1f5f9")),
            gauge=dict(
                axis=dict(range=[0, 100], tickwidth=0, tickcolor="rgba(0,0,0,0)", tickfont=dict(color="#64748b", size=10)),
                bar=dict(color=color, thickness=0.32),
                bgcolor="rgba(148,163,184,0.10)",
                borderwidth=0,
                steps=[
                    dict(range=[0, 40], color="rgba(239,68,68,0.12)"),
                    dict(range=[40, 70], color="rgba(245,158,11,0.12)"),
                    dict(range=[70, 100], color="rgba(16,185,129,0.12)"),
                ],
                threshold=dict(line=dict(color="#e2e8f0", width=2), thickness=0.8, value=score),
            ),
        )
    )
    return base_figure(fig, height=height)


def health_history_line(history: list[dict[str, Any]], *, height: int = 240) -> go.Figure | None:
    points = [h for h in history if h.get("score") is not None]
    if len(points) < 2:
        return None
    x = [datetime.fromisoformat(h["computed_at"]).strftime("%d %b %H:%M") for h in points]
    y = [h["score"] for h in points]
    fig = go.Figure(
        go.Scatter(
            x=x, y=y, mode="lines+markers",
            line=dict(color=EMERALD_LIGHT, width=2),
            marker=dict(size=6, color=EMERALD_LIGHT),
            fill="tozeroy", fillcolor="rgba(16,185,129,0.08)",
            hovertemplate="%{x}<br>score %{y:.1f}<extra></extra>",
        )
    )
    fig.update_yaxes(range=[0, 100])
    return base_figure(fig, height=height)


def forecast_path_chart(
    daily: list[dict[str, Any]],
    *,
    projected_expense: float | None,
    days_in_month: int,
    days_elapsed: int,
    height: int = 300,
) -> go.Figure | None:
    if not daily:
        return None
    points = sorted(daily, key=lambda row: row["date"])
    xs = [int(row["date"][-2:]) for row in points]
    cumulative: list[float] = []
    running = 0.0
    for row in points:
        running += float(row["amount"])
        cumulative.append(round(running, 2))

    fig = go.Figure()
    fig.add_scatter(
        x=xs, y=cumulative, mode="lines+markers", name="Actual (cumulative)",
        line=dict(color=EMERALD_LIGHT, width=2.4),
        marker=dict(size=4, color=EMERALD_LIGHT),
        fill="tozeroy", fillcolor="rgba(16,185,129,0.07)",
        hovertemplate="Day %{x}<br>actual %{y:,.0f}<extra></extra>",
    )
    if projected_expense is not None and xs:
        last_x, last_y = xs[-1], cumulative[-1]
        if days_in_month > last_x:
            fig.add_scatter(
                x=[last_x, days_in_month],
                y=[last_y, projected_expense],
                mode="lines", name="Projected path",
                line=dict(color=AMBER, width=2, dash="dash"),
                hovertemplate="Day %{x}<br>projected %{y:,.0f}<extra></extra>",
            )
            fig.add_scatter(
                x=[days_in_month], y=[projected_expense], mode="markers",
                marker=dict(size=9, color=AMBER, symbol="diamond"),
                name="Projected month total", showlegend=False,
                hovertemplate="Projected month total<br>%{y:,.0f}<extra></extra>",
            )
    fig.update_xaxes(title_text="Day of month")
    fig.update_yaxes(title_text="Cumulative spend")
    return base_figure(fig, height=height, showlegend=True)


def agent_latency_bars(agent_table: list[dict[str, Any]], *, height: int = 300) -> go.Figure | None:
    if not agent_table:
        return None
    rows = sorted(agent_table, key=lambda r: r["average_ms"])
    fig = go.Figure(
        go.Bar(
            x=[row["average_ms"] for row in rows],
            y=[row["agent"].replace("_agent", "").replace("_", " ") for row in rows],
            orientation="h",
            marker_color=VIOLET,
            hovertemplate="%{y}<br>%{x:.1f} ms<extra></extra>",
        )
    )
    fig.update_xaxes(title_text="Average latency (ms)")
    return base_figure(fig, height=height)


def severity_bars(by_severity: dict[str, int], *, height: int = 220) -> go.Figure | None:
    if not by_severity:
        return None
    order = ["critical", "high", "medium", "low"]
    labels = [key for key in order if key in by_severity]
    values = [by_severity[key] for key in labels]
    colors = {"critical": RED, "high": AMBER, "medium": SKY, "low": SLATE}
    fig = go.Figure(
        go.Bar(x=[label.title() for label in labels], y=values, marker_color=[colors[label] for label in labels],
               text=values, textposition="outside", hovertemplate="%{x}: %{y}<extra></extra>")
    )
    return base_figure(fig, height=height)


def mini_bar(values: list[float], labels: list[str], *, height: int = 220, color: str = EMERALD) -> go.Figure | None:
    if not values:
        return None
    fig = go.Figure(go.Bar(x=labels, y=values, marker_color=color, hovertemplate="%{x}: %{y:,.1f}<extra></extra>"))
    return base_figure(fig, height=height)


# --------------------------------------------------------------------------- #
# Formatting helpers used across views
# --------------------------------------------------------------------------- #


def health_tone(score: float | None) -> str:
    if score is None:
        return "neutral"
    return "positive" if score >= 70 else ("warning" if score >= 50 else "negative")


def ratio_tone(ratio: float | None, *, good_high: bool = True) -> str:
    if ratio is None:
        return "neutral"
    if good_high:
        return "positive" if ratio >= 0.7 else ("warning" if ratio >= 0.4 else "negative")
    return "negative" if ratio >= 1 else ("warning" if ratio >= 0.8 else "positive")


def money_or_dash(value: float | None) -> str:
    return money(value) if value is not None else "Not provided"


def pct_or_dash(value: float | None) -> str:
    return percent(value) if value is not None else "—"
