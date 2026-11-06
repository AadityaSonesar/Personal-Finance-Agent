"""Persistent storage for datasets, profiles and analysis artifacts.

Every dataset is an isolated directory under ``data/datasets/<id>``. Uploaded
statements and demonstration data live in separate datasets so the two can never
be mixed in one analysis.
"""

from __future__ import annotations

import csv
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.config import AppConfig, ensure_data_dirs, get_config
from core.models import RejectedRecord, Transaction, UpcomingBill, utcnow

DATASET_KINDS = ("upload", "demo")

FILES: dict[str, str] = {
    "meta": "dataset.json",
    "transactions": "transactions.json",
    "transactions_csv": "transactions.csv",
    "staging": "staging.json",
    "rejected": "rejected_records.csv",
    "profile": "profile.json",
    "budgets": "budgets.json",
    "overrides": "category_overrides.json",
    "anomalies": "anomalies.json",
    "subscriptions": "subscriptions.json",
    "spending": "spending.json",
    "budgets_analysis": "budget_analysis.json",
    "health": "health.json",
    "health_history": "health_history.json",
    "cashflow": "cashflow.json",
    "recommendations": "recommendations.json",
    "alerts": "alerts.json",
    "approvals": "approvals.json",
    "workflows": "workflows.json",
    "report": "report.json",
    "validation": "validation_stats.json",
}

REJECTED_FIELDS = ["timestamp", "row_ref", "reasons", "source_file", "raw"]


class FinancialProfile(BaseModel):
    """User-supplied facts. ``None`` means "not provided", never zero."""

    model_config = ConfigDict(extra="ignore", protected_namespaces=())

    display_name: str = ""
    monthly_income: float | None = None
    emergency_fund: float | None = None
    current_balance: float | None = None
    upcoming_bills: list[UpcomingBill] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utcnow)

    @field_validator("monthly_income", "emergency_fund")
    @classmethod
    def _non_negative(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value < 0:
            raise ValueError("must not be negative")
        return float(value)


# --------------------------------------------------------------------------- #
# Low-level helpers
# --------------------------------------------------------------------------- #

_io_lock = threading.RLock()


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError):
        return default


