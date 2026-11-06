"""Typed configuration loading with environment overrides.

config.json is the single source of truth for financial rules. Secrets are read
only from the environment and never enter this module's public representation.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"


class _Section(BaseModel):
    model_config = ConfigDict(extra="allow", protected_namespaces=())


class AppMeta(_Section):
    name: str = "Aurelia"
    product: str = "Personal Financial Wellness"
    currency: str = "INR"
    currency_symbol: str = "₹"
    locale: str = "en-IN"
    rule_version: str = "1.0.0"
    data_dir: str = "data"
    max_upload_mb: int = 20
    max_transactions: int = 20000


class ScoreBand(_Section):
    min: float
    label: str
    summary: str = ""


class ScoringConfig(_Section):
    weights: dict[str, float]
    max_points: dict[str, float]
    target_savings_rate: float = 0.2
    emergency_months_target: int = 3
    budget_warning_ratio: float = 0.9
    score_bands: list[ScoreBand] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate(self) -> "ScoringConfig":
        total = round(sum(self.weights.values()), 6)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"financial.scoring.weights must sum to 1.0 (got {total})")
        for key in ("savings", "budget", "emergency"):
            if key not in self.weights:
                raise ValueError(f"financial.scoring.weights missing '{key}'")
            if key not in self.max_points:
                raise ValueError(f"financial.scoring.max_points missing '{key}'")
            if self.max_points[key] <= 0:
                raise ValueError(f"financial.scoring.max_points['{key}'] must be positive")
        if self.max_points["savings"] + self.max_points["budget"] + self.max_points["emergency"] != 100:
            raise ValueError("financial.scoring.max_points must total 100")
        if not 0 < self.target_savings_rate <= 1:
            raise ValueError("financial.scoring.target_savings_rate must be within (0, 1]")
        if self.emergency_months_target < 1:
            raise ValueError("financial.scoring.emergency_months_target must be >= 1")
        return self


class AnomalyConfig(_Section):
    large_transaction_multiplier: float = 3.0
    large_transaction_absolute: float = 15000
    large_transaction_min_history: int = 4
    spike_multiplier: float = 2.0
    spike_min_months: int = 2
    duplicate_window_days: int = 3
    repeated_transaction_threshold: int = 3
    repeated_transaction_window_days: int = 30
    merchant_activity_multiplier: float = 2.5
    unexpected_category_multiplier: float = 2.0
    critical_amount: float = 40000


class SubscriptionConfig(_Section):
    min_occurrences: int = 3
    min_occurrences_potential: int = 2
    interval_tolerance_ratio: float = 0.3
    amount_tolerance_ratio: float = 0.12
    lookback_days: int = 400
    needs_verification_after_days: int = 45
    minimum_confidence: float = 0.55
    periods_days: list[int] = Field(default_factory=lambda: [7, 14, 30, 90, 180, 365])


class ForecastConfig(_Section):
    history_months: int = 3
    run_rate_weight: float = 0.6
    historical_weight: float = 0.4
    deficit_alert_ratio: float = 0.0

    @model_validator(mode="after")
    def _weights(self) -> "ForecastConfig":
        if self.run_rate_weight + self.historical_weight <= 0:
            raise ValueError("forecast weights must be positive")
        return self


class AdvisoryConfig(_Section):
    max_recommendations: int = 10
    minimum_budget_overrun_ratio: float = 0.05
    subscription_review_amount: float = 500


class CategoryRules(_Section):
    merchant_map: dict[str, str] = Field(default_factory=dict)
    keyword_rules: list[dict[str, Any]] = Field(default_factory=list)


class FinancialConfig(_Section):
    scoring: ScoringConfig
    default_budgets: dict[str, float] = Field(default_factory=dict)
    anomaly: AnomalyConfig = Field(default_factory=AnomalyConfig)
    subscriptions: SubscriptionConfig = Field(default_factory=SubscriptionConfig)
    forecast: ForecastConfig = Field(default_factory=ForecastConfig)
    advisory: AdvisoryConfig = Field(default_factory=AdvisoryConfig)
    category_rules: CategoryRules = Field(default_factory=CategoryRules)


class TriageConfig(_Section):
    p1_anomaly_score: float = 0.85
    p1_fraud_types: list[str] = Field(default_factory=list)
    p2_budget_overrun_ratio: float = 0.1
    p2_emergency_months: float = 1.0


class NotificationConfig(_Section):
    channel: str = "file"
    retries: int = 3
    base_delay_ms: int = 200
    max_delay_ms: int = 3000
    timeout_seconds: int = 10


class AlertsConfig(_Section):
    sla_minutes: dict[str, int]
    escalation_ratio: float = 0.8
    triage: TriageConfig = Field(default_factory=TriageConfig)
    notification: NotificationConfig = Field(default_factory=NotificationConfig)

    @model_validator(mode="after")
    def _sla(self) -> "AlertsConfig":
        for p in ("P1", "P2", "P3", "P4"):
            if p not in self.sla_minutes or self.sla_minutes[p] <= 0:
                raise ValueError(f"alerts.sla_minutes missing a positive value for {p}")
        return self


class AIConfig(_Section):
    enabled: bool = True
    provider: str = "openai_compatible"
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    temperature: float = 0.2
    max_output_tokens: int = 900
    timeout_seconds: int = 30
    max_retries: int = 2
    max_untrusted_chars: int = 200
    max_batch_size: int = 40
    # Groq model defaults (overridden by env vars at runtime)
    groq_model_fast: str = "llama-3.1-8b-instant"
    groq_model_analysis: str = "llama-3.3-70b-versatile"
    groq_model_advisor: str = "llama-3.3-70b-versatile"

    def api_key(self) -> str | None:
        """Read the key from the environment only — never from config.json."""
        for name in ("GROQ_API_KEY", "FINWELL_AI_API_KEY", "OPENAI_API_KEY"):
            value = os.getenv(name, "").strip()
            if value:
                return value
        return None

    def groq_model_for(self, task: str) -> str:
        """Return the Groq model name for the given task, respecting env overrides."""
        env_map = {
            "fast": os.getenv("GROQ_MODEL_FAST", self.groq_model_fast),
            "analysis": os.getenv("GROQ_MODEL_ANALYSIS", self.groq_model_analysis),
            "advisor": os.getenv("GROQ_MODEL_ADVISOR", self.groq_model_advisor),
        }
        return env_map.get(task, self.groq_model_fast)


class MCPConfig(_Section):
    enabled: bool = True
    prefer_for_workflow: bool = True
    server_module: str = "mcp_server.server"
    server_file: str = "mcp_server/server.py"
    retries: int = 2
    base_delay_ms: int = 250
    max_delay_ms: int = 2000
    call_timeout_seconds: int = 45
    startup_timeout_seconds: int = 20


class SecurityConfig(_Section):
    max_field_length: int = 280
    max_description_length: int = 400
    allowed_extensions: list[str] = Field(default_factory=lambda: [".csv", ".xlsx", ".xls", ".pdf"])
    max_amount: float = 100_000_000
    min_valid_date: str = "2000-01-01"


class LoggingConfig(_Section):
    level: str = "INFO"
    log_file: str = "data/app.log"


class AppConfig(_Section):
    app: AppMeta = Field(default_factory=AppMeta)
    financial: FinancialConfig
    alerts: AlertsConfig
    ai: AIConfig = Field(default_factory=AIConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    # -- convenience accessors -------------------------------------------------
    @property
    def rule_version(self) -> str:
        return self.app.rule_version

    @property
    def currency_symbol(self) -> str:
        return self.app.currency_symbol

    @property
    def data_root(self) -> Path:
        return (PROJECT_ROOT / self.app.data_dir).resolve()


_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, AppConfig]] = {}


def _apply_env_overrides(payload: dict[str, Any]) -> dict[str, Any]:
    """Environment variables override non-secret operational settings."""
    app_section = payload.setdefault("app", {})
    ai = payload.setdefault("ai", {})
    mcp = payload.setdefault("mcp", {})
    alerts = payload.setdefault("alerts", {})
    notification = alerts.setdefault("notification", {})
    logging_section = payload.setdefault("logging", {})

    if os.getenv("FINWELL_DATA_DIR"):
        # Supports isolated data roots for tests and portable deployments.
        app_section["data_dir"] = os.environ["FINWELL_DATA_DIR"]
    if os.getenv("FINWELL_AI_BASE_URL"):
        ai["base_url"] = os.environ["FINWELL_AI_BASE_URL"]
    if os.getenv("FINWELL_AI_MODEL"):
        ai["model"] = os.environ["FINWELL_AI_MODEL"]
    if os.getenv("FINWELL_DISABLE_AI", "").strip().lower() in {"1", "true", "yes"}:
        ai["enabled"] = False
    if os.getenv("GROQ_MODEL_FAST"):
        ai["groq_model_fast"] = os.environ["GROQ_MODEL_FAST"]
    if os.getenv("GROQ_MODEL_ANALYSIS"):
        ai["groq_model_analysis"] = os.environ["GROQ_MODEL_ANALYSIS"]
    if os.getenv("GROQ_API_KEY"):
        pass  # read at runtime only, never stored in config
    if os.getenv("FINWELL_MCP_ENABLED", "").strip().lower() in {"0", "false", "no"}:
        mcp["enabled"] = False
        mcp["prefer_for_workflow"] = False
    if os.getenv("FINWELL_LOG_LEVEL"):
        logging_section["level"] = os.environ["FINWELL_LOG_LEVEL"]
    if os.getenv("FINWELL_ALERT_WEBHOOK"):
        notification["channel"] = "webhook"
        notification["webhook_url"] = os.environ["FINWELL_ALERT_WEBHOOK"]
    return payload


def load_config(path: str | Path | None = None, *, reload: bool = False) -> AppConfig:
    """Load and validate configuration, cached by file modification time."""
    config_path = Path(path).resolve() if path else DEFAULT_CONFIG_PATH
    key = str(config_path)
    try:
        mtime = config_path.stat().st_mtime
    except FileNotFoundError as exc:  # pragma: no cover - configuration error
        raise RuntimeError(f"Configuration file not found: {config_path}") from exc

    with _cache_lock:
        cached = _cache.get(key)
        if cached and not reload and cached[0] == mtime:
            return cached[1]

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload = _apply_env_overrides(payload)
    config = AppConfig.model_validate(payload)

    with _cache_lock:
        _cache[key] = (mtime, config)
    return config


def reset_config_cache() -> None:
    with _cache_lock:
        _cache.clear()


def get_config() -> AppConfig:
    return load_config()


def ensure_data_dirs(config: AppConfig | None = None) -> Path:
    """Create the runtime data directories and return the data root."""
    config = config or get_config()
    root = config.data_root
    for relative in ("datasets", "notifications", "reports", "staging", "logs"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    return root
