"""Checks on the 150-turbine demo fleet and the fleet-scale views."""

from __future__ import annotations

import importlib.util
import time

import pandas as pd
import pytest
from conftest import ROOT
from test_scheduling import assert_feasible

from windmaint.analytics import (
    apply_filters,
    blockers_by_site,
    dispatch_table,
    jobs_per_day,
    subset_kpis,
)
from windmaint.io import load_dataset
from windmaint.pipeline import compare_schedulers, run
from windmaint.scheduling import backlog_cost, build_problem, slot_cost
from windmaint.scoring import build_summary, score
from windmaint.sensitivity import weight_sensitivity
from windmaint.weather import flag_rule_conflicts

FLEET = ROOT / "data" / "fleet"


@pytest.fixture(scope="module")
def fleet():
    return load_dataset(FLEET)


@pytest.fixture(scope="module")
def both(fleet):
    from windmaint.config import load_config

    started = time.perf_counter()
    table, results = compare_schedulers(cfg=load_config(), dataset=fleet)
    return table.set_index("scheduler"), results, time.perf_counter() - started


def test_fleet_is_consistent(fleet, cfg):
    assert not [i for i in fleet.issues if i.level == "error"]
    assert len(fleet.turbines) == 150
    assert fleet.turbines["Site"].nunique() == 12
    assert fleet.weather["Date"].nunique() == 10
    # contract sizes match the turbines listed for each customer
    listed = (
        fleet.turbines.merge(fleet.site_contracts, on="Site").groupby("Customer_Name").size()
    )
    declared = fleet.customer_contracts.set_index("Customer_Name")["Total_Turbines"]
    assert listed.to_dict() == declared.to_dict()
    # the Workable flag was derived from the measurements, so they never disagree
    assert flag_rule_conflicts(fleet.weather, cfg["weather"]["rules"]["default"]).empty


def test_both_schedulers_are_feasible_and_fast(both):
    kpi, results, seconds = both
    for result in results.values():
        assert_feasible(result)
        assert result.kpis["jobs"] == result.plan["Turbine_ID"].nunique()
    assert kpi.loc["milp", "plan_cost"] <= kpi.loc["greedy", "plan_cost"]
    assert results["milp"].kpis["solver_status"] == "optimal"
    assert seconds < 20


def test_optimiser_schedules_every_schedulable_critical_turbine_on_time(both):
    kpi, results, _ = both
    plan = results["milp"].plan
    stuck = plan[(plan["Category"] == "Critical") & (plan["Schedule_Status"] == "Backlog")]
    # Only turbines of the customer without any outage budget may remain.
    assert (stuck["Allowed_Additional_Down"] == 0).all()
    assert kpi.loc["milp", "late"] == 0
    assert kpi.loc["milp", "critical_backlog"] <= kpi.loc["greedy", "critical_backlog"]


def test_backlog_always_costs_more_than_any_slot(fleet, cfg):
    summary, _ = build_summary(fleet, cfg)
    problem = build_problem(score(summary, fleet.planning_rules, cfg), fleet, cfg)
    for due in (5, 10, 14, 30):
        worst = max(slot_cost(slot, due, cfg) for slot in problem.slots)
        assert backlog_cost(problem, due, cfg) > worst


def test_view_filters_and_kpis(both):
    _, results, _ = both
    plan = results["greedy"].plan
    assert len(apply_filters(plan, {"Customer_Name": [], "Region": []})) == len(plan)
    marmara = apply_filters(plan, {"Region": ["Marmara"], "Category": ["Critical"]})
    assert set(marmara["Region"]) == {"Marmara"} and set(marmara["Category"]) == {"Critical"}

    whole = subset_kpis(plan)
    assert {k: whole[k] for k in ("jobs", "scheduled", "backlog", "critical_backlog", "late")} == {
        k: results["greedy"].kpis[k] for k in ("jobs", "scheduled", "backlog", "critical_backlog", "late")
    }
    parts = [subset_kpis(group) for _, group in plan.groupby("Customer_Name")]
    assert sum(p["scheduled"] for p in parts) == whole["scheduled"]


def test_plan_aggregations(both):
    _, results, _ = both
    result = results["greedy"]
    plan, days = result.plan, result.kpis["planning_days"]

    per_day = jobs_per_day(plan, days)
    assert per_day["Day"].tolist() == days
    assert int(per_day[["Critical", "Non-Critical"]].to_numpy().sum()) == result.kpis["scheduled"]

    dispatch = dispatch_table(plan, days)
    text = " ".join(dispatch.drop(columns="Resource").to_numpy().ravel())
    for turbine in plan.loc[plan["Schedule_Status"] == "Scheduled", "Turbine_ID"]:
        assert text.count(turbine) == 1

    by_site = blockers_by_site(plan)
    assert int(by_site["Backlog"].sum()) == result.kpis["backlog"]
    assert blockers_by_site(plan[plan["Schedule_Status"] == "Scheduled"]).empty


def test_ranking_is_a_band_not_an_exact_order(both):
    _, results, _ = both
    result = results["greedy"]
    _, overall = weight_sensitivity(result.plan, result.config, draws=200, seed=3)
    assert overall["identical_order_share"] < 0.2  # exact order is fragile at this size
    assert overall["top10_overlap"] > 0.8  # but the top ten is robust


def test_generator_builds_a_valid_fleet(tmp_path):
    spec = importlib.util.spec_from_file_location("generate_fleet", ROOT / "scripts" / "generate_fleet.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    first = module.build(seed=11, start="2026-05-04", days=5, damaged_share=0.5)
    again = module.build(seed=11, start="2026-05-04", days=5, damaged_share=0.5)
    for name in first:
        pd.testing.assert_frame_equal(first[name], again[name])
    for name, frame in first.items():
        frame.to_csv(tmp_path / f"{name}.csv", sep=";", index=False)

    result = run(tmp_path, scheduler="milp")
    assert result.kpis["planning_days"][0] == "2026-05-04" and len(result.kpis["planning_days"]) == 5
    assert result.kpis["scheduled"] > 0
    assert_feasible(result)
