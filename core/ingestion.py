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
        "reference number", "ref no", "ref", "utr", "utr no", "cheque no", "chq no", "id",
        "chq/ref no", "chq/ref.no.", "chq./ref.no.", "chq / ref no",
        "transaction reference", "txn reference", "reference details",
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
        "amount", "txn amount", "transaction amount", "value", "amt",
        "amount inr", "amount (inr)", "amount (rs)", "amount rs",
    ),
    "debit": (
        "debit", "withdrawal", "withdrawal amount", "debit amount", "withdrawal amt",
        "withdrawal amt.", "withdrawal (dr)", "debit (dr)", "dr amount", "paid out", "dr",
    ),
    "credit": (
        "credit", "deposit", "deposit amount", "credit amount", "deposit amt",
        "deposit amt.", "deposit (cr)", "credit (cr)", "cr amount", "paid in", "cr",
    ),
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

_NON_TX_PREFIXES = (
    "opening balance",
    "closing balance",
    "balance brought forward",
    "balance carried forward",
    "b/f",
    "c/f",
    "brought forward",
    "carried forward",
    "statement summary",
    "account summary",
    "page total",
    "sub total",
    "subtotal",
    "grand total",
    "transaction total",
    "end of statement",
    "*** end of statement",
    "generated on",
    "this is a computer generated",
)

_NON_TX_PHRASE_RE = re.compile(
    r"\b(?:opening\s+bal(?:ance)?|closing\s+bal(?:ance)?|balance\s+brought\s+forward|"
    r"balance\s+carried\s+forward|brought\s+forward|carried\s+forward|end\s+of\s+statement)\b",
    re.IGNORECASE,
)

_CREDIT_DESC_KEYWORDS = (
    "upi/cr", "upi cr", "neft cr", "neft-cr", "imps/cr", "imps cr",
    "salary", "refund", "credit", "deposit", "interest", "cashback",
)

_METADATA_HEADER_LINE_RE = re.compile(
    r"^(?:account\s*(?:holder|name|no|number|type)|a/c\s*no|ifsc|micr|branch|cust(?:omer)?\s*id|"
    r"statement\s*(?:of\s+account|period|date)|period\s*:|generated\s+on|page\s+\d+|nominee|ckyc|"
    r"registered\s+office|gstin|pan\s*:|currency\s*:)",
    re.IGNORECASE,
)


def _is_non_transaction_text(text: str) -> bool:
    """Return True if text identifies a balance summary, total, footer, or disclaimer row."""
    norm = re.sub(r"\s+", " ", str(text or "").strip().casefold())
    if not norm:
        return False
    if norm == "total" or re.match(r"^total(?:\s*[:\-]|\s+(?:of|amount|debits?|credits?|withdrawals?|deposits?)\b)", norm):
        return True
    for marker in _NON_TX_PREFIXES:
        if marker in {"b/f", "c/f"}:
            if re.match(rf"^{re.escape(marker)}(?:\b|$)", norm):
                return True
        elif norm == marker or norm.startswith(marker):
            return True
    if _NON_TX_PHRASE_RE.search(norm):
        return True
    return False


def extract_statement_metadata(text: str) -> dict[str, Any]:
    """Extract account and balance metadata from raw statement text."""
    metadata: dict[str, Any] = {}
    if not text:
        return metadata

    open_match = re.search(
        r"(?:opening\s+bal(?:ance)?|balance\s+brought\s+forward)\s*[:\-]?\s*(?:inr|rs\.?|₹|\$)?\s*([\d,]+\.\d{2})",
        text,
        re.IGNORECASE,
    )
    if open_match:
        try:
            metadata["opening_balance"] = float(open_match.group(1).replace(",", ""))
        except ValueError:
            pass

    close_match = re.search(
        r"(?:closing\s+bal(?:ance)?|balance\s+carried\s+forward)\s*[:\-]?\s*(?:inr|rs\.?|₹|\$)?\s*([\d,]+\.\d{2})",
        text,
        re.IGNORECASE,
    )
    if close_match:
        try:
            metadata["closing_balance"] = float(close_match.group(1).replace(",", ""))
        except ValueError:
            pass

    acct_match = re.search(
        r"(?:account|a/c)\s*(?:no\.?|number|#)\s*[:\-]?\s*[xX*0-9\-]*(\d{4})\b",
        text,
        re.IGNORECASE,
    )
    if acct_match:
        metadata["account_number"] = f"••••{acct_match.group(1)}"

    period_match = re.search(
        r"(?:statement\s+period|period|from)\s*[:\-]?\s*(\d{1,2}[/\-\s][A-Za-z0-9]{2,9}[/\-\s]\d{2,4}\s*(?:to|\-)\s*\d{1,2}[/\-\s][A-Za-z0-9]{2,9}[/\-\s]\d{2,4})",
        text,
        re.IGNORECASE,
    )
    if period_match:
        metadata["statement_period"] = re.sub(r"\s+", " ", period_match.group(1).strip())

    return metadata


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


