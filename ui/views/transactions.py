"""Transactions: ingestion, validation, search, corrections and quarantine."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core.constants import EXPENSE_CATEGORIES
from core.formatting import money, percent
from core.services import set_category
from core.spending import available_months
from ui import data
from ui.actions import upload_flow
from ui.components import (
    KPI,
    badge,
    empty_state,
    esc,
    note,
    page_header,
    render_kpis,
    section,
)
from ui.context import UIContext, flash, source_badges


def _validated_tab(store, ctx: UIContext) -> None:
    transactions = data.transactions(store)
    if not transactions:
        note("No validated transactions yet. Upload a statement above.")
        return

    months = ["All months"] + available_months(transactions)[::-1]
    categories = ["All categories"] + sorted({t.category for t in transactions})
    col1, col2, col3, col4 = st.columns([2, 1.1, 1, 1.3])
    query = col1.text_input(
        "Search", placeholder="Merchant or description…", key="tx_search", label_visibility="collapsed"
    )
    month = col2.selectbox("Month", months, key="tx_month", label_visibility="collapsed")
    tx_type = col3.selectbox(
        "Type", ["All types", "Expense", "Income"], key="tx_type", label_visibility="collapsed"
    )
    category = col4.selectbox("Category", categories, key="tx_cat", label_visibility="collapsed")

    duplicates_only = st.checkbox("Show possible duplicates only", value=False, key="tx_dupes_only")

    filtered = []
    needle = (query or "").strip().casefold()
    for transaction in transactions:
        if month != "All months" and transaction.month_key != month:
            continue
        if tx_type != "All types" and transaction.transaction_type != tx_type.casefold():
            continue
        if category != "All categories" and transaction.category != category:
            continue
        if duplicates_only and not transaction.is_duplicate_suspect:
            continue
        if needle and needle not in f"{transaction.merchant} {transaction.description}".casefold():
            continue
        filtered.append(transaction)

    filtered.sort(key=lambda t: (t.transaction_date, t.transaction_id), reverse=True)
    st.caption(f"{len(filtered)} of {len(transactions)} validated transactions")

    if not filtered:
        note("No transactions match the current filters.")
    else:
        frame = pd.DataFrame(
            [
                {
                    "Date": t.transaction_date.isoformat(),
                    "Merchant": t.merchant,
                    "Description": t.description[:70],
                    "Category": t.category,
                    "Type": t.transaction_type.title(),
                    "Amount": float(t.amount),
                    "Category source": t.category_source,
                    "Flags": ", ".join(
                        (["possible duplicate"] if t.is_duplicate_suspect else [])
                        + (["sanitised text"] if t.sanitization_flags else [])
                    ),
                }
                for t in filtered
            ]
        )
        st.dataframe(
            frame,
            hide_index=True,
            width="stretch",
            height=min(560, 42 * len(frame) + 40),
            column_config={"Amount": st.column_config.NumberColumn(format="%.2f")},
        )

    with st.expander("Correct a transaction category", expanded=False):
        st.caption(
            "Rules take precedence over AI for all rows. A correction is stored as an override and can be "
            "learned for the merchant, so future statements classify consistently."
        )
        options = {
            f"{t.transaction_date.isoformat()} · {t.merchant[:40]} · {t.amount:,.0f} · {t.category}": t
            for t in filtered[:250]
        }
        if not options:
            note("Nothing to edit in the current selection.")
        else:
            selection = st.selectbox("Transaction", list(options.keys()), key="tx_edit_select")
            target = options[selection]
            editable = list(EXPENSE_CATEGORIES) + (["Income"] if target.transaction_type == "income" else [])
            new_category = st.selectbox(
                "Category",
                editable,
                index=editable.index(target.category) if target.category in editable else 0,
                key="tx_edit_category",
            )
            learn = st.checkbox("Remember this merchant for future statements", value=True, key="tx_learn")
            if st.button("Save category", type="primary", key="tx_save_category"):
                try:
                    result = set_category(store, target.transaction_id, new_category, learn_merchant=learn)
                except Exception as exc:
                    flash("error", f"Could not update the category: {exc}")
                    st.rerun()
                    return
                message = f"Updated {target.merchant[:30]} to {result['category']}."
                if result.get("learned_merchant"):
                    message += f" Merchant rule saved for “{result['learned_merchant']}”."
                flash("success", message + " Re-run the analysis to refresh aggregates.")
                st.rerun()


def _uploaded_tab(store, ctx: UIContext) -> None:
    meta = data.ingestion_meta(store)
    staged = data.staging_rows(store)
    transactions = data.transactions(store)
    rejected = data.rejected(store)
    validation = data.validation(store)

    if not meta and not transactions:
        note("No statement has been uploaded to this dataset yet.")
        return

    st.markdown(
        f"""
        <div class="au-card tight">
          <div class="au-kpi-top"><span class="au-kpi-label">Latest extraction</span>
          {badge("Requires manual review", "warning") if meta.get("requires_manual_review") else badge("Complete", "positive")}</div>
          <div style="font-weight:600;margin-top:6px">{esc(meta.get('file_name') or '—')}</div>
          <div class="au-note" style="margin-top:4px">
            {esc(meta.get('file_type', ''))} · {meta.get('row_count', 0)} rows extracted
            · staged {esc(meta.get('staged_at', '—'))}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    for entry in meta.get("notes", []) or []:
        st.info(entry)
    for entry in meta.get("warnings", []) or []:
        st.warning(entry)

    if validation:
        st.caption(
            f"Last validation: {validation.get('validated', 0)} accepted · "
            f"{validation.get('rejected', 0)} quarantined · "
            f"{validation.get('duplicate_suspects', 0)} duplicate suspects · "
            f"source “{validation.get('source_file', '—')}”"
        )

    st.markdown("")
    section("Staged records", "Parsed but not yet validated" if staged else "Nothing is awaiting validation")
    if staged:
        frame = pd.DataFrame(
            [
                {
                    "Row": row.get("_row_ref"),
                    "Date": row.get("transaction_date", ""),
                    "Merchant": str(row.get("merchant", ""))[:50],
                    "Amount": str(row.get("amount", "")),
                    "Type": row.get("transaction_type", ""),
                }
                for row in staged[:300]
            ]
        )
        st.dataframe(frame, hide_index=True, width="stretch")
        st.caption("Staged rows are validated with the next analysis run. Invalid rows are quarantined, never dropped.")
    else:
        note(
            f"{len(transactions)} validated record(s) are stored for this dataset. "
            f"{len(rejected)} record(s) were quarantined."
        )


