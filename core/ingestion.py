"""Statement ingestion for CSV, Excel and PDF files.

Extraction is defensive: column layouts are discovered rather than assumed,
encoding and delimiter variants are handled, and incomplete extraction is
reported instead of being silently accepted.
"""

from __future__ import annotations

import io
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from core.config import AppConfig, get_config
from core.models import IngestionOutcome
from core.security import sanitize_filename, sanitize_text


class IngestionError(ValueError):
    """Raised when a statement cannot be parsed into transaction rows at all."""


COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "transaction_id": (
        "transaction_id", "transaction id", "txn_id", "txn id", "txnid", "reference",
        "reference number", "ref no", "ref", "utr", "cheque no", "chq no", "id",
    ),
    "transaction_date": (
        "transaction_date", "transaction date", "date", "txn date", "value date",
        "posting date", "posted date", "trans date",
    ),
    "merchant": (
        "merchant", "payee", "vendor", "beneficiary", "merchant name", "name",
        "payee name", "counterparty",
    ),
    "description": (
        "description", "narration", "details", "memo", "remarks", "particulars",
        "transaction details", "transaction description", "note",
    ),
    "amount": (
        "amount", "txn amount", "transaction amount", "value", "amount inr",
        "amount (inr)", "amount (rs)", "amount rs", "amt",
    ),
    "debit": ("debit", "withdrawal", "withdrawal amount", "debit amount", "withdrawal amt", "dr"),
    "credit": ("credit", "deposit", "deposit amount", "credit amount", "deposit amt", "cr"),
    "transaction_type": (
        "transaction_type", "transaction type", "type", "txn type", "dr/cr",
        "debit/credit", "dr cr", "direction",
    ),
    "category": ("category", "expense category", "tag", "classification"),
    "account": (
        "account", "account number", "account no", "account name", "card",
        "card number", "bank", "instrument", "source account",
    ),
}

REQUIRED_GROUPS: tuple[tuple[str, ...], ...] = (
    ("transaction_date",),
    ("amount", "debit", "credit"),
    ("merchant", "description"),
)

CURRENCY_NOISE = re.compile(r"[₹$€£¥\s]")


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if pd.isna(value):
            return ""
        if value.is_integer():
            return str(int(value))
        return repr(value)
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    return str(value).strip()


