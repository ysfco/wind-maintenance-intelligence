from __future__ import annotations

import streamlit as st
from components import contribution_chart, kpi_row, show_chart, show_table

from windmaint.analytics import subset_kpis


def render(result, context) -> None:
    plan = context["plan"]
    k = subset_kpis(plan)
    run = result.kpis
    st.title("Fleet overview")
    scope = f"{k['jobs']} of {run['jobs']} jobs shown · " if context["filtered"] else ""
    st.caption(
        f"{scope}{len(result.dataset.turbines)} turbines in the fleet · planning days "
        f"{run['planning_days'][0]} → {run['planning_days'][-1]} ({len(run['planning_days'])}) · "
        f"scheduler: {run['scheduler']} ({run['solver_status']})"
    )

    kpi_row(
        [
            ("Maintenance jobs", k["jobs"], "Turbines with at least one open damage record."),
            ("Critical turbines", k["critical"], "At least one damage at or above the critical severity."),
            ("Scheduled", k["scheduled"], None),
            ("Backlog", k["backlog"], "No feasible slot inside the planning horizon."),
            ("Critical in backlog", k["critical_backlog"], None),
            ("Late", k["late"], "Scheduled after the recommended due day."),
            (
                "Priority covered",
                f"{k['priority_covered']:.0%}",
                "Share of total priority score that the plan schedules.",
            ),
        ]
    )
    st.divider()

    left, right = st.columns([1.2, 1])
    with left:
        st.subheader("Highest priorities")
        top = min(15, len(plan))
        if len(plan) > 10:
            top = st.slider("Turbines shown", 5, min(40, len(plan)), top)
        st.caption("Bars are the real weighted contributions; they add up to the score. ● = critical.")
        show_chart(contribution_chart(plan.sort_values("Priority_Rank").head(top)))
    with right:
        st.subheader("All jobs")
        table = plan.sort_values("Priority_Rank")[
            [
                "Priority_Rank",
                "Turbine_ID",
                "Site",
                "Customer_Name",
                "Category",
                "Final_Priority_Score",
                "Schedule_Status",
                "Scheduled_Day",
                "Due_Status",
            ]
        ].rename(
            columns={
                "Priority_Rank": "Rank",
                "Turbine_ID": "Turbine",
                "Customer_Name": "Customer",
                "Final_Priority_Score": "Score",
                "Schedule_Status": "Status",
                "Scheduled_Day": "Day",
                "Due_Status": "Due",
            }
        )
        table["Score"] = table["Score"].round(3)
        show_table(table, height=560)

    st.subheader("By site")
    by_site = (
        plan.groupby(["Site", "Customer_Name"])
        .agg(
            Jobs=("Turbine_ID", "count"),
            Critical=("Category", lambda s: int((s == "Critical").sum())),
            Scheduled=("Schedule_Status", lambda s: int((s == "Scheduled").sum())),
            Backlog=("Schedule_Status", lambda s: int((s == "Backlog").sum())),
            Highest_score=("Final_Priority_Score", "max"),
        )
        .reset_index()
        .sort_values("Highest_score", ascending=False)
        .rename(columns={"Customer_Name": "Customer", "Highest_score": "Highest score"})
    )
    show_table(by_site.round(3))

    weights = result.config["scoring"]["weights"]
    bands = result.config["scoring"]["bands"]
    with st.expander("How the score is built"):
        st.markdown(
            f"""
**Final score** = {weights['technical']:.2f} × technical + {weights['contract']:.2f} × contract
+ {weights['due']:.2f} × due, each component normalised to 0–1
(method: `{result.config['scoring']['normalization']['method']}`).

- **Technical** — maximum severity, number of growing damages, number of damages, number of critical damages.
- **Contract** — site priority boost × (1 + elapsed share of the contract period).
- **Due** — 1 / recommended intervention days from the planning rules.

The score is a **relative priority index**. It is not a failure probability.
Bands (high ≥ {bands['high']:.2f}, medium ≥ {bands['medium']:.2f}) are display conventions,
not calibrated thresholds. Weather is not part of the score: it decides whether a
slot can be used, not how important a turbine is.
"""
        )
