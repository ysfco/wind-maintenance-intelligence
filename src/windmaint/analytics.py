"""Aggregations used by the dashboard (kept here so they can be unit-tested)."""

from __future__ import annotations

from typing import Mapping, Sequence

import pandas as pd

from windmaint.pipeline import RunResult
from windmaint.weather import classify_site_weather


def site_overview(result: RunResult) -> pd.DataFrame:
    """One row per site: workload, plan status, snapshot weather, coordinates."""
    plan = result.plan
    sites = (
        plan.groupby("Site")
        .agg(
            Customer_Name=("Customer_Name", "first"),
            Jobs=("Turbine_ID", "count"),
            Critical=("Category", lambda s: int((s == "Critical").sum())),
            Scheduled=("Schedule_Status", lambda s: int((s == "Scheduled").sum())),
            Backlog=("Schedule_Status", lambda s: int((s == "Backlog").sum())),
            Max_Priority=("Final_Priority_Score", "max"),
            Average_Priority=("Final_Priority_Score", "mean"),
        )
        .reset_index()
    )
    critical_backlog = (
        plan[(plan["Category"] == "Critical") & (plan["Schedule_Status"] == "Backlog")]
        .groupby("Site")
        .size()
    )
    sites["Critical_Backlog"] = sites["Site"].map(critical_backlog).fillna(0).astype(int)
    sites["Site_Status"] = "On plan"
    sites.loc[sites["Backlog"] > 0, "Site_Status"] = "Backlog"
    sites.loc[sites["Critical_Backlog"] > 0, "Site_Status"] = "Critical backlog"

    ds = result.dataset
    if ds.site_weather is not None:
        snapshot = ds.site_weather.copy()
        snapshot["Weather_Status"] = classify_site_weather(snapshot, result.config)
        sites = sites.merge(snapshot, on="Site", how="left")
    if ds.site_locations is not None:
        sites = sites.merge(ds.site_locations, on="Site", how="left")
    return sites.sort_values("Max_Priority", ascending=False).reset_index(drop=True)


def resource_utilisation(result: RunResult) -> pd.DataFrame:
    """Jobs assigned vs daily capacity for every resource and planning day."""
    days = result.kpis["planning_days"]
    resources = result.dataset.resources
    scheduled = result.plan[result.plan["Schedule_Status"] == "Scheduled"]
    used = scheduled.groupby(["Assigned_Resource", "Scheduled_Day"]).size()
    rows = []
    for res in resources.itertuples(index=False):
        for day in days:
            rows.append(
                {
                    "Resource_ID": res.Resource_ID,
                    "Resource_Type": res.Resource_Type,
                    "Contractor": res.Contractor,
                    "Region": res.Region,
                    "Day": day,
                    "Used": int(used.get((res.Resource_ID, day), 0)),
                    "Capacity": int(res.Daily_Capacity),
                }
            )
    return pd.DataFrame(rows)


def calendar(result: RunResult) -> dict[str, dict[str, pd.DataFrame]]:
    """``{day: {window: scheduled jobs}}`` for the planning board."""
    windows = result.config["scheduling"]["time_windows"]
    scheduled = result.plan[result.plan["Schedule_Status"] == "Scheduled"]
    return {
        day: {
            window: scheduled[
                (scheduled["Scheduled_Day"] == day) & (scheduled["Scheduled_Window"] == window)
            ].sort_values("Priority_Rank")
            for window in windows
        }
        for day in result.kpis["planning_days"]
    }


def apply_filters(plan: pd.DataFrame, filters: Mapping[str, Sequence]) -> pd.DataFrame:
    """Keep rows whose column value is in the chosen list; an empty list = no filter."""
    out = plan
    for column, allowed in filters.items():
        if allowed and column in out.columns:
            out = out[out[column].isin(list(allowed))]
    return out


def subset_kpis(plan: pd.DataFrame) -> dict[str, float]:
    """Headline numbers for any subset of the plan (fleet, customer, region...)."""
    scheduled = plan["Schedule_Status"] == "Scheduled"
    critical = plan["Category"] == "Critical"
    total = float(plan["Final_Priority_Score"].sum())
    return {
        "jobs": int(len(plan)),
        "critical": int(critical.sum()),
        "scheduled": int(scheduled.sum()),
        "backlog": int((~scheduled).sum()),
        "critical_backlog": int((critical & ~scheduled).sum()),
        "late": int((plan["Due_Status"] == "Late").sum()),
        "priority_covered": float(plan.loc[scheduled, "Final_Priority_Score"].sum()) / total
        if total
        else 0.0,
    }


def jobs_per_day(plan: pd.DataFrame, days: Sequence[str]) -> pd.DataFrame:
    """Scheduled jobs per planning day, split by category."""
    scheduled = plan[plan["Schedule_Status"] == "Scheduled"]
    counts = scheduled.groupby(["Scheduled_Day", "Category"]).size().unstack(fill_value=0)
    counts = counts.reindex(index=list(days), columns=["Critical", "Non-Critical"], fill_value=0)
    return counts.rename_axis("Day").reset_index()


def dispatch_table(plan: pd.DataFrame, days: Sequence[str]) -> pd.DataFrame:
    """Crew view: one row per resource, one column per day, cells list the jobs.

    ``AEG01-T03 (M)`` = morning, ``(A)`` = afternoon; ``!`` marks a critical turbine.
    """
    scheduled = plan[plan["Schedule_Status"] == "Scheduled"].sort_values(
        ["Scheduled_Day_Index", "Priority_Rank"]
    )
    cells: dict[tuple[str, str], list[str]] = {}
    for job in scheduled.itertuples(index=False):
        mark = "!" if job.Category == "Critical" else ""
        text = f"{job.Turbine_ID}{mark} ({str(job.Scheduled_Window)[:1]})"
        cells.setdefault((job.Assigned_Resource, job.Scheduled_Day), []).append(text)
    resources = sorted({key[0] for key in cells})
    table = pd.DataFrame(
        [[", ".join(cells.get((res, day), [])) for day in days] for res in resources],
        columns=list(days),
    )
    table.insert(0, "Resource", resources)
    return table


def blockers_by_site(plan: pd.DataFrame) -> pd.DataFrame:
    """Backlog turbines and blocked slots per site, by constraint."""
    backlog = plan[plan["Schedule_Status"] == "Backlog"]
    columns = ["Weather_Blocks", "Contract_Blocks", "Resource_Blocks"]
    if backlog.empty:
        return pd.DataFrame(columns=["Site", "Backlog", *columns])
    out = backlog.groupby("Site").agg(
        Backlog=("Turbine_ID", "count"), **{c: (c, "sum") for c in columns}
    )
    out[columns] = out[columns].astype(int)
    return out.sort_values("Backlog", ascending=False).reset_index()
