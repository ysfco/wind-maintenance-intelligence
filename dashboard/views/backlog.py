from __future__ import annotations

import streamlit as st
from components import blocker_chart, kpi_row, show_chart, show_table

from windmaint.analytics import blockers_by_site


def render(result, context) -> None:
    st.title("Backlog root causes")
    st.caption("Why unscheduled turbines have no slot, and which constraint is worth challenging.")

    plan = context["plan"]
    backlog = plan[plan["Schedule_Status"] == "Backlog"].sort_values("Priority_Rank")
    if backlog.empty:
        st.success("No backlog: every job in this selection has a slot inside the horizon.")
        return

    kpi_row(
        [
            ("Backlog turbines", len(backlog), None),
            ("Critical", int((backlog["Category"] == "Critical").sum()), None),
            ("Weather blocks", int(backlog["Weather_Blocks"].sum()), "Slot × turbine combinations blocked by weather."),
            ("Contract blocks", int(backlog["Contract_Blocks"].sum()), None),
            ("Resource blocks", int(backlog["Resource_Blocks"].sum()), None),
            (
                "Actionable",
                int(backlog["Actionable"].fillna(False).astype(bool).sum()),
                "Turbines with a workable slot that failed only on contract or resources.",
            ),
        ]
    )
    st.divider()

    left, right = st.columns([1.2, 1])
    with left:
        st.subheader("Blocked slots by site")
        show_chart(blocker_chart(blockers_by_site(plan), label_column="Site"))
    with right:
        st.subheader("Primary blocker")
        primary = (
            backlog.groupby("Primary_Blocker", dropna=False)
            .agg(
                Turbines=("Turbine_ID", "count"),
                Critical=("Category", lambda s: int((s == "Critical").sum())),
            )
            .reset_index()
            .sort_values("Turbines", ascending=False)
            .rename(columns={"Primary_Blocker": "Primary blocker"})
        )
        show_table(primary)
        st.subheader("By customer")
        by_customer = (
            backlog.groupby("Customer_Name")
            .agg(
                Backlog=("Turbine_ID", "count"),
                Cap_per_day=("Allowed_Additional_Down", "first"),
            )
            .reset_index()
            .rename(columns={"Customer_Name": "Customer", "Cap_per_day": "Cap per day"})
        )
        show_table(by_customer)
    st.caption(
        "Primary blocker = constraint that blocked the most slots. Nearest miss = a slot that "
        "failed on a single constraint; if that is the contract cap or crew capacity, the "
        "backlog is actionable."
    )

    st.subheader("Backlog turbines")
    show_table(
        backlog[
            [
                "Priority_Rank",
                "Turbine_ID",
                "Site",
                "Customer_Name",
                "Category",
                "Recommended_Due_Days",
                "Weather_Blocks",
                "Contract_Blocks",
                "Resource_Blocks",
                "Primary_Blocker",
                "Near_Miss_Slot",
                "Near_Miss_Blocker",
                "Actionable",
            ]
        ].rename(
            columns={
                "Priority_Rank": "Rank",
                "Turbine_ID": "Turbine",
                "Customer_Name": "Customer",
                "Recommended_Due_Days": "Due (days)",
                "Weather_Blocks": "Weather",
                "Contract_Blocks": "Contract",
                "Resource_Blocks": "Resource",
                "Primary_Blocker": "Primary blocker",
                "Near_Miss_Slot": "Nearest miss",
                "Near_Miss_Blocker": "Blocked there by",
            }
        ),
        height=420,
    )
    st.caption("Open **Turbine detail** for the slot-by-slot check of a single turbine.")