def _grid_to_rows(
    grid: list[list[str]],
    *,
    start_ref: int = 1,
    fallback_mapping: dict[int, str] | None = None,
) -> tuple[list[dict[str, Any]], int, list[str], dict[int, str], int]:
    """Locate the header row of a grid and return normalized raw rows."""
    warnings: list[str] = []
    header_index = -1
    mapping: dict[int, str] = {}
    best_score = 0

    for index, row in enumerate(grid[:50]):
        score, candidate = _score_header_row(row)
        if score > best_score:
            best_score, header_index, mapping = score, index, candidate

    used_fallback = False
    if header_index < 0 or best_score < 2:
        max_cols = max((len(r) for r in grid), default=0)
        if fallback_mapping and max_cols > max(fallback_mapping.keys(), default=-1):
            mapping = dict(fallback_mapping)
            header_index = -1
            used_fallback = True
        else:
            raise IngestionError(
                "Could not locate a transaction header row. Expected columns such as date, amount and merchant or description."
            )

    present = set(mapping.values())
    for group in REQUIRED_GROUPS:
        if not present.intersection(group):
            raise IngestionError(
                "Statement is missing a required column group: " + " / ".join(group)
            )

    date_col = next((idx for idx, col in mapping.items() if col == "transaction_date"), None)
    data_slice = grid if used_fallback else grid[header_index + 1:]

    rows: list[dict[str, Any]] = []
    row_ref = start_ref
    filtered_count = 0

    for raw_row in data_slice:
        if not any(_cell(cell) for cell in raw_row):
            continue

        # Skip repeated header rows inside multi-page / continuation tables
        if date_col is not None and date_col < len(raw_row):
            date_norm = _normalize_header(raw_row[date_col])
            if date_norm in COLUMN_ALIASES["transaction_date"] and _score_header_row(raw_row)[0] >= 2:
                filtered_count += 1
                continue

        record: dict[str, Any] = {}
        for index, canonical in mapping.items():
            if index < len(raw_row):
                record[canonical] = _cell(raw_row[index])

        date_val = str(record.get("transaction_date", "")).strip()
        merch_val = str(record.get("merchant", "")).strip()
        desc_val = str(record.get("description", "")).strip()
        joined_row = " ".join(_cell(c) for c in raw_row if _cell(c))

        if (
            _is_non_transaction_text(merch_val)
            or _is_non_transaction_text(desc_val)
            or _is_non_transaction_text(date_val)
            or (not date_val and _is_non_transaction_text(joined_row))
        ):
            filtered_count += 1
            continue

        has_money = any(
            bool(str(record.get(k, "")).strip())
            for k in ("amount", "debit", "credit")
        )
        # Stitch multi-line wrapped narrations onto the previous row
        if not date_val and not has_money and (desc_val or merch_val):
            if rows:
                prev = rows[-1]
                prev_desc = str(prev.get("description", "")).strip()
                prev_merch = str(prev.get("merchant", "")).strip()
                if desc_val:
                    new_desc = f"{prev_desc} {desc_val}".strip() if prev_desc else desc_val
                    prev["description"] = new_desc
                    if "merchant" not in mapping.values() and prev_merch == prev_desc:
                        prev["merchant"] = new_desc
                if merch_val:
                    prev["merchant"] = f"{prev_merch} {merch_val}".strip() if prev_merch else merch_val
            else:
                filtered_count += 1
            continue

        record["_row_ref"] = row_ref
        row_ref += 1
        rows.append(record)

    if not rows:
        warnings.append("Header row found but no data rows were extracted.")
    return rows, (0 if used_fallback else header_index), warnings, mapping, filtered_count


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


_DATE_TOKEN = (
    r"(?:\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}|"
    r"\d{4}[/\-.]\d{1,2}[/\-.]\d{1,2}|"
    r"\d{1,2}[\s\-][A-Za-z]{3,9}[\s\-]\d{2,4})"
)
_DATE_CELL_RE = re.compile(rf"^{_DATE_TOKEN}$")
_AMOUNT_CELL_RE = re.compile(r"^-?[₹$€£¥\s]*[\d,]+\.\d{2}\s*(?:Dr|Cr|DR|CR)?$")

