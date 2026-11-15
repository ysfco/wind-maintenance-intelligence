from __future__ import annotations

import pytest
from conftest import random_dataset

from windmaint.pipeline import compare_schedulers, run


def by_turbine(result):
    return result.plan.set_index("Turbine_ID")


def assert_feasible(result):
    """No plan may violate weather, resource or contract constraints."""
    plan, ds = result.plan, result.dataset
    scheduled = plan[plan["Schedule_Status"] == "Scheduled"]
    workable = {
        (r.Site, r.Date, r.Time_Window): bool(r.Workable) for r in ds.weather.itertuples(index=False)
    }
    for row in scheduled.itertuples(index=False):
        assert workable[(row.Site, row.Scheduled_Day, row.Scheduled_Window)]

    capacity = dict(zip(ds.resources["Resource_ID"], ds.resources["Daily_Capacity"]))
    regions = {r.Resource_ID: set(str(r.Region).split("|")) for r in ds.resources.itertuples(index=False)}
    for (resource, _day), count in scheduled.groupby(["Assigned_Resource", "Scheduled_Day"]).size().items():
        assert count <= capacity[resource]
    for row in scheduled.itertuples(index=False):
        assert row.Region in regions[row.Assigned_Resource]

    counted = scheduled[~scheduled["Contract_Override"]]
    caps = plan.dropna(subset=["Allowed_Additional_Down"]).groupby("Customer_Name")[
        "Allowed_Additional_Down"
    ].first()
    for (customer, _day), count in counted.groupby(["Customer_Name", "Scheduled_Day"]).size().items():
        if customer in caps.index:
            assert count <= caps[customer]


def test_greedy_reproduces_the_legacy_plan(sample, legacy_cfg):
    plan = by_turbine(run(cfg=legacy_cfg, dataset=sample, scheduler="greedy"))
    assert plan.loc["T2", ["Scheduled_Day", "Scheduled_Window", "Assigned_Resource"]].tolist() == [
        "2026-04-15",
        "Morning",
        "Team_4",
    ]
    assert plan.loc["T3", ["Scheduled_Day", "Scheduled_Window"]].tolist() == ["2026-04-16", "Morning"]
    assert plan.loc[["T5", "T4", "T1"], "Schedule_Status"].tolist() == ["Backlog"] * 3
    assert plan.loc["T2", "Due_Status"] == "On Time"


def test_milp_finds_the_better_plan_on_the_sample(sample, cfg):
    """Greedy gives T2 the only slot T1 could use; the optimiser re-sequences."""
    table, results = compare_schedulers(cfg=cfg, dataset=sample)
    kpi = table.set_index("scheduler")
    assert kpi.loc["greedy", "scheduled"] == 2
    assert kpi.loc["milp", "scheduled"] == 3
    assert kpi.loc["milp", "plan_cost"] < kpi.loc["greedy", "plan_cost"]
    milp = by_turbine(results["milp"])
    assert milp.loc["T1", "Scheduled_Day"] == "2026-04-15"
    assert results["milp"].kpis["solver_status"] == "optimal"
    for result in results.values():
        assert_feasible(result)


@pytest.mark.parametrize("seed", range(8))
def test_plans_are_feasible_and_milp_is_never_worse(seed, cfg):
    cfg["scheduling"]["horizon_days"] = 4
    table, results = compare_schedulers(cfg=cfg, dataset=random_dataset(seed))
    for result in results.values():
        assert_feasible(result)
        assert result.kpis["scheduled"] + result.kpis["backlog"] == result.kpis["jobs"]
    kpi = table.set_index("scheduler")
    assert kpi.loc["milp", "plan_cost"] <= kpi.loc["greedy", "plan_cost"] + 1e-6


def test_zero_contract_budget_blocks_every_slot(sample, cfg):
    """Customer Y: floor(80 * 2 %) = 1 allowed, 1 already down -> no budget."""
    result = run(cfg=cfg, dataset=sample)
    plan = by_turbine(result)
    assert plan.loc["T5", "Allowed_Additional_Down"] == 0
    assert plan.loc["T5", "Schedule_Status"] == "Backlog"
    assert plan.loc["T5", "Contract_Blocks"] == 6
    assert plan.loc["T5", "Primary_Blocker"] == "Contract availability cap"


def test_critical_override_schedules_the_blocked_turbine(sample, cfg):
    cfg["contracts"]["critical_override"] = True
    for scheduler in ("greedy", "milp"):
        result = run(cfg=cfg, dataset=sample, scheduler=scheduler)
        plan = by_turbine(result)
        assert plan.loc["T5", "Schedule_Status"] == "Scheduled"
        assert bool(plan.loc["T5", "Contract_Override"])
        assert_feasible(result)


def test_horizon_limits_planning_days(sample, cfg):
    cfg["scheduling"]["horizon_days"] = 1
    result = run(cfg=cfg, dataset=sample)
    assert result.kpis["planning_days"] == ["2026-04-15"]
    assert set(result.plan["Scheduled_Day"].dropna()) <= {"2026-04-15"}


def test_resource_capacity_is_shared_within_a_region(sample, cfg):
    # T1 and T4 are both North (Team_1, capacity 1). Make site C workable.
    sample.weather.loc[sample.weather["Site"] == "C", "Workable"] = True
    sample.customer_contracts["Current_Down_Turbines"] = 0
    result = run(cfg=cfg, dataset=sample)
    plan = by_turbine(result)
    assert plan.loc["T4", "Scheduled_Day"] != plan.loc["T1", "Scheduled_Day"]
    assert_feasible(result)


def test_turbine_in_region_without_resource_is_explained(sample, cfg):
    sample.resources = sample.resources[sample.resources["Region"] != "East"]
    plan = by_turbine(run(cfg=cfg, dataset=sample))
    assert plan.loc["T2", "Schedule_Status"] == "Backlog"
    assert "no resource covers region" in plan.loc["T2", "Skip_Reason"]
