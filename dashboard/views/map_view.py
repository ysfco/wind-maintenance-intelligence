from __future__ import annotations

import pydeck as pdk
import streamlit as st
from components import STATUS, show_map, show_table

from windmaint.analytics import site_overview

_COLOURS = {
    "Critical backlog": STATUS["critical"],
    "Attention": STATUS["warning"],
    "On plan": STATUS["good"],
}


def _rgb(hex_colour: str) -> list[int]:
    value = hex_colour.lstrip("#")
    return [int(value[i : i + 2], 16) for i in (0, 2, 4)]


def render(result, context) -> None:
    st.title("Site map")
    st.caption("Site-level workload, plan status and snapshot weather.")

    sites = site_overview(result)
    if "Latitude" not in sites.columns:
        st.info("Add site_locations.csv (Site, Latitude, Longitude) to the data folder to see the map.")
    else:
        located = sites.dropna(subset=["Latitude", "Longitude"]).copy()
        located = located[
            located["Latitude"].between(-90, 90) & located["Longitude"].between(-180, 180)
        ]
        if located.empty:
            st.warning("No site has valid coordinates.")
        else:
            located["colour"] = located["Site_Status"].map(lambda s: _rgb(_COLOURS[s]))
            located["Max_Priority"] = located["Max_Priority"].round(2)
            for column in ("Wind_Speed", "Rain_Risk", "Temperature", "Weather_Status"):
                if column not in located.columns:
                    located[column] = "n/a"
            layer = pdk.Layer(
                "ScatterplotLayer",
                data=located,
                get_position="[Longitude, Latitude]",
                get_fill_color="colour",
                get_line_color=[255, 255, 255],
                stroked=True,
                line_width_min_pixels=2,
                get_radius=25000,
                radius_min_pixels=8,
                pickable=True,
                auto_highlight=True,
            )
            deck = pdk.Deck(
                layers=[layer],
                initial_view_state=pdk.ViewState(
                    latitude=float(located["Latitude"].mean()),
                    longitude=float(located["Longitude"].mean()),
                    zoom=5,
                ),
                map_style=None,
                tooltip={
                    "html": (
                        "<b>Site {Site}</b> — {Customer_Name}<br/>"
                        "Status: {Site_Status}<br/>"
                        "Jobs: {Jobs} · critical: {Critical}<br/>"
                        "Scheduled: {Scheduled} · backlog: {Backlog}<br/>"
                        "Highest priority: {Max_Priority}<hr/>"
                        "Wind: {Wind_Speed} m/s · rain: {Rain_Risk} · {Temperature} °C<br/>"
                        "Weather: {Weather_Status}"
                    )
                },
            )
            st.caption("🔴 critical turbine in backlog · 🟠 backlog or critical work · 🟢 on plan")
            show_map(deck)

    st.subheader("Site summary")
    show_table(sites.drop(columns=["Latitude", "Longitude"], errors="ignore").round(2))
    st.caption(
        "Weather status here comes from the site snapshot (site_weather.csv). The scheduler "
        "uses the slot-level table (weather.csv) instead."
    )
