"""Rule-based, explainable recommendations.

This is an expert-system layer, not machine learning: every sentence is
generated from a value in the plan (score contributions, due date, slot
diagnostics, contract budget). Nothing is hard-coded per site or customer.
"""

from __future__ import annotations

from typing import Any, Mapping

import pandas as pd

LEVELS = ["Escalate", "Action", "On track"]
COLUMNS = ["Turbine_ID", "Level", "Headline", "Rationale", "Actions"]


def explain_score(row: Mapping[str, Any]) -> str:
    """One line that decomposes the final score into its real contributions."""
    parts = {
        "technical condition": float(row["Technical_Contribution"]),
        "contract pressure": float(row["Contract_Contribution"]),
        "due-date urgency": float(row["Due_Contribution"]),
    }
    total = float(row["Final_Priority_Score"])
    driver = max(parts, key=lambda name: parts[name])
    share = 100 * parts[driver] / total if total > 0 else 0
    return (
        f"Priority {total:.2f} = technical {parts['technical condition']:.2f} + contract "
        f"{parts['contract pressure']:.2f} + due {parts['due-date urgency']:.2f}; "
        f"main driver: {driver} ({share:.0f} %)."
    )


def _competitors(plan: pd.DataFrame, row: Mapping[str, Any], day: str) -> list[str]:
    same = plan[
        (plan["Customer_Name"] == row["Customer_Name"])
        & (plan["Scheduled_Day"] == day)
        & (plan["Turbine_ID"] != row["Turbine_ID"])
    ]
    return same["Turbine_ID"].astype(str).tolist()


def _override_hint(row: Mapping[str, Any], cfg: Mapping[str, Any]) -> str:
    """Mention the critical override only where it would actually apply."""
    contracts = cfg["contracts"]
    minimum = float(contracts["override_min_severity"])
    if float(row["Max_Severity"]) < minimum:
        return (
            f"The critical override does not apply (max severity {int(row['Max_Severity'])} "
            f"< {int(minimum)})."
        )
    if contracts["critical_override"]:
        return ""
    return "Alternatively, turn on the critical override (Scenario → Model options)."


def _backlog_advice(
    plan: pd.DataFrame, row: Mapping[str, Any], cfg: Mapping[str, Any]
) -> tuple[list[str], list[str]]:
    rationale: list[str] = []
    actions: list[str] = []
    slots = int(row.get("Slots_Checked") or 0)
    weather_blocks = int(row.get("Weather_Blocks") or 0)
    workable = slots - weather_blocks - int(row.get("NoData_Blocks") or 0)
    near = row.get("Near_Miss_Blocker")
    near_slot = row.get("Near_Miss_Slot")
    primary = row.get("Primary_Blocker")
    cap = row.get("Allowed_Additional_Down")
    cap = None if cap is None or pd.isna(cap) else int(cap)
    customer = row["Customer_Name"]

    if weather_blocks:
        rationale.append(
            f"Weather blocked {weather_blocks} of {slots} slots at site {row['Site']}."
        )

    if cap == 0:
        rationale.append(
            f"{customer} has no outage budget left: every slot fails the availability cap."
        )
        hint = _override_hint(row, cfg)
        actions.append(
            "The contract leaves no outage budget, so no schedule can place this turbine. "
            "Request a customer-approved outage or a temporary outage allowance."
            + (f" {hint}" if hint else "")
        )
    elif near == "Contract availability cap":
        day = str(near_slot).split(" ")[0]
        rationale.append(
            f"{near_slot} was workable; only the availability cap of {customer} "
            f"({cap} additional turbine(s) down per day) blocked it."
        )
        others = _competitors(plan, row, day)
        if others:
            actions.append(
                f"The cap on {day} is used by {', '.join(others)}. Re-sequence those jobs "
                "(the MILP scheduler does this automatically) or agree a second outage."
            )
    elif near == "Resource capacity":
        rationale.append(
            f"{near_slot} was workable and inside the contract cap; no crew covering region "
            f"{row['Region']} had capacity."
        )
        actions.append("Assign a contractor or a crew from a neighbouring region for that day.")
    elif near == "No resource for region":
        actions.append(f"No resource is defined for region {row['Region']}; add one to resources.csv.")
    elif near == "Missing weather data":
        actions.append(f"Weather data is missing for site {row['Site']}; complete weather.csv.")
    elif slots > 0 and workable <= 0:
        actions.append(
            "No workable weather window in the planning horizon. Re-run when the forecast "
            "updates or extend the horizon."
        )
    elif primary == "Contract availability cap":
        waiting = int(
            ((plan["Customer_Name"] == customer) & (plan["Schedule_Status"] == "Backlog")).sum()
        )
        rationale.append(
            f"On the {workable} workable slot(s) the cap of {customer} ({cap} per day) was "
            "already used by higher-priority jobs."
        )
        actions.append(
            f"Carry over to the next horizon ({waiting} job(s) of {customer} are waiting), or "
            "agree a temporary outage allowance if it must be done sooner."
        )
    elif primary == "Resource capacity":
        rationale.append(
            f"On the {workable} workable slot(s) every crew covering region {row['Region']} "
            "was booked by higher-priority jobs."
        )
        actions.append("Add crew capacity for this region or carry over to the next horizon.")
    else:
        rationale.append(
            f"The {workable} workable slot(s) were taken by higher-priority jobs "
            "(contract cap and crew capacity combined)."
        )
        actions.append("Carry over to the next horizon; re-run when the forecast updates.")
    return rationale, actions