def _normalize_header(value: Any) -> str:
    text = _cell(value).casefold()
    text = re.sub(r"[_\-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def resolve_extension(file_name: str, config: AppConfig | None = None) -> str:
    config = config or get_config()
    suffix = Path(str(file_name)).suffix.casefold()
    allowed = {ext.casefold() for ext in config.security.allowed_extensions}
    if suffix not in allowed:
        raise IngestionError(
            f"Unsupported file type '{suffix or 'unknown'}'. Allowed types: "
            + ", ".join(sorted(allowed))
        )
    return suffix


def _read_bytes_with_fallbacks(data: bytes, *, sep: str | None) -> pd.DataFrame:
    errors: list[str] = []
    for encoding in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            return pd.read_csv(
                io.BytesIO(data),
                header=None,
                dtype=str,
                keep_default_na=False,
                sep=sep,
                engine="python",
                encoding=encoding,
                skip_blank_lines=True,
            )
        except (UnicodeError, pd.errors.ParserError, ValueError) as exc:
            errors.append(f"{encoding}: {exc}")
            continue
    raise IngestionError("Unable to decode the CSV file. " + " | ".join(errors[:2]))


def _frame_to_grid(frame: pd.DataFrame) -> list[list[str]]:
    grid: list[list[str]] = []
    for row in frame.itertuples(index=False, name=None):
        grid.append([_cell(value) for value in row])
    return grid


def _score_header_row(row: list[str]) -> tuple[int, dict[int, str]]:
    mapping: dict[int, str] = {}
    seen: set[str] = set()
    for index, raw in enumerate(row):
        header = _normalize_header(raw)
        if not header:
            continue
        for canonical, aliases in COLUMN_ALIASES.items():
            if canonical in seen:
                continue
            if header in aliases:
                mapping[index] = canonical
                seen.add(canonical)
                break
    return len(mapping), mapping


def _grid_to_rows(grid: list[list[str]], *, start_ref: int = 1) -> tuple[list[dict[str, Any]], int, list[str]]:
    """Locate the header row of a grid and return normalized raw rows."""
    warnings: list[str] = []
    header_index = -1
    mapping: dict[int, str] = {}
    best_score = 0

    for index, row in enumerate(grid[:30]):
        score, candidate = _score_header_row(row)
        if score > best_score:
            best_score, header_index, mapping = score, index, candidate

    if header_index < 0 or best_score < 2:
        raise IngestionError(
            "Could not locate a transaction header row. Expected columns such as date, amount and merchant or description."
        )

    present = set(mapping.values())
    for group in REQUIRED_GROUPS:
        if not present.intersection(group):
            raise IngestionError(
                "Statement is missing a required column group: " + " / ".join(group)
            )

    rows: list[dict[str, Any]] = []
    row_ref = start_ref
    for raw_row in grid[header_index + 1:]:
        if not any(_cell(cell) for cell in raw_row):
            continue
        record: dict[str, Any] = {}
        for index, canonical in mapping.items():
            if index < len(raw_row):
                record[canonical] = _cell(raw_row[index])
        record["_row_ref"] = row_ref
        row_ref += 1
        rows.append(record)

    if not rows:
        warnings.append("Header row found but no data rows were extracted.")
    return rows, header_index, warnings


def _grids_from_csv(data: bytes) -> list[list[list[str]]]:
    frame = _read_bytes_with_fallbacks(data, sep=None)
    if frame.empty:
        raise IngestionError("The CSV file is empty.")
    return [_frame_to_grid(frame)]


def _grids_from_excel(data: bytes) -> list[list[list[str]]]:
    try:
        sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, header=None, dtype=str)
    except Exception as exc:  # openpyxl / xlrd specific failures
        raise IngestionError(f"Unable to read the Excel workbook: {exc}") from exc
    grids: list[list[list[str]]] = []
    for _name, frame in sheets.items():
        if frame is None or frame.empty:
            continue
        grids.append(_frame_to_grid(frame.fillna("")))
    if not grids:
        raise IngestionError("The Excel workbook contains no readable sheets.")
    return grids


def _grids_from_pdf(data: bytes) -> tuple[list[list[list[str]]], list[str], bool]:
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise IngestionError("PDF support requires the pdfplumber package.") from exc

    grids: list[list[list[str]]] = []
    notes: list[str] = []
    incomplete = False

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        if not pdf.pages:
            raise IngestionError("The PDF has no pages.")
        for page_number, page in enumerate(pdf.pages, start=1):
            tables = page.extract_tables() or []
            if not tables:
                incomplete = True
                notes.append(f"Page {page_number}: no tabular structure detected.")
                text = page.extract_text() or ""
                lines = _rows_from_pdf_text(text)
                if lines:
                    notes.append(f"Page {page_number}: recovered {len(lines)} rows from raw text; manual review recommended.")
                    grids.append(lines)
                continue
            for table in tables:
                grid = [[_cell(cell) for cell in row] for row in table if row]
                if grid:
                    grids.append(grid)

    if not grids:
        raise IngestionError(
            "No transaction table could be extracted from this PDF. Export the statement as CSV or Excel, or upload a text-based PDF."
        )
    return grids, notes, incomplete


def _rows_from_pdf_text(text: str) -> list[list[str]]:
    """Best-effort recovery of text-only statements (flagged for review)."""
    pattern = re.compile(
        r"^(?P<date>\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})\s+"
        r"(?P<desc>.+?)\s+"
        r"(?P<amount>-?[\d,]+\.\d{2})\s*(?P<type>Dr|Cr|DR|CR)?$"
    )
    rows: list[list[str]] = [["Date", "Description", "Amount", "Type"]]
    for line in text.splitlines():
        match = pattern.match(line.strip())
        if match:
            rows.append(
                [
                    match.group("date"),
                    match.group("desc").strip(),
                    match.group("amount"),
                    (match.group("type") or "").strip(),
                ]
            )
    return rows if len(rows) > 1 else []


