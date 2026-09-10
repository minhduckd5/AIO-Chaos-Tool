"""
Map raw FeatureEngineer columns to a fixed canonical schema (signal-type pooling).

Enables merged training across public datasets and sparse lab Prom without
4624-wide sparse unions or feature-count mismatch at Advisor detect time.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

logger = logging.getLogger(__name__)

CANONICAL_SCHEMA_VERSION = "1"
DEFAULT_RULES_PATH = Path("examples/canonical_features.yaml")

_STAT_SUFFIXES = ("mean", "std", "roc", "p95", "p99")

# MODIFIED: per-service pooling for boutique/lab golden signals (Phase 2 RCA attribution)
# MODIFIED: pack__ series + log signals for telemetry packs
_PER_SERVICE_SIGNALS = frozenset({
    "errors",
    "request_rate",
    "latency",
    "availability",
    "log_volume",
    "log_error_rate",
})
_PASSTHROUGH_PREFIXES = (
    "custom__error",
    "custom__request_rate",
    "custom__latency",
    "custom__error_ratio",
    "custom__replicas",
    "custom__frontend_health",
    "custom__span_error",
    "pack__error",
    "pack__request_rate",
    "pack__latency",
    "pack__error_ratio",
    "pack__replicas",
    "pack__frontend_health",
    "pack__span_error",
    "pack__log_error",
    "pack__log_volume",
    # MODIFIED: Schema B boutique_otel_app — keep pack columns for dual-pack RCA
    "pack__otel_",
)

# Signal bucket names — never treat as microservice identities
_NON_SERVICE_TOKENS = frozenset({
    "custom", "canonical", "pack", "latency", "memory", "cpu", "disk", "network",
    "errors", "request", "request_rate", "error_rate", "error_ratio",
    "availability", "saturation", "log_volume", "log_error_rate",
    "mean", "std", "roc", "p95", "p99", "unknown",
    # MODIFIED: reserved entity for label-less series (entity-keyed layout)
    "_system",
})


def is_concrete_service(name: str | None) -> bool:
    """Return True when *name* looks like a microservice identity, not a signal bucket."""
    if not name:
        return False
    token = str(name).strip().lower()
    return bool(token) and token not in _NON_SERVICE_TOKENS and "__" not in token


def extract_service_from_column(name: str) -> str | None:
    """
    Parse a microservice identity from a FeatureEngineer column name.

    Supported shapes:
      custom__{category}__{metric}__{service}__{stat}
      pack__{signal}__{service}__{stat}
      canonical__{signal}__{service}__{stat}
      {metric}__{service}__{stat}
    """
    parts = str(name).split("__")
    if len(parts) < 3:
        return None

    stat = parts[-1].lower()
    if stat not in _STAT_SUFFIXES:
        return None

    if parts[0] == "custom" and len(parts) >= 5:
        candidate = parts[-2]
    elif parts[0] == "pack" and len(parts) >= 4:
        candidate = parts[-2]
    elif parts[0] == "canonical" and len(parts) >= 4:
        candidate = parts[-2]
    else:
        candidate = parts[-2]

    candidate = candidate.strip().lower()
    if not candidate or candidate in _NON_SERVICE_TOKENS:
        return None
    if candidate.startswith("boutique:") or ":" in candidate:
        return None
    return candidate


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_rules_path(path: str | Path | None) -> Path:
    if path is None:
        return _project_root() / DEFAULT_RULES_PATH
    p = Path(path)
    if p.is_file():
        return p
    rooted = _project_root() / p
    if rooted.is_file():
        return rooted
    return p


class CanonicalFeatureMapper:
    """Pool raw wide features into fixed canonical columns by signal type."""

    def __init__(self, config: dict[str, Any]):
        self.schema_version = str(config.get("schema_version", CANONICAL_SCHEMA_VERSION))
        self.stats: list[str] = list(config.get("stats", list(_STAT_SUFFIXES)))
        self.pooling: str = str(config.get("pooling", "max"))
        self.signals: dict[str, list[str]] = {
            k: list(v) for k, v in (config.get("signals") or {}).items()
        }
        self.log_signals: dict[str, list[str]] = {
            k: list(v) for k, v in (config.get("log_signals") or {}).items()
        }
        self._signal_patterns: list[tuple[str, re.Pattern[str]]] = []
        for signal, fragments in self.signals.items():
            combined = "|".join(f"(?:{frag})" for frag in fragments)
            self._signal_patterns.append((signal, re.compile(combined, re.IGNORECASE)))
        self._log_patterns: list[tuple[str, re.Pattern[str]]] = []
        for signal, fragments in self.log_signals.items():
            combined = "|".join(f"(?:{frag})" for frag in fragments)
            self._log_patterns.append((signal, re.compile(combined, re.IGNORECASE)))
        self.canonical_columns: list[str] = self._build_column_list()

    @classmethod
    def from_yaml(cls, path: str | Path | None = None) -> CanonicalFeatureMapper:
        rules_path = resolve_rules_path(path)
        if not rules_path.is_file():
            raise FileNotFoundError(f"Canonical rules not found: {rules_path}")
        with rules_path.open(encoding="utf-8") as fh:
            config = yaml.safe_load(fh) or {}
        return cls(config)

    @classmethod
    def from_settings(cls, settings: Any) -> CanonicalFeatureMapper | None:
        enabled = getattr(settings, "canonical_enabled", False)
        if not enabled:
            return None
        rules_path = getattr(settings, "canonical_rules_path", None)
        return cls.from_yaml(rules_path)

    def _build_column_list(self) -> list[str]:
        cols: list[str] = []
        for signal in self.signals:
            if signal == "availability":
                cols.append(f"canonical__{signal}__mean")
                continue
            for stat in self.stats:
                cols.append(f"canonical__{signal}__{stat}")
        for signal in self.log_signals:
            cols.append(f"canonical__{signal}__mean")
        return cols

    def classify_column(self, name: str) -> tuple[str | None, str | None]:
        """Return (signal_bucket, stat_suffix) for a raw column name."""
        lower = name.lower()
        stat: str | None = None
        base = lower
        for suffix in _STAT_SUFFIXES:
            token = f"__{suffix}"
            if lower.endswith(token):
                stat = suffix
                base = lower[: -len(token)]
                break
        if stat is None:
            if lower in ("log_volume", "error_rate"):
                for signal, pattern in self._log_patterns:
                    if pattern.search(lower):
                        return signal, "mean"
            return None, None

        for signal, pattern in self._signal_patterns:
            if pattern.search(base):
                if signal == "availability" and stat != "mean":
                    return signal, "mean"
                if stat in self.stats or signal == "availability":
                    return signal, stat if signal != "availability" else "mean"
        for signal, pattern in self._log_patterns:
            if pattern.search(base) or pattern.search(lower):
                return signal, "mean"
        return None, None

    def transform(self, raw_df: pd.DataFrame) -> pd.DataFrame:
        if raw_df.empty:
            return pd.DataFrame(columns=self.canonical_columns)

        buckets: dict[str, list[pd.Series]] = {col: [] for col in self.canonical_columns}
        per_service_buckets: dict[str, list[pd.Series]] = {}
        passthrough: dict[str, pd.Series] = {}
        unmapped = 0

        for col in raw_df.columns:
            col_str = str(col)
            if col_str.startswith(_PASSTHROUGH_PREFIXES):
                passthrough[col_str] = raw_df[col].astype(float, errors="ignore")
                continue

            signal, stat = self.classify_column(col_str)
            if signal is None or stat is None:
                unmapped += 1
                continue

            series = raw_df[col].astype(float, errors="ignore")
            service = extract_service_from_column(col_str)

            if signal in _PER_SERVICE_SIGNALS and service:
                svc_target = f"canonical__{signal}__{service}__{stat}"
                per_service_buckets.setdefault(svc_target, []).append(series)
            else:
                target = f"canonical__{signal}__{stat}"
                if target not in buckets:
                    unmapped += 1
                    continue
                buckets[target].append(series)

        out_data: dict[str, pd.Series] = {}
        for target, series_list in buckets.items():
            if not series_list:
                out_data[target] = pd.Series(0.0, index=raw_df.index, name=target)
                continue
            stacked = pd.concat(series_list, axis=1)
            if self.pooling == "max":
                out_data[target] = stacked.max(axis=1)
            else:
                out_data[target] = stacked.mean(axis=1)
            out_data[target].name = target

        for target, series_list in per_service_buckets.items():
            stacked = pd.concat(series_list, axis=1)
            if self.pooling == "max":
                out_data[target] = stacked.max(axis=1)
            else:
                out_data[target] = stacked.mean(axis=1)
            out_data[target].name = target

        result = pd.DataFrame(out_data, index=raw_df.index)
        base = result.reindex(columns=self.canonical_columns, fill_value=0.0)
        extras = result.drop(columns=self.canonical_columns, errors="ignore")
        passthrough_df = pd.DataFrame(passthrough, index=raw_df.index) if passthrough else pd.DataFrame(index=raw_df.index)
        result = pd.concat([base, extras, passthrough_df], axis=1)
        result = result.fillna(0.0)

        if unmapped:
            logger.debug(
                "Canonical mapper: %d / %d raw columns unmapped",
                unmapped,
                len(raw_df.columns),
            )
        logger.info(
            "Canonical feature matrix: %s (from %d raw columns, %d per-service, %d passthrough)",
            result.shape,
            len(raw_df.columns),
            len(extras.columns),
            len(passthrough_df.columns),
        )
        return result

    def mapping_report(self, raw_df: pd.DataFrame) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for col in raw_df.columns:
            signal, stat = self.classify_column(str(col))
            rows.append(
                {
                    "raw": str(col),
                    "signal": signal or "",
                    "stat": stat or "",
                    "canonical": f"canonical__{signal}__{stat}" if signal and stat else "",
                }
            )
        return rows


def apply_canonical_features(
    raw_df: pd.DataFrame,
    settings: Any | None,
) -> pd.DataFrame:
    """Apply canonical mapping when enabled in FeatureSettings."""
    if settings is None or not getattr(settings, "canonical_enabled", False):
        return raw_df
    # MODIFIED: entity-keyed columns are already fixed and service-free; pooling
    # them again only loses resolution.
    if isinstance(raw_df.index, pd.MultiIndex) and "service" in list(raw_df.index.names or []):
        logger.warning(
            "canonical_enabled=true is redundant under the entity-keyed feature "
            "layout; skipping canonical pooling to preserve the fixed schema"
        )
        return raw_df
    mapper = CanonicalFeatureMapper.from_settings(settings)
    if mapper is None:
        return raw_df
    return mapper.transform(raw_df)


def align_features_to_model(
    features: pd.DataFrame,
    feature_names: list[str],
) -> pd.DataFrame:
    """Reindex live features to trained column order; missing columns -> 0."""
    if not feature_names:
        return features
    aligned = features.reindex(columns=feature_names, fill_value=0.0)
    extra = set(features.columns) - set(feature_names)
    if extra:
        logger.debug(
            "Dropped %d live column(s) not in trained model schema",
            len(extra),
        )
    missing = set(feature_names) - set(features.columns)
    if missing:
        logger.debug(
            "Padded %d trained column(s) missing from live features with 0",
            len(missing),
        )
    return aligned.fillna(0.0)
