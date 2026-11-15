"""Backlog root-cause analytics.

Works on the structured slot diagnostics produced by the schedulers (no string
matching on a free-text reason). For every unscheduled turbine it reports:

* how many slots each constraint blocked;
* the **primary blocker** - the constraint that blocked the most slots;
* the **near miss** - the earliest slot that failed on exactly one constraint.
  If that constraint is the contract cap or crew capacity it is *actionable*:
  people can negotiate an outage or move a crew, nobody can move the weather.
"""

from __future__ import annotations

import pandas as pd

from windmaint.scheduling import (
    BLOCK_LABELS,
    CONTRACT,
    NO_RESOURCE,
    NO_WEATHER_DATA,
    RESOURCE,
    WEATHER,
)

BLOCKER_NAMES = {
    WEATHER: "Weather",
    NO_WEATHER_DATA: "Missing weather data",
    CONTRACT: "Contract availability cap",
    RESOURCE: "Resource capacity",
    NO_RESOURCE: "No resource for region",
    "NOT_SELECTED": "Not worth its cost",
}
ACTIONABLE = {CONTRACT, RESOURCE, NO_RESOURCE, NO_WEATHER_DATA}
COUNT_COLUMNS = {
    WEATHER: "Weather_Blocks",
    NO_WEATHER_DATA: "NoData_Blocks",
    CONTRACT: "Contract_Blocks",
    RESOURCE: "Resource_Blocks",
    NO_RESOURCE: "Resource_Blocks",
}
ROOT_CAUSE_COLUMNS = [
    "Turbine_ID",
    "Slots_Checked",
    "Weather_Blocks",
    "Contract_Blocks",
    "Resource_Blocks",
    "NoData_Blocks",
    "Primary_Blocker",
    "Near_Miss_Slot",
    "Near_Miss_Blocker",
    "Actionable",
]


def skip_reasons(diagnostics: pd.DataFrame) -> pd.Series:
    """Human-readable trace per turbine: ``day window: reason + reason | ...``."""
    if diagnostics.empty:
        return pd.Series(dtype=object, name="Skip_Reason")

    def describe(row: pd.Series) -> str:
        labels = [BLOCK_LABELS.get(code, "free but not selected") for code in row["Blocks"].split("+")]
        return f"{row['Day']} {row['Window']}: {' + '.join(labels)}"

    text = diagnostics.assign(_text=diagnostics.apply(describe, axis=1))
    return text.groupby("Turbine_ID", sort=False)["_text"].agg(" | ".join).rename("Skip_Reason")


def root_causes(diagnostics: pd.DataFrame, backlog_ids: list[str]) -> pd.DataFrame:
    rows = []
    for turbine in backlog_ids:
        slots = diagnostics[diagnostics["Turbine_ID"].astype(str) == str(turbine)]
        counts = {col: 0 for col in set(COUNT_COLUMNS.values())}
        frequency: dict[str, int] = {}
        near_slot, near_code = None, None
        for slot in slots.sort_values(["Day_Index"], kind="mergesort").itertuples(index=False):
            codes = slot.Blocks.split("+")
            for code in codes:
                frequency[code] = frequency.get(code, 0) + 1
                if code in COUNT_COLUMNS:
                    counts[COUNT_COLUMNS[code]] += 1
            if near_slot is None and len(codes) == 1:
                near_slot, near_code = f"{slot.Day} {slot.Window}", codes[0]
        # Prefer an actionable near miss over an earlier weather-only slot.
        for slot in slots.itertuples(index=False):
            codes = slot.Blocks.split("+")
            if len(codes) == 1 and codes[0] in ACTIONABLE:
                near_slot, near_code = f"{slot.Day} {slot.Window}", codes[0]
                break
        primary = max(frequency, key=lambda code: frequency[code]) if frequency else None
        rows.append(
            {
                "Turbine_ID": turbine,
                "Slots_Checked": len(slots),
                **counts,
                "Primary_Blocker": BLOCKER_NAMES.get(primary, primary),
                "Near_Miss_Slot": near_slot,
                "Near_Miss_Blocker": BLOCKER_NAMES.get(near_code, near_code),
                "Actionable": near_code in ACTIONABLE,
            }
        )
    return pd.DataFrame(rows, columns=ROOT_CAUSE_COLUMNS)


def blocker_totals(root: pd.DataFrame) -> dict[str, int]:
    """Fleet-level totals for the KPI row."""
    return {
        "Weather": int(root["Weather_Blocks"].sum()) if not root.empty else 0,
        "Contract": int(root["Contract_Blocks"].sum()) if not root.empty else 0,
        "Resource": int(root["Resource_Blocks"].sum()) if not root.empty else 0,
        "Actionable turbines": int(root["Actionable"].sum()) if not root.empty else 0,
    }
