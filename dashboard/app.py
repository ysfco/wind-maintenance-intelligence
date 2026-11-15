"""Wind Maintenance Intelligence dashboard.

Run from the repository root:

    streamlit run dashboard/app.py

The app does not read a pre-computed Excel file. It runs the planning engine
itself, so every control in the sidebar is a live what-if scenario.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT / "dashboard"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from views import (  # noqa: E402
    backlog,
    contracts,
    data_quality,
    map_view,
    overview,
    planning,
    recommendations,
    scenario,
    turbine,
)

from windmaint.analytics import apply_filters  # noqa: E402
from windmaint.config import DEFAULTS, ConfigError, load_config  # noqa: E402
from windmaint.io import DataValidationError, load_dataset  # noqa: E402
from windmaint.pipeline import compare_schedulers, run  # noqa: E402

st.set_page_config(page_title="Wind Maintenance Intelligence", page_icon="🌬️", layout="wide")

PAGES = {
    "Overview": overview,
    "Maintenance plan": planning,
    "Backlog root causes": backlog,
    "Recommendations": recommendations,
    "Turbine detail": turbine,
    "Contracts": contracts,
    "Site map": map_view,
    "Scenario lab": scenario,
    "Data quality": data_quality,
}
DATASETS = {
    "Fleet (150 turbines)": ROOT / "data" / "fleet",
    "Minimal case (5 turbines)": ROOT / "data" / "sample",
    "Other folder…": None,
}


def data_stamp(data_dir: str) -> float:
    """Latest modification time of the input files, so edits invalidate the cache."""
    files = list(Path(data_dir).glob("*.csv"))
    return max((f.stat().st_mtime for f in files), default=0.0)


@st.cache_data(show_spinner=False)
def cached_dataset(data_dir: str, stamp: float):
    return load_dataset(data_dir)


@st.cache_data(show_spinner="Running the planning engine…")
def cached_run(data_dir: str, cfg_json: str, stamp: float):
    return run(cfg=json.loads(cfg_json), dataset=load_dataset(data_dir))


@st.cache_data(show_spinner="Comparing schedulers…")
def cached_compare(data_dir: str, cfg_json: str, stamp: float):
    return compare_schedulers(cfg=json.loads(cfg_json), dataset=load_dataset(data_dir))


def choose_data() -> str:
    side = st.sidebar
    choice = side.selectbox("Dataset", list(DATASETS))
    folder = DATASETS[choice]
    if folder is None:
        return side.text_input("Data folder", str(ROOT / "data" / "fleet"))
    return str(folder)


def scenario_controls(dataset) -> dict:
    """Controls that change the plan itself. Returns config overrides."""
    side = st.sidebar
    side.subheader("Scenario")
    sched = DEFAULTS["scheduling"]
    scoring = DEFAULTS["scoring"]

    scheduler = side.selectbox(
        "Scheduler",
        ["greedy", "milp"],
        index=["greedy", "milp"].index(sched["scheduler"]),
        format_func={"greedy": "Greedy (priority order)", "milp": "Optimiser (MILP)"}.get,
        help="Greedy: each job takes its first feasible slot. MILP: best plan overall.",
    )
    available_days = int(dataset.weather["Date"].nunique())
    horizon = min(sched["horizon_days"], available_days)
    if available_days > 1:
        horizon = side.slider("Planning horizon (days)", 1, available_days, horizon)

    with side.expander("Priority weights"):
        st.caption("Relative importance; rescaled to sum to 1.")
        raw = {
            name: st.slider(name.capitalize(), 0, 100, int(round(100 * scoring["weights"][name])))
            for name in ("technical", "contract", "due")
        }
        total = sum(raw.values())
        weights = (
            {name: value / total for name, value in raw.items()} if total else dict(scoring["weights"])
        )
        st.caption(" · ".join(f"{name} {value:.2f}" for name, value in weights.items()))

    with side.expander("Model options"):
        method = st.selectbox(
            "Normalisation",
            ["reference", "minmax"],
            help="reference: fixed bounds, stable scores. minmax: relative to the current fleet.",
        )
        critical_first = st.checkbox(
            "Critical turbines always first",
            value=scoring["ranking"]["critical_first"],
        )
        override = st.checkbox(
            "Allow critical override of contract cap",
            value=DEFAULTS["contracts"]["critical_override"],
            help="Severity-5 turbines may exceed the availability cap (needs customer approval).",
        )
        source = st.selectbox(
            "Weather workability",
            ["flag", "rules"],
            format_func={"flag": "Workable column", "rules": "Wind/rain limits"}.get,
        )

    return {
        "scoring": {
            "weights": weights,
            "normalization": {"method": method},
            "ranking": {"critical_first": critical_first},
        },
        "contracts": {"critical_override": override},
        "scheduling": {"scheduler": scheduler, "horizon_days": horizon},
        "weather": {"workability_source": source},
    }


def view_filters(plan) -> dict:
    """Filters that only narrow what is shown; the plan is always fleet-wide."""
    side = st.sidebar
    side.subheader("Show")
    filters = {
        "Customer_Name": side.multiselect("Customer", sorted(plan["Customer_Name"].unique())),
        "Region": side.multiselect("Region", sorted(plan["Region"].dropna().unique())),
        "Site": side.multiselect("Site", sorted(plan["Site"].dropna().unique())),
        "Category": side.multiselect("Category", ["Critical", "Non-Critical"]),
    }
    side.caption("Empty = all. Filters change the view, not the plan.")
    return filters


def main() -> None:
    st.sidebar.title("🌬️ Wind Maintenance")
    st.sidebar.caption("Decision support — the engineer decides.")
    page = st.sidebar.radio("Navigation", list(PAGES))
    st.sidebar.divider()

    data_dir = choose_data()
    if not Path(data_dir).is_dir():
        st.error(f"Data folder not found: {data_dir}")
        st.stop()

    stamp = data_stamp(data_dir)
    try:
        dataset = cached_dataset(data_dir, stamp)
    except DataValidationError as error:
        st.error("The input data cannot be used. Fix the problems below and reload.")
        for issue in error.issues:
            if issue.level == "error":
                st.write(f"- **{issue.table}**: {issue.message}")
        st.stop()

    try:
        cfg = load_config(overrides=scenario_controls(dataset))
    except ConfigError as error:
        st.error(f"Invalid configuration: {error}")
        st.stop()

    cfg_json = json.dumps(cfg, sort_keys=True)
    result = cached_run(data_dir, cfg_json, stamp)
    st.sidebar.divider()
    filters = view_filters(result.plan)
    plan = apply_filters(result.plan, filters)
    if plan.empty:
        st.warning("No turbine matches the selected filters.")
        st.stop()

    context = {
        "data_dir": data_dir,
        "plan": plan,  # filtered view of result.plan
        "filtered": len(plan) < len(result.plan),
        "compare": lambda: cached_compare(data_dir, cfg_json, stamp),
    }
    PAGES[page].render(result, context)


main()