def _rejected_tab(store) -> None:
    rejected = data.rejected(store)
    page_path = store.path("rejected")
    section(
        "Rejected records",
        "Every malformed row is preserved with its reason and timestamp",
    )
    if not rejected:
        note("No records have been rejected for this dataset.")
        return

    st.caption(f"{len(rejected)} record(s) in {page_path.name}")
    frame = pd.DataFrame(
        [
            {
                "Timestamp": row.get("timestamp", ""),
                "Row": row.get("row_ref", ""),
                "Reasons": row.get("reasons", ""),
                "Source": row.get("source_file", ""),
                "Raw row": (row.get("raw") or "")[:160],
            }
            for row in rejected
        ]
    )
    st.dataframe(frame, hide_index=True, width="stretch", height=min(480, 46 * len(frame) + 40))

    if page_path.exists():
        st.download_button(
            "Download rejected_records.csv",
            data=page_path.read_bytes(),
            file_name=f"{store.dataset_id}-rejected_records.csv",
            mime="text/csv",
        )

    reasons: dict[str, int] = {}
    for row in rejected:
        for reason in str(row.get("reasons", "")).split(";"):
            key = reason.strip().split("'")[0].strip()
            if key:
                reasons[key] = reasons.get(key, 0) + 1
    if reasons:
        st.markdown("")
        section("Rejection reasons", "Deterministic validation rules")
        st.dataframe(
            pd.DataFrame(sorted(reasons.items(), key=lambda kv: kv[1], reverse=True), columns=["Reason", "Count"]),
            hide_index=True,
            width="stretch",
        )


def render(ctx: UIContext) -> None:
    store = ctx.store
    if store is None:
        return

    transactions = data.transactions(store)
    rejected = data.rejected(store)
    meta = data.ingestion_meta(store)
    validation = data.validation(store)
    staged = data.staging_rows(store)

    page_header(
        "Transactions",
        "Ingest statements, inspect every validated record and audit what was quarantined.",
        source_badges(ctx),
    )

    uploaded_rows = int(meta.get("row_count", 0) or 0)
    rate = validation.get("validation_rate")
    render_kpis(
        [
            KPI("Uploaded records", f"{uploaded_rows:,}" if uploaded_rows else "—", meta.get("file_name", "No file parsed yet")),
            KPI("Validated", f"{len(transactions):,}", "Stored and available for analysis", "positive"),
            KPI("Rejected", f"{len(rejected):,}", "Quarantined with reasons", "warning" if rejected else "neutral"),
            KPI("Staged", f"{len(staged):,}", "Awaiting validation" if staged else "Nothing pending"),
            KPI(
                "Validation rate",
                percent(rate) if rate is not None else "—",
                "Accepted ÷ rows received",
                "positive" if (rate or 0) >= 0.9 else "warning",
            ),
        ]
    )

    st.write("")
    if store.is_demo:
        st.info(
            "This dataset holds DEMONSTRATION DATA. Uploads are blocked here to keep synthetic records "
            "separate from your own statements — switch to an uploaded dataset to ingest a file."
        )
    else:
        with st.container(border=True):
            section("Upload a statement", "CSV, Excel or text-based PDF · parsed, validated and quarantined")
            uploaded = st.file_uploader(
                "Statement file",
                type=["csv", "xlsx", "xls", "pdf"],
                key="au_uploader",
                label_visibility="collapsed",
                help=f"Maximum {ctx.config.app.max_upload_mb} MB.",
            )
            columns = st.columns([1, 3])
            ingest = columns[0].button(
                "Ingest & analyse",
                type="primary",
                disabled=uploaded is None,
                use_container_width=True,
            )
            columns[1].markdown(
                '<div class="au-note">The file is stored inside the application data area, parsed into rows, '
                "validated against the schema, and only then used for calculations. PDF extraction that is "
                "incomplete is flagged for manual review.</div>",
                unsafe_allow_html=True,
            )
            if ingest and uploaded is not None:
                upload_flow(store, uploaded, config=ctx.config)

    st.write("")
    tab_validated, tab_uploaded, tab_rejected = st.tabs(
        ["Validated transactions", "Uploaded records", "Rejected records"]
    )
    with tab_validated:
        _validated_tab(store, ctx)
    with tab_uploaded:
        _uploaded_tab(store, ctx)
    with tab_rejected:
        _rejected_tab(store)