def _combine_debit_credit(record: dict[str, Any]) -> None:
    """Fold separate debit/credit columns into one amount + direction hint."""
    debit = CURRENCY_NOISE.sub("", str(record.get("debit", "")))
    credit = CURRENCY_NOISE.sub("", str(record.get("credit", "")))
    explicit = str(record.get("amount", "")).strip()

    def _present(value: str) -> bool:
        return bool(value) and value not in {"-", "--", "0", "0.0", "0.00", "0.000"}

    if _present(debit):
        record["amount"] = record.get("debit", "")
        record["transaction_type"] = "expense"
    elif _present(credit):
        record["amount"] = record.get("credit", "")
        record["transaction_type"] = "income"
    elif not explicit:
        record["amount"] = ""
    record.pop("debit", None)
    record.pop("credit", None)


def parse_statement(data: bytes, file_name: str, *, config: AppConfig | None = None) -> IngestionOutcome:
    """Parse raw statement bytes into canonical, unvalidated transaction rows."""
    config = config or get_config()
    safe_name = sanitize_filename(file_name)
    suffix = resolve_extension(safe_name, config)
    outcome = IngestionOutcome(
        file_name=safe_name,
        file_type=suffix.lstrip("."),
    )

    if not data:
        raise IngestionError("The uploaded file is empty.")
    if len(data) > config.app.max_upload_mb * 1024 * 1024:
        raise IngestionError(f"File exceeds the {config.app.max_upload_mb} MB upload limit.")

    notes: list[str] = []
    warnings: list[str] = []
    try:
        if suffix == ".csv":
            grids = _grids_from_csv(data)
        elif suffix in {".xlsx", ".xls"}:
            grids = _grids_from_excel(data)
        else:
            grids, pdf_notes, incomplete = _grids_from_pdf(data)
            notes.extend(pdf_notes)
            outcome.extraction_complete = not incomplete
            outcome.requires_manual_review = incomplete
    except IngestionError:
        raise
    except Exception as exc:  # unexpected parser failure
        raise IngestionError(f"Unable to parse the file: {exc}") from exc

    all_rows: list[dict[str, Any]] = []
    row_ref = 1
    for index, grid in enumerate(grids):
        try:
            rows, header_index, grid_warnings = _grid_to_rows(grid, start_ref=row_ref)
        except IngestionError as exc:
            if len(grids) == 1:
                raise
            warnings.append(f"Table {index + 1} skipped: {exc}")
            outcome.requires_manual_review = True
            continue
        if header_index > 0:
            notes.append(f"Table {index + 1}: skipped {header_index} preamble row(s) before the header.")
        warnings.extend(grid_warnings)
        row_ref += len(rows)
        all_rows.extend(rows)

    if not all_rows:
        raise IngestionError("No transaction rows were extracted from the statement.")

    if len(all_rows) > config.app.max_transactions:
        raise IngestionError(
            f"Statement exceeds the supported limit of {config.app.max_transactions:,} transactions."
        )

    for record in all_rows:
        _combine_debit_credit(record)
        merchant = _cell(record.get("merchant", ""))
        description = _cell(record.get("description", ""))
        if not merchant and description:
            merchant = description.split("  ")[0][:80]
        record["merchant"] = sanitize_text(merchant, max_length=config.security.max_field_length).text
        record["description"] = sanitize_text(description, max_length=config.security.max_description_length).text
        if not record.get("transaction_id"):
            record["transaction_id"] = ""

    outcome.rows = all_rows
    outcome.row_count = len(all_rows)
    outcome.notes = notes
    outcome.warnings = warnings
    if outcome.requires_manual_review:
        outcome.notes.append(
            "Extraction was incomplete: verify the parsed rows against the source document before relying on results."
        )
    return outcome


def parse_statement_path(file_path: str | Path, *, config: AppConfig | None = None) -> IngestionOutcome:
    """Parse a statement from disk. Callers must pass a pre-confined path."""
    path = Path(file_path)
    return parse_statement(path.read_bytes(), path.name, config=config)