def recommend(
    plan: pd.DataFrame, cfg: Mapping[str, Any], horizon_days: int | None = None
) -> pd.DataFrame:
    """One recommendation per turbine, most urgent first.

    ``horizon_days`` is the number of days actually planned (the weather table
    may hold fewer days than the configured horizon).
    """
    horizon = int(horizon_days or cfg["scheduling"]["horizon_days"])
    rows = []
    for row in plan.to_dict("records"):
        rationale = [explain_score(row)]
        actions: list[str] = []
        due = int(row["Recommended_Due_Days"])
        critical = row["Category"] == "Critical"

        if row["Schedule_Status"] == "Scheduled":
            headline = (
                f"Scheduled {row['Scheduled_Day']} {row['Scheduled_Window']} "
                f"with {row['Assigned_Resource']}"
            )
            if row["Due_Status"] == "Late":
                level = "Action"
                rationale.append(
                    f"Planned on day {int(row['Scheduled_Day_Index'])}, after the recommended "
                    f"{due}-day limit."
                )
                actions.append("Look for an earlier slot or accept and document the delay.")
            else:
                level = "On track"
                rationale.append(f"Inside the recommended {due}-day intervention limit.")
            actions.append("Re-check the forecast and crew availability the day before.")
            if row.get("Contract_Override"):
                level = "Action"
                actions.append(
                    "This slot exceeds the customer availability cap (critical override): "
                    "obtain customer approval before execution."
                )
        else:
            level = "Escalate" if critical else "Action"
            headline = f"Not scheduled within the {horizon}-day horizon"
            more_rationale, more_actions = _backlog_advice(plan, row, cfg)
            rationale.extend(more_rationale)
            actions.extend(more_actions)
            if due <= horizon:
                rationale.append(f"The recommended {due}-day limit expires inside this horizon.")
            if critical:
                actions.append(
                    f"Critical damage (max severity {int(row['Max_Severity'])}): keep under "
                    "active monitoring until a slot is confirmed."
                )

        factor = float(row.get("Contract_Time_Factor") or 0)
        if float(row.get("Priority_Boost") or 0) > 0:
            status = row.get("Contract_Status") or "below target"
            rationale.append(
                f"Site availability is '{status}' and the contract period is "
                f"{100 * factor:.0f} % elapsed."
            )

        rows.append(
            {
                "Turbine_ID": row["Turbine_ID"],
                "Level": level,
                "Headline": headline,
                "Rationale": "\n".join(rationale),
                "Actions": "\n".join(actions),
            }
        )

    out = pd.DataFrame(rows, columns=COLUMNS)
    out["_order"] = out["Level"].map({name: i for i, name in enumerate(LEVELS)})
    rank = dict(zip(plan["Turbine_ID"], plan["Priority_Rank"]))
    out["_rank"] = out["Turbine_ID"].map(rank)
    return out.sort_values(["_order", "_rank"]).drop(columns=["_order", "_rank"]).reset_index(drop=True)