_PDF_LINE_BALANCE_RE = re.compile(
    rf"^(?:\d{{1,4}}\s+)?(?P<date>{_DATE_TOKEN})\s+"
    rf"(?:{_DATE_TOKEN}\s+)?"
    rf"(?P<desc>.+?)\s+"
    rf"(?P<amount>-?[\d,]+\.\d{{2}})\s*(?P<type>Dr|Cr|DR|CR)?\s+"
    rf"(?P<balance>-?[\d,]+\.\d{{2}})\s*(?:Dr|Cr|DR|CR)?$"
)
_PDF_LINE_AMOUNT_RE = re.compile(
    rf"^(?:\d{{1,4}}\s+)?(?P<date>{_DATE_TOKEN})\s+"
    rf"(?:{_DATE_TOKEN}\s+)?"
    rf"(?P<desc>.+?)\s+"
    rf"(?P<amount>-?[\d,]+\.\d{{2}})\s*(?P<type>Dr|Cr|DR|CR)?$"
)


def _is_pdf_header_line(line: str) -> bool:
    norm = line.casefold()
    has_date_kw = bool(re.search(r"\bdate\b", norm))
    has_other_kw = bool(re.search(
        r"\b(?:narration|particulars|description|details|withdrawal|deposit|debit|credit|amount|balance|chq|ref)\b",
        norm,
    ))
    return has_date_kw and has_other_kw


def _is_transaction_grid(grid: list[list[str]], last_tx_width: int | None) -> tuple[bool, int | None]:
    """Return True if a PDF table grid is a transaction table (has tx headers or matches previous tx table)."""
    for row in grid[:50]:
        score, candidate = _score_header_row(row)
        if score >= 2:
            present = set(candidate.values())
            if all(present.intersection(group) for group in REQUIRED_GROUPS):
                width = max((len(r) for r in grid if r), default=len(row))
                return True, width

    if last_tx_width is not None:
        max_cols = max((len(r) for r in grid if r), default=0)
        if max_cols >= 3 and abs(max_cols - last_tx_width) <= 1:
            has_date_and_amt = False
            for row in grid[:5]:
                cells = [_cell(c) for c in row if _cell(c)]
                if any(_DATE_CELL_RE.match(c) for c in cells) and any(_AMOUNT_CELL_RE.match(c) for c in cells):
                    has_date_and_amt = True
                    break
            if has_date_and_amt:
                return True, last_tx_width

    return False, None


def _grids_from_pdf(data: bytes) -> tuple[list[list[list[str]]], list[str], bool, dict[str, Any], int]:
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise IngestionError("PDF support requires the pdfplumber package.") from exc

    grids: list[list[list[str]]] = []
    notes: list[str] = []
    incomplete = False
    text_filtered_total = 0

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        if not pdf.pages:
            raise IngestionError("The PDF has no pages.")

        page_data: list[tuple[int, str, list[Any]]] = []
        for page_number, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            tables = page.extract_tables() or []
            page_data.append((page_number, text, tables))

        full_text = "\n".join(text for _, text, _ in page_data)
        metadata = extract_statement_metadata(full_text)
        metadata["pages_parsed"] = len(pdf.pages)

        prev_balance: float | None = metadata.get("opening_balance")
        last_tx_width: int | None = None

        for page_number, text, tables in page_data:
            page_tx_grids: list[list[list[str]]] = []
            for table in tables:
                grid = [[_cell(cell) for cell in row] for row in table if row and any(_cell(c) for c in row)]
                if not grid:
                    continue
                is_tx, tx_width = _is_transaction_grid(grid, last_tx_width)
                if is_tx:
                    page_tx_grids.append(grid)
                    if tx_width is not None:
                        last_tx_width = tx_width

            if page_tx_grids:
                grids.extend(page_tx_grids)
            else:
                if not tables:
                    notes.append(f"Page {page_number}: no tabular structure detected; parsing statement text.")
                else:
                    notes.append(f"Page {page_number}: skipped non-transaction summary table(s); parsing statement text.")
                lines, text_filtered, prev_balance = _rows_from_pdf_text(text, opening_balance=prev_balance)
                text_filtered_total += text_filtered
                if lines:
                    incomplete = True
                    notes.append(
                        f"Page {page_number}: recovered {len(lines) - 1} transaction rows from statement text."
                    )
                    grids.append(lines)

    if not grids:
        raise IngestionError(
            "No transaction table could be extracted from this PDF. Export the statement as CSV or Excel, or upload a text-based PDF."
        )
    return grids, notes, incomplete, metadata, text_filtered_total


