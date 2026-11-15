from __future__ import annotations

import streamlit as st
from components import show_table

from windmaint.weather import flag_rule_conflicts


def render(result, context) -> None:
    st.title("Data quality")
    st.caption(f"Source folder: {context['data_dir']}")
    ds = result.dataset

    st.subheader("Validation")
    if not ds.issues:
        st.success("No issues found in the input tables.")
    for issue in ds.issues:
        (st.error if issue.level == "error" else st.warning)(f"**{issue.table}** — {issue.message}")

    rules = result.config["weather"]["rules"]["default"]
    conflicts = flag_rule_conflicts(ds.weather, rules)
    if not conflicts.empty:
        st.subheader("Weather flag vs. limits")
        st.write(
            f"{len(conflicts)} of {len(ds.weather)} slots have a Workable flag that disagrees "
            f"with the configured limits (wind ≤ {rules['max_wind_speed']:g} m/s). The plan uses "
            f"the **{result.config['weather']['workability_source']}** source; switch it in the "
            "sidebar to see the effect."
        )
        show_table(conflicts)

    st.subheader("Input tables")
    tables = {
        "Turbines": ds.turbines,
        "Damages": ds.damages,
        "Weather": ds.weather,
        "Resources": ds.resources,
        "Planning rules": ds.planning_rules,
        "Site contracts": ds.site_contracts,
        "Customer contracts": ds.customer_contracts,
    }
    for tab, (name, frame) in zip(st.tabs(list(tables)), tables.items()):
        with tab:
            st.caption(f"{len(frame)} rows")
            show_table(frame)
