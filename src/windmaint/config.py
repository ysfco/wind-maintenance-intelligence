"""Configuration: defaults, YAML loading and validation.

Every tunable number of the model lives here (and in ``config/default.yaml``,
which mirrors ``DEFAULTS``) so that nothing is hard-coded inside the engines.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

import yaml


class ConfigError(ValueError):
    """Raised when the configuration is structurally invalid."""


DEFAULTS: dict[str, Any] = {
    "scoring": {
        # A damage with Current_Severity >= this value is "critical".
        "critical_severity_threshold": 4,
        # Technical_Score = sum(weight * metric)
        "technical_weights": {
            "max_severity": 2.0,
            "increasing_count": 1.5,
            "total_damage_count": 1.0,
            "critical_count": 3.0,
        },
        # Final_Priority_Score = sum(weight * normalised component). Must sum to 1.
        "weights": {"technical": 0.60, "contract": 0.25, "due": 0.15},
        "normalization": {
            # "reference": divide by fixed reference bounds (scores are stable
            #              when turbines are added/removed).
            # "minmax":    legacy behaviour, rescales against the current fleet.
            "method": "reference",
            # Technical score regarded as "worst realistic case" (maps to 1.0).
            "technical_reference_max": 25.0,
            # Upper end of the Priority_Boost scale used in site_contracts.
            "priority_boost_max": 2.0,
        },
        "ranking": {
            # True: every Critical turbine ranks above every Non-Critical one;
            # the score only orders turbines inside a tier.
            "critical_first": True,
        },
        # Display bands for Final_Priority_Score (uncalibrated, presentation only).
        "bands": {"high": 0.70, "medium": 0.40},
    },
    "contracts": {
        # True: turbines with Max_Severity >= override_min_severity may be
        # scheduled even when the customer availability cap is exhausted
        # (treated as a customer-approved outage; flagged in the output).
        "critical_override": False,
        "override_min_severity": 5,
    },
    "scheduling": {
        "horizon_days": 10,
        "time_windows": ["Morning", "Afternoon"],
        "scheduler": "greedy",  # greedy | milp
        "milp": {
            # Added to a job's worst in-horizon slot cost when it stays unscheduled.
            "backlog_penalty_days": 10.0,
            # Extra cost per day scheduled after Recommended_Due_Days.
            "late_penalty_per_day": 5.0,
            # Small cost that makes Morning preferable to Afternoon.
            "window_tiebreak": 0.1,
            # Weight bonus for Critical turbines when ranking.critical_first is on.
            "critical_weight_bonus": 1.0,
            "time_limit_s": 30,
        },
    },
    "weather": {
        # "flag":  trust the Workable column of weather.csv.
        # "rules": derive workability from measurements with the profile below.
        "workability_source": "flag",
        "rules": {
            "default": {
                "max_wind_speed": 12.0,  # m/s
                "max_wind_gust": 18.0,  # m/s
                "max_precip_probability": 60.0,  # %
                "min_temperature": -10.0,  # deg C
            }
        },
        # Site-level status shown on the map (from site_weather.csv).
        "site_status": {"blocked_wind": 15.0, "risky_wind": 10.0},
    },
}


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def validate_config(cfg: Mapping[str, Any]) -> None:
    """Raise :class:`ConfigError` if ``cfg`` cannot drive the engines."""
    weights = cfg["scoring"]["weights"]
    missing = {"technical", "contract", "due"} - set(weights)
    if missing:
        raise ConfigError(f"scoring.weights is missing: {sorted(missing)}")
    if any(float(w) < 0 for w in weights.values()):
        raise ConfigError("scoring.weights must be non-negative")
    total = sum(float(w) for w in weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ConfigError(f"scoring.weights must sum to 1.0 (got {total:.4f})")

    method = cfg["scoring"]["normalization"]["method"]
    if method not in {"reference", "minmax"}:
        raise ConfigError(f"scoring.normalization.method must be reference|minmax, got {method!r}")
    if float(cfg["scoring"]["normalization"]["technical_reference_max"]) <= 0:
        raise ConfigError("scoring.normalization.technical_reference_max must be > 0")
    if float(cfg["scoring"]["normalization"]["priority_boost_max"]) <= 0:
        raise ConfigError("scoring.normalization.priority_boost_max must be > 0")

    bands = cfg["scoring"]["bands"]
    if not 0 <= float(bands["medium"]) <= float(bands["high"]) <= 1:
        raise ConfigError("scoring.bands must satisfy 0 <= medium <= high <= 1")

    sched = cfg["scheduling"]
    if sched["scheduler"] not in {"greedy", "milp"}:
        raise ConfigError(f"scheduling.scheduler must be greedy|milp, got {sched['scheduler']!r}")
    if int(sched["horizon_days"]) < 1:
        raise ConfigError("scheduling.horizon_days must be >= 1")
    if not sched["time_windows"]:
        raise ConfigError("scheduling.time_windows must not be empty")

    if cfg["weather"]["workability_source"] not in {"flag", "rules"}:
        raise ConfigError("weather.workability_source must be flag|rules")


def load_config(
    path: str | Path | None = None, overrides: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Return defaults, merged with a YAML file and then with ``overrides``."""
    cfg = copy.deepcopy(DEFAULTS)
    if path is not None:
        with open(path, encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
        if not isinstance(loaded, Mapping):
            raise ConfigError(f"{path} must contain a YAML mapping")
        _deep_merge(cfg, loaded)
    if overrides:
        _deep_merge(cfg, overrides)
    validate_config(cfg)
    return cfg
