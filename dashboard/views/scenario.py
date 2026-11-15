from __future__ import annotations

import streamlit as st
from components import rank_range_chart, show_chart, show_table

from windmaint.sensitivity import weight_sensitivity


def render(result, context) -> None:
    st.title("Scenario lab")
    st.caption(
        "How much do the conclusions depend on the method and on the chosen weights? "
        "Always fleet-wide; the sidebar filters do not apply here."
    )

    st.subheader("Greedy vs optimiser")
    table, results = context["compare"]()
    view = table.rename(
        columns={
            "scheduler": "Scheduler",
            "scheduled": "Scheduled",
            "backlog": "Backlog",
            "critical_backlog": "Critical in backlog",
            "late": "Late",
            "priority_covered": "Priority covered",
            "plan_cost": "Plan cost",
        }
    )
    view["Priority covered"] = (100 * view["Priority covered"]).round(0).astype(int).astype(str) + " %"
    view["Plan cost"] = view["Plan cost"].round(1)
    show_table(view)

    cols = ["Turbine_ID", "Category", "Scheduled_Day", "Scheduled_Window", "Due_Status"]
    diff = results["greedy"].plan[cols].merge(
        results["milp"].plan[cols[:1] + cols[2:]], on="Turbine_ID", suffixes=(" (greedy)", " (MILP)")
    )
    changed = diff[
        (diff["Scheduled_Day (greedy)"].fillna("") != diff["Scheduled_Day (MILP)"].fillna(""))
        | (diff["Scheduled_Window (greedy)"].fillna("") != diff["Scheduled_Window (MILP)"].fillna(""))
    ]
    if changed.empty:
        st.success("Both schedulers produce the same plan for this scenario.")
    else:
        st.write(f"{len(changed)} of {len(diff)} jobs are placed differently:")
        show_table(changed.fillna("backlog"))
    st.caption(
        "Greedy gives each job its first feasible slot in priority order. The optimiser "
        "minimises weighted delay plus backlog cost over all jobs at once, so it may move a "
        "high-priority job (inside its due date) to make room for another one."
    )
    st.divider()

    st.subheader("Rank stability under weight uncertainty")
    draws = st.slider("Random weight sets", 200, 2000, 400, step=200)
    ranks, overall = weight_sensitivity(result.plan, result.config, draws=draws)
    a, b, c, d = st.columns(4)
    a.metric(
        "Top 10 stays top 10",
        f"{overall['top10_overlap']:.0%}",
        help="Average share of the current top 10 that is still in the top 10.",
    )
    b.metric("Mean rank shift", f"{overall['mean_abs_rank_shift']:.1f} places")
    c.metric("Identical top 3", f"{overall['identical_top3_share']:.0%}")
    d.metric("Identical full ranking", f"{overall['identical_order_share']:.0%}")

    shown = min(20, len(ranks))
    st.caption(f"Rank range of the {shown} highest-priority turbines.")
    show_chart(rank_range_chart(ranks.head(shown)))
    with st.expander("All turbines"):
        show_table(
            ranks.rename(
                columns={
                    "Turbine_ID": "Turbine",
                    "Base_Rank": "Current rank",
                    "Mean_Rank": "Mean rank",
                    "Best_Rank": "Best",
                    "Worst_Rank": "Worst",
                    "Rank_Stability": "Share unchanged",
                }
            )
        )
    st.caption(
        "Weights are drawn around the current ones (technical roughly ±0.15). On a large fleet "
        "the exact order is almost never reproduced, so read ranks as bands: a turbine ranked "
        "6th is “in the top ten”, not “more urgent than the 7th”."
    )
