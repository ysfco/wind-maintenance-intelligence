from __future__ import annotations

import pandas as pd
import streamlit as st
from components import LEVEL_ICON, bullets, kpi_row, show_table


def render(result, context) -> None:
    st.title("Turbine detail")
    st.caption("Everything the system knows and decided about one turbine.")

    plan = context["plan"].sort_values("Priority_Rank").set_index("Turbine_ID")
    labels = {
        turbine: f"#{int(row['Priority_Rank'])} · {turbine} · {row['Site']} · {row['Category']} · {row['Schedule_Status']}"
        for turbine, row in plan.iterrows()
    }
    turbine = st.selectbox("Turbine (by priority rank)", list(labels), format_func=labels.get)
    row = plan.loc[turbine]

    scheduled = row["Schedule_Status"] == "Scheduled"
    slot = f"{row['Scheduled_Day']} {row['Scheduled_Window']}" if scheduled else "Backlog"
    kpi_row(
        [
            ("Priority rank", f"{int(row['Priority_Rank'])} of {len(result.plan)}", None),
            ("Score", f"{row['Final_Priority_Score']:.2f}", "Relative priority index, 0–1."),
            ("Category", row["Category"], None),
            ("Plan", slot, None),
            ("Due", f"{int(row['Recommended_Due_Days'])} days · {row['Due_Status']}", None),
        ]
    )
    st.divider()

    left, right = st.columns([1.3, 1])
    with left:
        st.subheader("Damage records")
        damages = result.dataset.damages
        mine = damages[damages["Turbine_ID"] == turbine].drop(columns=["Turbine_ID"])
        mine = mine.assign(Change=mine["Current_Severity"] - mine["Previous_Severity"])
        show_table(mine.sort_values("Current_Severity", ascending=False))
        st.caption(
            f"Max severity {int(row['Max_Severity'])} · {int(row['Total_Damage_Count'])} damage(s) · "
            f"{int(row['Critical_Count'])} critical · {int(row['Increasing_Count'])} growing"
        )

        rec = result.recommendations.set_index("Turbine_ID").loc[turbine]
        st.subheader(f"{LEVEL_ICON[rec['Level']]} {rec['Headline']}")
        st.markdown("**Why**")
        st.markdown(bullets(rec["Rationale"]))
        st.markdown("**What to do**")
        st.markdown(bullets(rec["Actions"]))

    with right:
        st.subheader("Score contributions")
        weights = result.config["scoring"]["weights"]
        for label, key, column in (
            ("Technical", "technical", "Technical_Contribution"),
            ("Contract", "contract", "Contract_Contribution"),
            ("Due date", "due", "Due_Contribution"),
        ):
            weight = float(weights[key])
            value = float(row[column])
            st.progress(
                min(max(value / weight, 0.0), 1.0) if weight > 0 else 0.0,
                text=f"{label}: {value:.2f} of at most {weight:.2f}",
            )

        st.subheader("Context")
        cap = row["Allowed_Additional_Down"]
        facts = {
            "Site": row["Site"],
            "Region": row["Region"],
            "Customer": row["Customer_Name"],
            "Contract status": row.get("Contract_Status"),
            "Contract period elapsed": f"{100 * float(row['Contract_Time_Factor']):.0f} %",
            "Customer cap per day": "no cap known" if pd.isna(cap) else int(cap),
            "Resource": row["Assigned_Resource"] if scheduled else "—",
        }
        show_table(pd.DataFrame({"Item": list(facts), "Value": [str(v) for v in facts.values()]}))

    slots = result.diagnostics[result.diagnostics["Turbine_ID"].astype(str) == str(turbine)]
    st.subheader("Slot check")
    if slots.empty:
        st.write("The first slot that was checked was feasible; no slot was rejected.")
    else:
        view = slots[["Day", "Window", "Weather_OK", "Contract_OK", "Resource_OK"]].copy()
        for column in ("Weather_OK", "Contract_OK", "Resource_OK"):
            view[column] = view[column].map({True: "✓", False: "✗ blocked"})
        show_table(
            view.rename(
                columns={"Weather_OK": "Weather", "Contract_OK": "Contract cap", "Resource_OK": "Resource"}
            )
        )
        st.caption("Rejected slots only. A slot can fail on more than one constraint.")
