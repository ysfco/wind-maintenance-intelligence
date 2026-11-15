from __future__ import annotations

import streamlit as st
from components import LEVEL_ICON, bullets, kpi_row, show_table

from windmaint.recommendations import LEVELS

_BOX = {"Escalate": st.error, "Action": st.warning, "On track": st.success}


def render(result, context) -> None:
    st.title("Recommendations")
    st.caption(
        "Rule-based explanations generated from the plan. This is an expert-system layer, "
        "not a machine-learning model."
    )

    plan = context["plan"].set_index("Turbine_ID")
    recs = result.recommendations[result.recommendations["Turbine_ID"].isin(plan.index)]
    counts = recs["Level"].value_counts()
    kpi_row([(f"{LEVEL_ICON[level]} {level}", int(counts.get(level, 0)), None) for level in LEVELS])

    left, right = st.columns([2, 1])
    chosen = left.multiselect("Levels", LEVELS, default=["Escalate", "Action"])
    shown = recs[recs["Level"].isin(chosen)]
    limit = min(10, len(shown))
    if len(shown) > 10:
        limit = right.slider("Cards shown", 5, min(50, len(shown)), 10)

    if shown.empty:
        st.info("Nothing to show for the selected levels.")
        return
    if limit < len(shown):
        st.caption(f"Showing the {limit} most urgent of {len(shown)}; the table below lists all.")

    for rec in shown.head(limit).itertuples(index=False):
        row = plan.loc[rec.Turbine_ID]
        _BOX[rec.Level](
            f"{LEVEL_ICON[rec.Level]} **{rec.Turbine_ID}** · site {row['Site']} · "
            f"{row['Customer_Name']} · {row['Category']} — {rec.Headline}"
        )
        why, what = st.columns(2)
        with why:
            st.markdown("**Why**")
            st.markdown(bullets(rec.Rationale))
        with what:
            st.markdown("**What to do**")
            st.markdown(bullets(rec.Actions))

    st.subheader("All recommendations in this selection")
    table = shown.copy()
    table["Actions"] = table["Actions"].str.replace("\n", " ")
    show_table(table[["Turbine_ID", "Level", "Headline", "Actions"]], height=380)
    st.download_button(
        "Download as CSV",
        data=shown.to_csv(index=False, sep=";").encode("utf-8-sig"),
        file_name="recommendations.csv",
        mime="text/csv",
    )
