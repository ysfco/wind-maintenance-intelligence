"""Write a run to disk: a styled Excel workbook plus plain CSV files."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from windmaint.pipeline import RunResult

PLAN_COLUMNS = [
    "Priority_Rank",
    "Turbine_ID",
    "Site",
    "Region",
    "Customer_Name",
    "Category",
    "Priority_Band",
    "Final_Priority_Score",
    "Technical_Contribution",
    "Contract_Contribution",
    "Due_Contribution",
    "Schedule_Status",
    "Scheduled_Day",
    "Scheduled_Day_Index",
    "Scheduled_Window",
    "Assigned_Resource",
    "Due_Status",
    "Recommended_Due_Days",
    "Contract_Override",
    "Max_Severity",
    "Total_Damage_Count",
    "Critical_Count",
    "Increasing_Count",
    "Delta_Sum",
    "Technical_Score",
    "Contract_Score",
    "Due_Score",
    "Technical_Normalized",
    "Contract_Normalized",
    "Due_Normalized",
    "Priority_Boost",
    "Contract_Time_Factor",
    "Allowed_Additional_Down",
    "Contract_Status",
    "Primary_Blocker",
    "Near_Miss_Slot",
    "Near_Miss_Blocker",
    "Actionable",
    "Skip_Reason",
]

_FILLS = {
    "Backlog": "D9D9D9",
    "Late": "FFD966",
    "Critical": "FFC7CE",
    "Non-Critical": "C6EFCE",
}


def ordered_plan(plan: pd.DataFrame) -> pd.DataFrame:
    first = [c for c in PLAN_COLUMNS if c in plan.columns]
    rest = [c for c in plan.columns if c not in first]
    return plan[first + rest]


def _style(ws) -> None:
    thin = Side(style="thin", color="999999")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header = {cell.value: cell.column for cell in ws[1]}
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", start_color="DDEBF7")
        cell.border = border
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.freeze_panes = "A2"

    due_col, cat_col = header.get("Due_Status"), header.get("Category")
    for row in ws.iter_rows(min_row=2):
        colour = None
        if due_col:
            colour = _FILLS.get(row[due_col - 1].value)
        if colour is None and cat_col:
            colour = _FILLS.get(row[cat_col - 1].value)
        for cell in row:
            cell.border = border
            cell.alignment = Alignment(vertical="top", wrap_text=isinstance(cell.value, str) and "\n" in cell.value)
            if colour:
                cell.fill = PatternFill("solid", start_color=colour)
            if isinstance(cell.value, float):
                cell.number_format = "0.000"

    for column in ws.columns:
        longest = max(
            (max(len(line) for line in str(c.value).split("\n")) for c in column if c.value is not None),
            default=0,
        )
        ws.column_dimensions[get_column_letter(column[0].column)].width = min(longest + 2, 70)


def export(result: RunResult, out_dir: str | Path) -> dict[str, Path]:
    """Write ``wind_planning_output.xlsx`` and CSV copies. Returns the paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    plan = ordered_plan(result.plan)
    scheduled = plan[plan["Schedule_Status"] == "Scheduled"].sort_values(
        ["Scheduled_Day_Index", "Scheduled_Window", "Priority_Rank"],
        key=lambda s: s.map({"Morning": 0, "Afternoon": 1}).fillna(9) if s.name == "Scheduled_Window" else s,
    )
    backlog = plan[plan["Schedule_Status"] == "Backlog"]

    k = result.kpis
    info = pd.DataFrame(
        [
            ("Scheduler", k["scheduler"]),
            ("Solver status", k["solver_status"]),
            ("Planning days", ", ".join(k["planning_days"])),
            ("Jobs", k["jobs"]),
            ("Scheduled", k["scheduled"]),
            ("Backlog", k["backlog"]),
            ("Critical in backlog", k["critical_backlog"]),
            ("Late", k["late"]),
            ("Priority covered", round(k["priority_covered"], 3)),
            ("Plan cost", k["plan_cost"]),
            ("Configuration", json.dumps(result.config, ensure_ascii=False)),
        ],
        columns=["Item", "Value"],
    )

    sheets = {
        "Summary": plan,
        "Schedule": scheduled,
        "Backlog": backlog,
        "Recommendations": result.recommendations,
        "Customers": result.customers,
        "Slot_Diagnostics": result.diagnostics,
        "Run_Info": info,
    }
    xlsx = out / "wind_planning_output.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)
            _style(writer.book[name])

    paths = {"xlsx": xlsx}
    for name, frame in (
        ("plan", plan),
        ("backlog_root_causes", result.backlog),
        ("recommendations", result.recommendations),
        ("slot_diagnostics", result.diagnostics),
    ):
        paths[name] = out / f"{name}.csv"
        frame.to_csv(paths[name], index=False, sep=";", encoding="utf-8-sig")
    return paths
