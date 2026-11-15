"""Shared UI helpers: colours, layout-safe wrappers and the two core charts."""

from __future__ import annotations

import inspect

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# Categorical series colours (fixed order; validated for colour-vision
# deficiency separation). Status colours are reserved for state, not series.
SERIES = {"first": "#2a78d6", "second": "#eb6834", "third": "#1baf7a"}
STATUS = {"critical": "#d03b3b", "warning": "#fab219", "good": "#0ca30c", "muted": "#898781"}
LEVEL_ICON = {"Escalate": "🔴", "Action": "🟠", "On track": "🟢"}


def _fill(func) -> dict:
    """Keyword that makes an element span its container on any Streamlit version.

    Newer releases default to ``width="stretch"``; older ones need
    ``use_container_width=True`` (deprecated later). Inspecting the signature
    avoids pinning the app to one side of that change.
    """
    try:
        width = inspect.signature(func).parameters.get("width")
    except (TypeError, ValueError):
        return {}
    if width is not None and width.default == "stretch":
        return {}
    return {"use_container_width": True}


def show_chart(fig: go.Figure) -> None:
    st.plotly_chart(fig, **_fill(st.plotly_chart))


def show_table(frame: pd.DataFrame, **kwargs) -> None:
    st.dataframe(frame, hide_index=True, **_fill(st.dataframe), **kwargs)


def show_map(deck) -> None:
    st.pydeck_chart(deck, **_fill(st.pydeck_chart))


def kpi_row(items: list[tuple[str, object, str | None]]) -> None:
    """``items`` = (label, value, help text)."""
    for column, (label, value, hint) in zip(st.columns(len(items)), items):
        column.metric(label, value, help=hint)


def bullets(text: str) -> str:
    return "\n".join(f"- {line}" for line in str(text).split("\n") if line.strip())


def _base_layout(fig: go.Figure, rows: int, x_title: str) -> go.Figure:
    fig.update_layout(
        barmode="stack",
        bargap=0.35,
        height=max(260, 44 * rows + 110),
        margin=dict(l=10, r=30, t=10, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        xaxis=dict(title=x_title, zeroline=False),
        yaxis=dict(autorange="reversed", type="category"),
        hovermode="closest",
    )
    return fig


def contribution_chart(plan: pd.DataFrame) -> go.Figure:
    """Final priority score per turbine, split into its three real contributions."""
    plan = plan.sort_values("Priority_Rank")
    labels = [
        f"{row.Turbine_ID}{' ●' if row.Category == 'Critical' else ''}"
        for row in plan.itertuples(index=False)
    ]
    fig = go.Figure()
    for name, column, colour in (
        ("Technical condition", "Technical_Contribution", SERIES["first"]),
        ("Contract pressure", "Contract_Contribution", SERIES["second"]),
        ("Due-date urgency", "Due_Contribution", SERIES["third"]),
    ):
        fig.add_bar(
            y=labels,
            x=plan[column].round(4),
            name=name,
            orientation="h",
            marker_color=colour,
            hovertemplate="%{y}<br>" + name + ": %{x:.3f}<extra></extra>",
        )
    fig.add_scatter(
        y=labels,
        x=plan["Final_Priority_Score"],
        mode="text",
        text=[f"  {value:.2f}" for value in plan["Final_Priority_Score"]],
        textposition="middle right",
        showlegend=False,
        hoverinfo="skip",
    )
    fig = _base_layout(fig, len(plan), "Final priority score (0–1)")
    fig.update_xaxes(range=[0, max(1.0, float(plan["Final_Priority_Score"].max()) + 0.1)])
    return fig


def jobs_per_day_chart(per_day: pd.DataFrame) -> go.Figure:
    """Scheduled jobs per planning day; critical turbines in the status colour."""
    fig = go.Figure()
    for name, colour in (("Critical", STATUS["critical"]), ("Non-Critical", SERIES["first"])):
        fig.add_bar(
            x=per_day["Day"],
            y=per_day[name],
            name=name,
            marker_color=colour,
            hovertemplate="%{x}<br>" + name + ": %{y} job(s)<extra></extra>",
        )
    fig.update_layout(
        barmode="stack",
        bargap=0.35,
        height=300,
        margin=dict(l=10, r=10, t=10, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        xaxis=dict(type="category"),
        yaxis=dict(title="Jobs", dtick=1, zeroline=False),
    )
    return fig


def blocker_chart(backlog: pd.DataFrame, label_column: str = "Turbine_ID") -> go.Figure:
    """Blocked slots by constraint, one bar per turbine or per site."""
    labels = backlog[label_column].astype(str).tolist()
    fig = go.Figure()
    for name, column, colour in (
        ("Weather", "Weather_Blocks", SERIES["first"]),
        ("Contract cap", "Contract_Blocks", SERIES["second"]),
        ("Resource capacity", "Resource_Blocks", SERIES["third"]),
    ):
        fig.add_bar(
            y=labels,
            x=backlog[column],
            name=name,
            orientation="h",
            marker_color=colour,
            hovertemplate="%{y}<br>" + name + ": %{x} slot(s)<extra></extra>",
        )
    return _base_layout(fig, len(backlog), "Slots blocked (a slot can fail on several constraints)")


def rank_range_chart(table: pd.DataFrame) -> go.Figure:
    """Best-to-worst rank per turbine under weight uncertainty, base rank marked."""
    labels = table["Turbine_ID"].astype(str).tolist()
    fig = go.Figure()
    xs: list[float | None] = []
    ys: list[str | None] = []
    for label, row in zip(labels, table.itertuples(index=False)):
        xs += [row.Best_Rank, row.Worst_Rank, None]
        ys += [label, label, None]
    fig.add_scatter(
        x=xs, y=ys, mode="lines", name="Rank range", line=dict(color=STATUS["muted"], width=6),
        hoverinfo="skip",
    )
    fig.add_scatter(
        x=table["Base_Rank"],
        y=labels,
        mode="markers",
        name="Rank with current weights",
        marker=dict(color=SERIES["first"], size=12, line=dict(color="white", width=2)),
        customdata=table[["Best_Rank", "Worst_Rank", "Rank_Stability"]].to_numpy(),
        hovertemplate=(
            "%{y}<br>current rank %{x}<br>range %{customdata[0]}–%{customdata[1]}"
            "<br>unchanged in %{customdata[2]:.0%} of draws<extra></extra>"
        ),
    )
    fig = _base_layout(fig, len(table), "Priority rank (1 = first)")
    fig.update_layout(barmode=None)
    fig.update_xaxes(dtick=1)
    return fig
