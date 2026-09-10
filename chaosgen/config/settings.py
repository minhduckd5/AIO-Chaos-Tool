"""
ChaosGen Settings — user-provided hints for Hybrid Discovery.

Stored as ~/.config/chaosgen/settings.yaml (XDG) or %APPDATA%/chaosgen/settings.yaml.
Secret values (tokens, passwords) are NEVER stored here — only key *names*
referencing entries in .env (e.g. token_ref: "PROMETHEUS_TOKEN").
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from chaosgen.config.paths import CONFIG_DIR, SETTINGS_FILE, ensure_config_dir
from chaosgen.schemas.discovery import ArchitectureType, EnvironmentType, ObservabilityTool

logger = logging.getLogger(__name__)

_SECRET_PATTERNS = re.compile(
    r"^(sk-|ghp_|gho_|xoxb-|xoxp-|Bearer\s|eyJ[A-Za-z0-9])",
    re.IGNORECASE,
)
_REF_FIELDS = {"token_ref", "password_ref"}
_INLINE_SECRET_LENGTH_THRESHOLD = 40


# ---------------------------------------------------------------------------
# Auth config
# ---------------------------------------------------------------------------


class AuthConfig(BaseModel):
    """
    Authentication configuration for an observability endpoint.

    token_ref / password_ref hold KEY NAMES in .env, not raw secret values.
    At probe time, the actual value is resolved via secrets.get_key().
    """

    auth_type: Literal["none", "bearer", "basic", "mtls"] = "none"
    token_ref: str | None = None
    username: str | None = None
    password_ref: str | None = None
    cert_path: str | None = None
    key_path: str | None = None
    ca_path: str | None = None


# ---------------------------------------------------------------------------
# Observability hint
# ---------------------------------------------------------------------------


class ObservabilityHint(BaseModel):
    tool: ObservabilityTool
    url: str
    auth: AuthConfig = Field(default_factory=AuthConfig)


# ---------------------------------------------------------------------------
# User hints
# ---------------------------------------------------------------------------


class UserHints(BaseModel):
    architecture: ArchitectureType | None = None
    environment: EnvironmentType | None = None
    observability: list[ObservabilityHint] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    skip_auto_detect: bool = False


# ---------------------------------------------------------------------------
# Gatekeeper settings (P1 — Gatekeeper Real Filter)
# ---------------------------------------------------------------------------


class GatekeeperSettings(BaseModel):
    """Thresholds and rules for the incident gatekeeper (`?? real ??`)."""

    frequency_low_threshold: float = 0.5   # events/hour
    frequency_high_threshold: float = 2.0
    severity_low_threshold: float = 0.4
    severity_high_threshold: float = 0.75
    log_correlation_boost: bool = True
    strict_log_boost: bool = True
    severe_log_keywords: list[str] = Field(
        default_factory=lambda: ["error", "fatal", "critical"]
    )
    ignore_log_keywords: list[str] = Field(
        default_factory=lambda: ["warning", "warn", "deprecation", "info"]
    )
    # MODIFIED: promote service-specific HTTP/5xx error signals to REAL (Phase 2 RCA)
    service_error_boost: bool = True


# ---------------------------------------------------------------------------
# Telemetry settings (P7 — Telemetry Window & Auto-K Anomalies)
# ---------------------------------------------------------------------------


class TelemetrySettings(BaseModel):
    """Defaults for telemetry window collection."""

    default_lookback_hours: int = 24
    step: str = "60s"


# ---------------------------------------------------------------------------
# Anomaly settings (P7 — Telemetry Window & Auto-K Anomalies)
# ---------------------------------------------------------------------------


class AnomalySettings(BaseModel):
    """Clustering and outlier detection policy."""

    clustering_mode: Literal["auto", "fixed"] = "auto"
    n_clusters: int = Field(default=5, ge=1)
    min_clusters: int = Field(default=2, ge=1)
    max_clusters: int = Field(default=15, ge=1)
    contamination: float = Field(default=0.1, gt=0, lt=0.5)
    # MODIFIED: P0-A — optional default joblib for classify-only inference
    default_model_path: str | None = None

    @model_validator(mode="after")
    def validate_cluster_bounds(self) -> AnomalySettings:
        if self.max_clusters < self.min_clusters:
            raise ValueError(
                f"max_clusters ({self.max_clusters}) must be >= min_clusters ({self.min_clusters})"
            )
        return self


# ---------------------------------------------------------------------------
# Feature Tuning settings (P8 — Analysis & Ranking Tuning)
# ---------------------------------------------------------------------------


class FeatureSettings(BaseModel):
    """Parameters for rolling window aggregation and anomaly scaling."""

    model_config = {"populate_by_name": True}

    # MODIFIED: P8 — accept plan aliases window_size_seconds / step_seconds
    rolling_window_seconds: int = Field(
        default=300, ge=10, le=7200, alias="window_size_seconds"
    )
    resample_step_seconds: int = Field(
        default=60, ge=10, le=1800, alias="step_seconds"
    )
    zscore_threshold: float = Field(default=5.0, ge=0.5, le=10.0)
    # MODIFIED: entity-keyed long format — service identity in the row index, not
    # in column names, so the trained column set is window-independent.
    # "wide" is the legacy dynamic-column layout, kept only as a debug escape hatch.
    layout: Literal["entity_keyed", "wide"] = "entity_keyed"
    # MODIFIED: canonical v1 — signal-type pooling for stable train/detect schema
    canonical_enabled: bool = False
    canonical_rules_path: str | None = "examples/canonical_features.yaml"

    @model_validator(mode="after")
    def warn_step_vs_window(self) -> FeatureSettings:
        if self.resample_step_seconds > self.rolling_window_seconds:
            logger.warning(
                "features.resample_step_seconds (%d) > rolling_window_seconds (%d); "
                "feature matrix may be sparse",
                self.resample_step_seconds,
                self.rolling_window_seconds,
            )
        return self


# ---------------------------------------------------------------------------
# Ingest settings (P8)
# ---------------------------------------------------------------------------


class IngestSettings(BaseModel):
    """Prometheus/Loki query tuning beyond telemetry.step."""

    log_query: str = '{namespace=~".+"}'
    custom_promql: dict[str, str] = Field(default_factory=dict)
    # --- START MODIFICATION ---
    # Form-first telemetry packs (ADR: telemetry-packs-form-first)
    telemetry_profile: str = "boutique"
    extra_packs: list[str] = Field(default_factory=list)
    scope_namespace: str = "default"
    allow_legacy_golden: bool = True
    # --- END MODIFICATION ---

    @model_validator(mode="after")
    def validate_custom_promql(self) -> IngestSettings:
        for name, query in self.custom_promql.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("ingest.custom_promql keys must be non-empty strings")
            if not isinstance(query, str) or not query.strip():
                raise ValueError(
                    f"ingest.custom_promql[{name!r}] must be a non-empty PromQL string"
                )
        return self

    @model_validator(mode="after")
    def validate_telemetry_packs(self) -> IngestSettings:
        # MODIFIED: reject unknown pack ids at settings load (deterministic packs only)
        from chaosgen.telemetry.pack_loader import list_available_packs

        available = set(list_available_packs())
        profile = (self.telemetry_profile or "").strip()
        if not profile:
            raise ValueError("ingest.telemetry_profile must be a non-empty pack id")
        unknown = [profile] if profile not in available else []
        for pack_id in self.extra_packs:
            pid = (pack_id or "").strip()
            if not pid:
                raise ValueError("ingest.extra_packs entries must be non-empty strings")
            if pid not in available:
                unknown.append(pid)
        if unknown:
            raise ValueError(
                f"Unknown telemetry pack(s) {unknown}; available: {sorted(available)}"
            )
        ns = (self.scope_namespace or "").strip()
        if not ns:
            raise ValueError("ingest.scope_namespace must be a non-empty string")
        self.telemetry_profile = profile
        self.scope_namespace = ns
        self.extra_packs = [(p or "").strip() for p in self.extra_packs]
        return self


# ---------------------------------------------------------------------------
# Advisor settings (P8)
# ---------------------------------------------------------------------------


class AdvisorSettings(BaseModel):
    """Scenario generation / describe sensitivity knobs."""

    confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    top_n_scenarios: int = Field(default=5, ge=1, le=50)
    describer_max_retries: int = Field(default=2, ge=0, le=10)


# ---------------------------------------------------------------------------
# Scenario Ranker Weights (P8 — Analysis & Ranking Tuning)
# ---------------------------------------------------------------------------


class RankingSettings(BaseModel):
    """Raw weight tuning for multi-criteria prioritization."""

    weight_confidence: float = Field(default=0.35, ge=0.0, le=10.0)
    weight_historical: float = Field(default=0.25, ge=0.0, le=10.0)
    weight_coverage: float = Field(default=0.20, ge=0.0, le=10.0)
    weight_safety: float = Field(default=0.20, ge=0.0, le=10.0)
    recency_days: int = Field(default=7, ge=1, le=90)

    @model_validator(mode="after")
    def validate_weight_sum(self) -> RankingSettings:
        total = (
            self.weight_confidence
            + self.weight_historical
            + self.weight_coverage
            + self.weight_safety
        )
        if total <= 0:
            raise ValueError("ranking weights must sum to a positive value")
        if abs(total - 1.0) > 0.01:
            # Auto-normalize in-place (Pydantic v2: return self only)
            logger.warning(
                "ranking weights sum to %.4f (expected ~1.0); normalizing", total
            )
            self.weight_confidence = self.weight_confidence / total
            self.weight_historical = self.weight_historical / total
            self.weight_coverage = self.weight_coverage / total
            self.weight_safety = self.weight_safety / total
        return self


# ---------------------------------------------------------------------------
# Safety bounds (P8 — Analysis & Ranking Tuning)
# ---------------------------------------------------------------------------


class SafetySettings(BaseModel):
    """Operator boundaries to prevent service-mesh disruption."""

    max_affected_nodes: int = Field(default=2, ge=1)
    max_affected_pods_percent: int = Field(default=20, ge=1, le=100)
    max_services_per_suite: int = Field(default=3, ge=1, le=20)
    blocked_namespaces: list[str] = Field(
        default_factory=lambda: ["kube-system", "monitoring"]
    )
    blocked_services: list[str] = Field(
        default_factory=lambda: ["database-master"]
    )


# ---------------------------------------------------------------------------
# Inject settings (real K8s chaos — kubeconfig / kubectl path)
# ---------------------------------------------------------------------------


class SshBastionSettings(BaseModel):
    """Optional OpenSSH local-forward when the API is not reachable directly."""

    enabled: bool = False
    auto_on_api_fail: bool = True
    host: str | None = None
    user: str | None = None
    port: int = Field(default=22, ge=1, le=65535)
    identity_file: str | None = None
    remote_api_host: str = "127.0.0.1"
    remote_api_port: int = Field(default=6443, ge=1, le=65535)
    local_port: int = Field(default=0, ge=0, le=65535)
    skip_tls_verify: bool = False


class InjectSettings(BaseModel):
    """Remote Kubernetes inject via kubeconfig (Lens-parity API path)."""

    enabled: bool = False
    kubeconfig: str | None = None
    context: str | None = None
    default_namespace: str = "default"
    label_key: str = "app"
    dry_run: bool = False
    chaos_backend: Literal["chaosmesh", "delete_pod"] = "chaosmesh"
    # auto: official client first, kubectl subprocess fallback
    client: Literal["auto", "native", "kubectl"] = "auto"
    # Primary experiment executor after CTK pivot
    executor: Literal["ctk", "kubectl"] = "ctk"
    kubectl_timeout_s: int = Field(default=30, ge=5, le=600)
    delete_force_on_timeout: bool = True
    prefer_self_expiring_chaos: bool = True
    managed_by_label: str = "chaosgen"
    ephemeral_label: str = "true"
    ssh_bastion: SshBastionSettings = Field(default_factory=SshBastionSettings)


# ---------------------------------------------------------------------------
# Connect settings (WS-1 stub — WS-2 wires GUI / validation)
# ---------------------------------------------------------------------------


class DockerConnectSettings(BaseModel):
    host: str | None = None
    compose_file: str | None = None
    project_name: str | None = None


class BrokerConnectSettings(BaseModel):
    type: Literal["kafka", "redpanda", "rabbitmq", "nats"] | None = None
    bootstrap: str | None = None
    admin_api_url: str | None = None
    schema_registry_url: str | None = None
    auth_token_ref: str | None = None


class ToxiproxyConnectSettings(BaseModel):
    api_url: str | None = None


class KubernetesConnectSettings(BaseModel):
    kubeconfig: str | None = None
    context: str | None = None
    default_namespace: str = "default"
    ssh_bastion: SshBastionSettings = Field(default_factory=SshBastionSettings)


class ConnectSettings(BaseModel):
    """Runtime connect profiles (form-first; parsed from settings.yaml)."""

    kubernetes: KubernetesConnectSettings = Field(default_factory=KubernetesConnectSettings)
    docker: DockerConnectSettings = Field(default_factory=DockerConnectSettings)
    broker: BrokerConnectSettings = Field(default_factory=BrokerConnectSettings)
    toxiproxy: ToxiproxyConnectSettings = Field(default_factory=ToxiproxyConnectSettings)


# ---------------------------------------------------------------------------
# History settings (P5 — SQLite History Loop)
# ---------------------------------------------------------------------------


class HistorySettings(BaseModel):
    """SQLite analytics history (not runtime SOT — see P5 plan §0)."""

    enabled: bool = True
    db_path: str | None = None
    async_writes: bool = True


# ---------------------------------------------------------------------------
# Top-level settings
# ---------------------------------------------------------------------------


class ChaosGenSettings(BaseModel):
    hints: UserHints = Field(default_factory=UserHints)
    # Audit actor identity (A8): prompted once, then persisted. Never defaulted
    # silently — an unattributable audit trail defeats the audit log.
    operator_name: str | None = None
    llm_provider: str = "ollama"
    llm_model: str | None = None
    developer_mode: bool = False
    telemetry: TelemetrySettings = Field(default_factory=TelemetrySettings)
    anomaly: AnomalySettings = Field(default_factory=AnomalySettings)
    features: FeatureSettings = Field(default_factory=FeatureSettings)
    ingest: IngestSettings = Field(default_factory=IngestSettings)
    advisor: AdvisorSettings = Field(default_factory=AdvisorSettings)
    ranking: RankingSettings = Field(default_factory=RankingSettings)
    safety: SafetySettings = Field(default_factory=SafetySettings)
    inject: InjectSettings = Field(default_factory=InjectSettings)
    connect: ConnectSettings = Field(default_factory=ConnectSettings)
    gatekeeper: GatekeeperSettings = Field(default_factory=GatekeeperSettings)
    history: HistorySettings = Field(default_factory=HistorySettings)


# ---------------------------------------------------------------------------
# Secret-leak guard
# ---------------------------------------------------------------------------


class InlineSecretError(ValueError):
    """Raised when settings.yaml appears to contain an inline secret value."""


def _check_no_inline_secrets(raw: dict[str, Any]) -> None:
    """
    Scan raw YAML dict for values that look like pasted secrets.
    Raises InlineSecretError if suspicious content is found.
    """
    for hint in raw.get("hints", {}).get("observability", []):
        auth = hint.get("auth", {})
        for field in _REF_FIELDS:
            val = auth.get(field)
            if not val or not isinstance(val, str):
                continue
            if _SECRET_PATTERNS.match(val):
                raise InlineSecretError(
                    f"settings.yaml field 'auth.{field}' value starts with a known "
                    f"secret prefix. Store the secret in .env via "
                    f"`chaosgen config set-key <NAME> <VALUE>` and put only "
                    f"the key name (e.g. 'PROMETHEUS_TOKEN') in settings.yaml."
                )
            if len(val) > _INLINE_SECRET_LENGTH_THRESHOLD:
                raise InlineSecretError(
                    f"settings.yaml field 'auth.{field}' value is {len(val)} chars long, "
                    f"which looks like an inline secret. Store it in .env and reference "
                    f"the key name here instead."
                )


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def load_settings(path: str | None = None) -> ChaosGenSettings:
    """
    Load settings from YAML. Falls back to defaults if file doesn't exist or is invalid.
    Runs secret-leak guard before parsing into Pydantic models.
    """
    settings_path = SETTINGS_FILE if path is None else __import__("pathlib").Path(path)

    if not settings_path.exists():
        logger.debug("No settings file at %s — using defaults", settings_path)
        return ChaosGenSettings()

    try:
        raw = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
        _check_no_inline_secrets(raw)
        return ChaosGenSettings.model_validate(raw)
    except Exception as e:
        logger.warning(
            "Failed to load settings from %s due to: %s. Falling back to default settings.",
            settings_path,
            e,
        )
        return ChaosGenSettings()


def save_settings(settings: ChaosGenSettings, path: str | None = None) -> None:
    """Persist settings to YAML."""
    settings_path = SETTINGS_FILE if path is None else __import__("pathlib").Path(path)
    ensure_config_dir()
    data = settings.model_dump(mode="json", exclude_none=True)
    settings_path.write_text(
        yaml.dump(data, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    logger.info("Settings saved to %s", settings_path)
