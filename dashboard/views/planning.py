from __future__ import annotations

import tempfile

import streamlit as st
from components import jobs_per_day_chart, kpi_row, show_chart, show_table

from windmaint.analytics import dispatch_table, jobs_per_day, resource_utilisation, subset_kpis
from windmaint.export import export, ordered_plan


def _workbook_bytes(result) -> bytes:
    with tempfile.TemporaryDirectory() as folder:
        return export(result, folder)["xlsx"].read_bytes()


def render(result, context) -> None:
    plan = context["plan"]
    days = result.kpis["planning_days"]
    k = subset_kpis(plan)
    st.title("Maintenance plan")
    st.caption("What can actually be done in the horizon, given weather, contract caps and crews.")

    kpi_row(
        [
            ("Scheduled", k["scheduled"], None),
            ("Backlog", k["backlog"], None),
            ("Late", k["late"], "Scheduled after the recommended due day."),
            (
                "Plan cost (fleet)",
                f"{result.kpis['plan_cost']:.1f}",
                "Weighted delay + backlog cost over all jobs. Lower is better.",
            ),
        ]
    )
    st.divider()

    st.subheader("Jobs per day")
    show_chart(jobs_per_day_chart(jobs_per_day(plan, days)))

    st.subheader("Crew dispatch")
    st.caption("(M) morning · (A) afternoon · ! critical turbine")
    dispatch = dispatch_table(plan, days)
    if dispatch.empty:
        st.info("Nothing is scheduled for the current selection.")
    else:
        show_table(dispatch)

    st.subheader("Day detail")
    day = st.selectbox("Day", days)
    day_jobs = plan[plan["Scheduled_Day"] == day].sort_values(["Scheduled_Window", "Priority_Rank"])
    if day_jobs.empty:
        st.write("No jobs on this day.")
    else:
        show_table(
            day_jobs[
                [
                    "Scheduled_Window",
                    "Turbine_ID",
                    "Site",
                    "Customer_Name",
                    "Category",
                    "Max_Severity",
                    "Assigned_Resource",
                    "Due_Status",
                    "Priority_Rank",
                ]
            ].rename(
                columns={
                    "Scheduled_Window": "Window",
                    "Turbine_ID": "Turbine",
                    "Customer_Name": "Customer",
                    "Max_Severity": "Max severity",
                    "Assigned_Resource": "Resource",
                    "Due_Status": "Due",
                    "Priority_Rank": "Rank",
                }
            )
        )

    st.subheader("Resource utilisation")
    usage = resource_utilisation(result)
    total = usage.groupby("Resource_ID")[["Used", "Capacity"]].sum()
    usage["Load"] = usage["Used"].astype(str) + " / " + usage["Capacity"].astype(str)
    table = usage.pivot(
        index=["Resource_ID", "Resource_Type", "Contractor", "Region"], columns="Day", values="Load"
    ).reset_index()
    table.insert(
        4,
        "Horizon",
        table["Resource_ID"].map(
            lambda r: f"{100 * total.loc[r, 'Used'] / max(total.loc[r, 'Capacity'], 1):.0f} %"
        ),
    )
    show_table(table)
    st.caption("Fleet-wide (not filtered). Low utilisation next to a backlog means crews are not the bottleneck.")

    with st.expander("Full plan table"):
        show_table(ordered_plan(plan).drop(columns=["Skip_Reason"], errors="ignore"))
    st.download_button(
        "Download Excel report (whole fleet)",
        data=_workbook_bytes(result),
        file_name="wind_planning_output.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