def _rows_from_pdf_text(
    text: str,
    opening_balance: float | None = None,
) -> tuple[list[list[str]], int, float | None]:
    """Recover transaction rows from text-only / borderless PDF statements, skipping metadata and summaries."""
    rows: list[list[str]] = [["Date", "Description", "Amount", "Type"]]
    filtered_count = 0
    prev_balance = opening_balance
    can_stitch = False

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue

        match = _PDF_LINE_BALANCE_RE.match(stripped)
        balance_val: float | None = None
        if match:
            bal_str = match.group("balance")
            try:
                balance_val = float(bal_str.replace(",", ""))
            except ValueError:
                balance_val = None
        else:
            match = _PDF_LINE_AMOUNT_RE.match(stripped)

        if match:
            tx_date = match.group("date").strip()
            desc = match.group("desc").strip()
            amt_str = match.group("amount").strip()
            explicit_type = (match.group("type") or "").strip()

            try:
                amt_abs = abs(float(amt_str.replace(",", "")))
            except ValueError:
                amt_abs = 0.0

            if _is_non_transaction_text(desc) or (desc.casefold().startswith("to ") and "balance" in desc.casefold()):
                filtered_count += 1
                can_stitch = False
                if prev_balance is None and (
                    "opening" in desc.casefold() or "brought forward" in desc.casefold()
                ):
                    prev_balance = balance_val if balance_val is not None else amt_abs
                continue

            if explicit_type:
                tx_type = "Cr" if explicit_type.casefold() == "cr" else "Dr"
            elif balance_val is not None and prev_balance is not None:
                diff = balance_val - prev_balance
                if diff > 0 and abs(diff - amt_abs) <= 0.05:
                    tx_type = "Cr"
                elif diff < 0 and abs(-diff - amt_abs) <= 0.05:
                    tx_type = "Dr"
                else:
                    desc_cf = desc.casefold()
                    tx_type = "Cr" if any(kw in desc_cf for kw in _CREDIT_DESC_KEYWORDS) else "Dr"
            else:
                desc_cf = desc.casefold()
                tx_type = "Cr" if any(kw in desc_cf for kw in _CREDIT_DESC_KEYWORDS) else "Dr"

            if balance_val is not None:
                prev_balance = balance_val

            rows.append([tx_date, desc, amt_str, tx_type])
            can_stitch = True
            continue

        # Non-dated line: filter headers/footers/summaries or stitch continuation text
        if _is_non_transaction_text(stripped):
            filtered_count += 1
            can_stitch = False
            continue
        if (
            _METADATA_HEADER_LINE_RE.match(stripped)
            or _is_pdf_header_line(stripped)
            or re.fullmatch(r"[\*\-_=\s]+", stripped)
        ):
            can_stitch = False
            continue

        if can_stitch and len(rows) > 1:
            rows[-1][1] = f"{rows[-1][1]} {stripped}".strip()

    return (rows if len(rows) > 1 else []), filtered_count, prev_balance


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
    metadata: dict[str, Any] = {}
    filtered_total = 0
    try:
        if suffix == ".csv":
            grids = _grids_from_csv(data)
        elif suffix in {".xlsx", ".xls"}:
            grids = _grids_from_excel(data)
        else:
            grids, pdf_notes, incomplete, metadata, text_filtered_total = _grids_from_pdf(data)
            notes.extend(pdf_notes)
            filtered_total += text_filtered_total
            outcome.extraction_complete = not incomplete
            outcome.requires_manual_review = incomplete
    except IngestionError:
        raise
    except Exception as exc:  # unexpected parser failure
        raise IngestionError(f"Unable to parse the file: {exc}") from exc

    all_rows: list[dict[str, Any]] = []
    row_ref = 1
    last_mapping: dict[int, str] | None = None
    for index, grid in enumerate(grids):
        try:
            rows, header_index, grid_warnings, grid_mapping, grid_filtered = _grid_to_rows(
                grid,
                start_ref=row_ref,
                fallback_mapping=last_mapping,
            )
            last_mapping = grid_mapping
            filtered_total += grid_filtered
        except IngestionError as exc:
            if len(grids) == 1:
                raise
            warnings.append(f"Table {index + 1} skipped: {exc}")
            outcome.requires_manual_review = True
            continue
        if header_index > 0:
            notes.append(f"Table {index + 1}: skipped {header_index} preamble row(s) before the header.")
            if not metadata and suffix != ".pdf":
                preamble_text = "\n".join(" ".join(c for c in r if c) for r in grid[:header_index])
                metadata.update(extract_statement_metadata(preamble_text))
        warnings.extend(grid_warnings)
        row_ref += len(rows)
        all_rows.extend(rows)

    if filtered_total > 0:
        notes.append(f"Filtered {filtered_total} non-transaction summary/header/footer row(s).")
    metadata["non_transaction_rows_filtered"] = filtered_total

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
    outcome.metadata = metadata
    if outcome.requires_manual_review:
        outcome.notes.append(
            "Extraction was incomplete: verify the parsed rows against the source document before relying on results."
        )
    return outcome


def parse_statement_path(file_path: str | Path, *, config: AppConfig | None = None) -> IngestionOutcome:
    """Parse a statement from disk. Callers must pass a pre-confined path."""
    path = Path(file_path)
    return parse_statement(path.read_bytes(), path.name, config=config)