def write_json_atomic(path: Path, payload: Any) -> Path:
    """Write JSON atomically with a short retry loop for Windows file locks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, indent=2, default=str, ensure_ascii=False)
    last_error: Exception | None = None
    with _io_lock:
        for attempt in range(5):
            tmp = path.with_name(f".{path.name}.{uuid4().hex[:8]}.tmp")
            try:
                tmp.write_text(body, encoding="utf-8")
                os.replace(tmp, path)
                return path
            except OSError as exc:  # transient lock (antivirus / concurrent reader)
                last_error = exc
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                time.sleep(0.05 * (attempt + 1))
    raise OSError(f"Unable to write {path.name}: {last_error}")


def append_csv_rows(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> int:
    """Append rows to a CSV, writing the header when the file is new."""
    written = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with _io_lock:
        needs_header = not path.exists() or path.stat().st_size == 0
        with path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            if needs_header:
                writer.writeheader()
            for row in rows:
                clean = {
                    key: (json.dumps(value, default=str) if isinstance(value, (dict, list)) else value)
                    for key, value in row.items()
                }
                writer.writerow(clean)
                written += 1
    return written


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open("r", newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))
    except OSError:
        return []


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug[:32] or "dataset"


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


# --------------------------------------------------------------------------- #
# Dataset store
# --------------------------------------------------------------------------- #


class DatasetStore:
    """Filesystem-backed store for one isolated financial dataset."""

    def __init__(self, dataset_id: str, config: AppConfig | None = None) -> None:
        self.config = config or get_config()
        self.dataset_id = dataset_id
        ensure_data_dirs(self.config)
        self.root = self.config.data_root / "datasets" / dataset_id
        self.root.mkdir(parents=True, exist_ok=True)
        self._meta: dict[str, Any] = read_json(self.path("meta"), {}) or {}

    # -- paths --------------------------------------------------------------
    def path(self, key: str) -> Path:
        filename = FILES.get(key, key)
        candidate = (self.root / filename).resolve()
        root = self.root.resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError("Dataset path escape blocked.")
        return candidate

    @property
    def exists(self) -> bool:
        return bool(self._meta)

    @property
    def meta(self) -> dict[str, Any]:
        return dict(self._meta)

    @property
    def kind(self) -> str:
        return str(self._meta.get("kind", "upload"))

    @property
    def is_demo(self) -> bool:
        return self.kind == "demo"

    @property
    def name(self) -> str:
        return str(self._meta.get("name", self.dataset_id))

    @property
    def data_source(self) -> str:
        return "demo" if self.is_demo else "upload"

    def create(self, name: str, kind: str = "upload", note: str = "") -> "DatasetStore":
        if kind not in DATASET_KINDS:
            raise ValueError(f"Unknown dataset kind: {kind}")
        self._meta = {
            "id": self.dataset_id,
            "name": name,
            "kind": kind,
            "note": note,
            "created_at": utcnow().isoformat(),
            "updated_at": utcnow().isoformat(),
        }
        self._save_meta()
        return self

    def touch(self) -> None:
        self._meta["updated_at"] = utcnow().isoformat()
        self._save_meta()

    def _save_meta(self) -> None:
        write_json_atomic(self.path("meta"), self._meta)

    # -- transactions -------------------------------------------------------
    def load_transactions(self) -> list[Transaction]:
        payload = read_json(self.path("transactions"), []) or []
        result: list[Transaction] = []
        for item in payload:
            try:
                result.append(Transaction.model_validate(item))
            except Exception:
                continue
        result.sort(key=lambda t: (t.transaction_date, t.transaction_id))
        return result

    def save_transactions(self, transactions: list[Transaction]) -> None:
        payload = [t.model_dump(mode="json") for t in transactions]
        write_json_atomic(self.path("transactions"), payload)
        self._write_transactions_csv(transactions)
        self._meta["transaction_count"] = len(transactions)
        self._meta["last_ingested_at"] = utcnow().isoformat()
        self._save_meta()

    def _write_transactions_csv(self, transactions: list[Transaction]) -> None:
        fields = [
            "transaction_id", "transaction_date", "merchant", "description",
            "amount", "transaction_type", "category", "category_source", "account", "source",
        ]
        path = self.path("transactions_csv")
        try:
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                for t in transactions:
                    writer.writerow(
                        {
                            "transaction_id": t.transaction_id,
                            "transaction_date": t.transaction_date.isoformat(),
                            "merchant": t.merchant,
                            "description": t.description,
                            "amount": f"{t.amount:.2f}",
                            "transaction_type": t.transaction_type,
                            "category": t.category,
                            "category_source": t.category_source,
                            "account": t.account,
                            "source": t.source,
                        }
                    )
        except OSError:
            pass

    def add_transactions(self, new_items: list[Transaction]) -> dict[str, int]:
        """Merge transactions, blocking both ID and content duplicates."""
        existing = self.load_transactions()
        ids = {t.transaction_id for t in existing}
        signatures = {t.duplicate_signature for t in existing}
        added = 0
        for item in new_items:
            if item.transaction_id in ids or item.duplicate_signature in signatures:
                continue
            existing.append(item)
            ids.add(item.transaction_id)
            signatures.add(item.duplicate_signature)
            added += 1
        existing.sort(key=lambda t: (t.transaction_date, t.transaction_id))
        self.save_transactions(existing)
        return {"added": added, "skipped": len(new_items) - added, "total": len(existing)}

    # -- staging ------------------------------------------------------------
    def save_staging(self, rows: list[dict[str, Any]], meta: dict[str, Any]) -> None:
        write_json_atomic(self.path("staging"), {"meta": meta, "rows": rows})

    def load_staging(self) -> dict[str, Any]:
        return read_json(self.path("staging"), {"meta": {}, "rows": []}) or {"meta": {}, "rows": []}

    # -- rejected records ----------------------------------------------------
    def append_rejected(self, records: list[RejectedRecord]) -> int:
        rows = []
        for record in records:
            rows.append(
                {
                    "timestamp": record.timestamp.astimezone(timezone.utc).isoformat(),
                    "row_ref": record.row_ref,
                    "reasons": "; ".join(record.reasons),
                    "source_file": record.source_file,
                    "raw": record.raw,
                }
            )
        return append_csv_rows(self.path("rejected"), REJECTED_FIELDS, rows)

    def load_rejected(self) -> list[dict[str, str]]:
        return read_csv_rows(self.path("rejected"))

    # -- profile ------------------------------------------------------------
    def load_profile(self) -> FinancialProfile:
        payload = read_json(self.path("profile"), {}) or {}
        try:
            return FinancialProfile.model_validate(payload)
        except Exception:
            return FinancialProfile()

    def save_profile(self, profile: FinancialProfile) -> None:
        profile.updated_at = utcnow()
        write_json_atomic(self.path("profile"), profile.model_dump(mode="json"))

    # -- budgets ------------------------------------------------------------
    def load_budget_overrides(self) -> dict[str, float]:
        payload = read_json(self.path("budgets"), {}) or {}
        result: dict[str, float] = {}
        for key, value in payload.items():
            try:
                result[str(key)] = max(0.0, float(value))
            except (TypeError, ValueError):
                continue
        return result

    def save_budget_overrides(self, budgets: dict[str, float]) -> None:
        write_json_atomic(self.path("budgets"), {k: float(v) for k, v in budgets.items()})

    def effective_budgets(self) -> dict[str, float]:
        """User budgets override configured defaults, category by category."""
        merged = {k: float(v) for k, v in self.config.financial.default_budgets.items()}
        merged.update(self.load_budget_overrides())
        return {k: v for k, v in merged.items() if v >= 0}

    # -- category overrides --------------------------------------------------
    def load_overrides(self) -> dict[str, dict[str, str]]:
        payload = read_json(self.path("overrides"), {}) or {}
        return {
            "transactions": dict(payload.get("transactions", {})),
            "merchants": dict(payload.get("merchants", {})),
        }

    def save_overrides(self, overrides: dict[str, dict[str, str]]) -> None:
        write_json_atomic(self.path("overrides"), overrides)

    def set_transaction_category(self, transaction_id: str, category: str) -> None:
        overrides = self.load_overrides()
        overrides["transactions"][transaction_id] = category
        self.save_overrides(overrides)

    def set_merchant_preference(self, merchant_key: str, category: str) -> None:
        overrides = self.load_overrides()
        overrides["merchants"][merchant_key] = category
        self.save_overrides(overrides)

    # -- analysis artifacts --------------------------------------------------
    def save_artifact(self, key: str, payload: Any) -> None:
        if key not in FILES:
            raise ValueError(f"Unknown artifact: {key}")
        write_json_atomic(self.path(key), payload)

    def load_artifact(self, key: str, default: Any = None) -> Any:
        return read_json(self.path(key), default)

    def append_health_history(self, entry: dict[str, Any]) -> None:
        history = self.load_artifact("health_history", []) or []
        history.append(entry)
        self.save_artifact("health_history", history[-240:])

    # -- workflows ----------------------------------------------------------
    def save_workflow(self, payload: dict[str, Any]) -> None:
        workflows = self.load_artifact("workflows", []) or []
        workflows.append(payload)
        self.save_artifact("workflows", workflows[-100:])

    def load_workflows(self) -> list[dict[str, Any]]:
        workflows = self.load_artifact("workflows", []) or []
        return list(reversed(workflows))

    # -- approvals ----------------------------------------------------------
    def save_approval(self, payload: dict[str, Any]) -> None:
        approvals = self.load_artifact("approvals", []) or []
        approvals.append(payload)
        self.save_artifact("approvals", approvals)

    def load_approvals(self) -> list[dict[str, Any]]:
        return self.load_artifact("approvals", []) or []

    def latest_decision(self, recommendation_id: str) -> dict[str, Any] | None:
        matches = [a for a in self.load_approvals() if a.get("recommendation_id") == recommendation_id]
        return matches[-1] if matches else None


# --------------------------------------------------------------------------- #
# Dataset registry
# --------------------------------------------------------------------------- #


def data_root(config: AppConfig | None = None) -> Path:
    config = config or get_config()
    return ensure_data_dirs(config)


def list_datasets(config: AppConfig | None = None) -> list[dict[str, Any]]:
    config = config or get_config()
    root = data_root(config) / "datasets"
    datasets: list[dict[str, Any]] = []
    for directory in sorted(root.glob("*")):
        if not directory.is_dir():
            continue
        meta = read_json(directory / FILES["meta"], None)
        if isinstance(meta, dict) and meta.get("id"):
            meta["transaction_count"] = meta.get("transaction_count", 0)
            datasets.append(meta)
    datasets.sort(key=lambda m: str(m.get("created_at", "")), reverse=True)
    return datasets


def create_dataset(
    name: str,
    *,
    kind: str = "upload",
    note: str = "",
    config: AppConfig | None = None,
) -> DatasetStore:
    config = config or get_config()
    if kind not in DATASET_KINDS:
        raise ValueError(f"Unknown dataset kind: {kind}")
    dataset_id = f"{_slug(name)}-{uuid4().hex[:6]}"
    store = DatasetStore(dataset_id, config=config)
    store.create(name=name, kind=kind, note=note)
    return store


def get_dataset(dataset_id: str, config: AppConfig | None = None) -> DatasetStore | None:
    if not dataset_id or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", dataset_id):
        return None
    store = DatasetStore(dataset_id, config=config)
    return store if store.exists else None


def active_dataset_id(config: AppConfig | None = None) -> str | None:
    payload = read_json(data_root(config) / "active_dataset.json", {}) or {}
    return payload.get("dataset_id")


def set_active_dataset(dataset_id: str | None, config: AppConfig | None = None) -> None:
    write_json_atomic(data_root(config) / "active_dataset.json", {"dataset_id": dataset_id})


def resolve_dataset(dataset_id: str | None = None, config: AppConfig | None = None) -> DatasetStore | None:
    """Resolve an explicit id, else the active dataset, else the newest one."""
    if dataset_id:
        store = get_dataset(dataset_id, config=config)
        if store:
            return store
    active = active_dataset_id(config)
    if active:
        store = get_dataset(active, config=config)
        if store:
            return store
    available = list_datasets(config)
    if available:
        return get_dataset(str(available[0]["id"]), config=config)
    return None


def dataset_choice_labels(datasets: list[dict[str, Any]]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for meta in datasets:
        kind = "DEMONSTRATION DATA" if meta.get("kind") == "demo" else "Uploaded"
        labels[str(meta["id"])] = f"{meta.get('name', meta['id'])} — {kind} ({meta.get('transaction_count', 0)} txn)"
    return labels


def notification_path(config: AppConfig | None = None) -> Path:
    config = config or get_config()
    path = data_root(config) / "notifications" / "deliveries.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
