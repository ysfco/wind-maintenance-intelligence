from __future__ import annotations

import pandas as pd
import streamlit as st
from components import kpi_row, show_table


def render(result, context) -> None:
    st.title("Contracts")
    st.caption(
        "Two separate effects: a soft priority boost for sites below target, and a hard daily "
        "cap on turbines taken down per customer."
    )

    customers = result.customers
    known = customers.dropna(subset=["Allowed_Additional_Down"])
    kpi_row(
        [
            ("Customers", len(customers), None),
            (
                "No outage budget",
                int((known["Allowed_Additional_Down"] == 0).sum()),
                "Customers whose availability budget is already used up.",
            ),
            (
                "Period > 70 % elapsed",
                int((customers["Contract_Time_Factor"] >= 0.7).sum()),
                "Little time left to recover availability before the period closes.",
            ),
            ("Jobs in backlog", int(customers["Backlog"].sum()), None),
        ]
    )
    st.divider()

    for row in customers.itertuples(index=False):
        with st.container(border=True):
            st.subheader(row.Customer_Name)
            if pd.isna(row.Allowed_Additional_Down):
                st.info("No customer contract row: availability cap is not enforced.")
                continue
            a, b, c = st.columns(3)
            a.metric("Target availability", f"{row.Contract_Target_Availability:g} %")
            b.metric(
                "Outage budget left",
                f"{int(row.Allowed_Additional_Down)} / {int(row.Allowed_Total_Down)}",
                help=(
                    f"floor({int(row.Total_Turbines)} turbines × "
                    f"{100 - row.Contract_Target_Availability:g} %) = "
                    f"{int(row.Allowed_Total_Down)} may be down; "
                    f"{int(row.Current_Down_Turbines)} already are."
                ),
            )
            c.metric(
                "Planned peak per day",
                f"{int(row.Peak_Daily_Down_Planned)}",
                help="Largest number of this customer's turbines scheduled on one day.",
            )
            st.progress(
                float(min(max(row.Contract_Time_Factor, 0.0), 1.0)),
                text=(
                    f"Contract period elapsed: month {int(row.Contract_Month)} of "
                    f"{int(row.Contract_Total_Months)} ({row.Contract_Time_Factor:.0%})"
                ),
            )
            st.write(
                f"{int(row.Planned_Turbines)} job(s): {int(row.Scheduled)} scheduled, "
                f"{int(row.Backlog)} in backlog, {int(row.Critical_Turbines)} critical."
            )
            if row.Allowed_Additional_Down == 0 and row.Backlog > 0:
                st.error(
                    "The availability budget is exhausted, so none of this customer's turbines "
                    "can be scheduled without a customer-approved outage."
                )

    with st.expander("Table and definitions"):
        show_table(customers.round(3))
        st.markdown(
            """
- **Contract period elapsed** (`Contract_Time_Factor`) = contract month / total months.
  It measures time, not money: it is not a penalty estimate or an SLA-breach probability.
- **Outage budget** = floor(total turbines × (1 − target availability)) − turbines currently down.
  The scheduler never plans more turbines of one customer per day than the budget left.
"""
        )
