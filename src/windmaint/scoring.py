"""Priority engine: "which turbine matters most?".

Pipeline: damage records -> per-turbine metrics -> three raw scores
(technical, contract, due) -> normalisation to 0-1 -> weighted sum.

The weighted terms are exported as ``*_Contribution`` columns; they add up to
``Final_Priority_Score`` exactly, so any score can be explained without
guesswork in the UI.
"""

from __future__ import annotations

from typing import Any, Mapping

import pandas as pd

from windmaint.contracts import compute_customer_limits
from windmaint.io import Dataset

UNASSIGNED_CUSTOMER = "Unassigned"


def summarise_damages(damages: pd.DataFrame, critical_threshold: float) -> pd.DataFrame:
    """Aggregate damage records to one row per turbine."""
    d = damages.copy()
    d["_delta"] = d["Current_Severity"] - d["Previous_Severity"]
    d["_increasing"] = d["_delta"] > 0
    d["_critical"] = d["Current_Severity"] >= critical_threshold
    summary = (
        d.groupby("Turbine_ID", sort=True)
        .agg(
            Max_Severity=("Current_Severity", "max"),
            Total_Damage_Count=("Damage_ID", "count"),
            Critical_Count=("_critical", "sum"),
            Delta_Sum=("_delta", "sum"),
            Increasing_Count=("_increasing", "sum"),
        )
        .reset_index()
    )
    for col in ("Total_Damage_Count", "Critical_Count", "Increasing_Count"):
        summary[col] = summary[col].astype(int)
    summary["Category"] = summary["Critical_Count"].map(
        lambda n: "Critical" if n > 0 else "Non-Critical"
    )
    return summary


def recommended_due_days(summary: pd.DataFrame, rules: pd.DataFrame) -> pd.Series:
    """First matching row of ``planning_rules`` wins; otherwise the Default rule."""
    specific = rules[rules["Category"] != "Default"]
    default_days = float(rules.loc[rules["Category"] == "Default", "Due_Days"].iloc[0])

    def lookup(row: pd.Series) -> float:
        for rule in specific.itertuples(index=False):
            if (
                row["Category"] == rule.Category
                and rule.Min_Increasing_Count <= row["Increasing_Count"] <= rule.Max_Increasing_Count
            ):
                return float(rule.Due_Days)
        return default_days

    return summary.apply(lookup, axis=1)


def build_summary(ds: Dataset, cfg: Mapping[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Join damages with turbine, site-contract and customer-contract data.

    Returns ``(summary, customer_limits)``. Only turbines with at least one
    damage record become maintenance jobs.
    """
    threshold = float(cfg["scoring"]["critical_severity_threshold"])
    summary = summarise_damages(ds.damages, threshold)
    summary = summary.merge(ds.turbines[["Turbine_ID", "Site", "Region"]], on="Turbine_ID", how="left")

    contract_cols = [
        c
        for c in (
            "Site",
            "Customer_Name",
            "Contract_Target_Availability",
            "Current_Availability",
            "Contract_Status",
            "Priority_Boost",
        )
        if c in ds.site_contracts.columns
    ]
    summary = summary.merge(ds.site_contracts[contract_cols], on="Site", how="left")
    summary["Customer_Name"] = summary["Customer_Name"].fillna(UNASSIGNED_CUSTOMER)
    summary["Priority_Boost"] = summary["Priority_Boost"].fillna(0.0)

    limits = compute_customer_limits(ds.customer_contracts)
    summary = summary.merge(
        limits[["Customer_Name", "Allowed_Additional_Down", "Contract_Time_Factor"]],
        on="Customer_Name",
        how="left",
    )
    summary["Contract_Time_Factor"] = summary["Contract_Time_Factor"].fillna(0.0)
    # NaN in Allowed_Additional_Down means "no cap known" and is kept as such.
    summary["Recommended_Due_Days"] = recommended_due_days(summary, ds.planning_rules)
    return summary, limits


def _minmax(series: pd.Series) -> pd.Series:
    low, high = series.min(), series.max()
    if high == low:
        return series * 0.0
    return (series - low) / (high - low)


def score(summary: pd.DataFrame, rules: pd.DataFrame, cfg: Mapping[str, Any]) -> pd.DataFrame:
    """Compute raw scores, normalised scores, contributions, final score and rank."""
    sc = cfg["scoring"]
    tw = sc["technical_weights"]
    out = summary.copy()

    out["Technical_Score"] = (
        tw["max_severity"] * out["Max_Severity"]
        + tw["increasing_count"] * out["Increasing_Count"]
        + tw["total_damage_count"] * out["Total_Damage_Count"]
        + tw["critical_count"] * out["Critical_Count"]
    )
    out["Contract_Score"] = out["Priority_Boost"] * (1 + out["Contract_Time_Factor"])
    out["Due_Score"] = 1.0 / out["Recommended_Due_Days"]

    norm = sc["normalization"]
    if norm["method"] == "minmax":
        out["Technical_Normalized"] = _minmax(out["Technical_Score"])
        out["Contract_Normalized"] = _minmax(out["Contract_Score"])
        out["Due_Normalized"] = _minmax(out["Due_Score"])
    else:
        out["Technical_Normalized"] = (
            out["Technical_Score"] / float(norm["technical_reference_max"])
        ).clip(0, 1)
        # Largest possible contract score: max boost at the end of the contract.
        out["Contract_Normalized"] = (
            out["Contract_Score"] / (2.0 * float(norm["priority_boost_max"]))
        ).clip(0, 1)
        # Bounds come from the rule table itself: slowest rule -> 0, fastest -> 1.
        due_low = 1.0 / float(rules["Due_Days"].max())
        due_high = 1.0 / float(rules["Due_Days"].min())
        if due_high == due_low:
            out["Due_Normalized"] = 0.0
        else:
            out["Due_Normalized"] = ((out["Due_Score"] - due_low) / (due_high - due_low)).clip(0, 1)

    weights = sc["weights"]
    out["Technical_Contribution"] = weights["technical"] * out["Technical_Normalized"]
    out["Contract_Contribution"] = weights["contract"] * out["Contract_Normalized"]
    out["Due_Contribution"] = weights["due"] * out["Due_Normalized"]
    out["Final_Priority_Score"] = (
        out["Technical_Contribution"] + out["Contract_Contribution"] + out["Due_Contribution"]
    )

    bands = sc["bands"]
    out["Priority_Band"] = out["Final_Priority_Score"].map(
        lambda s: "High" if s >= bands["high"] else "Medium" if s >= bands["medium"] else "Low"
    )
    return rank(out, critical_first=bool(sc["ranking"]["critical_first"]))


def rank(scored: pd.DataFrame, critical_first: bool) -> pd.DataFrame:
    """Sort into processing order and add ``Priority_Rank`` (1 = first).

    Ties are broken deterministically: higher max severity, larger severity
    growth, then turbine id.
    """
    out = scored.copy()
    out["_tier"] = (out["Category"] != "Critical").astype(int) if critical_first else 0
    out = out.sort_values(
        ["_tier", "Final_Priority_Score", "Max_Severity", "Delta_Sum", "Turbine_ID"],
        ascending=[True, False, False, False, True],
        kind="mergesort",
    ).reset_index(drop=True)
    out["Priority_Rank"] = range(1, len(out) + 1)
    return out.drop(columns="_tier")
