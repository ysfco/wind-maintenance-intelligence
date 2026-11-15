"""Scheduling engine: "what can actually be done, and when?".

A high priority does not guarantee a slot. A job is placed in a
(day, window) slot only if three hard constraints hold:

* **weather**  - the slot is workable at the turbine's site;
* **contract** - the customer's daily availability cap is not exceeded;
* **resource** - a crew covering the turbine's region has capacity that day.

This module defines the shared problem model, the constraint check used by
every scheduler, the plan cost function, and the greedy baseline. The exact
optimiser lives in :mod:`windmaint.optimizer`.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Mapping

import pandas as pd

from windmaint.io import Dataset
from windmaint.weather import derive_workability

# Block codes, in display order.
WEATHER = "WEATHER"
NO_WEATHER_DATA = "NO_WEATHER_DATA"
CONTRACT = "CONTRACT"
RESOURCE = "RESOURCE"
NO_RESOURCE = "NO_RESOURCE"

BLOCK_LABELS = {
    WEATHER: "weather not workable",
    NO_WEATHER_DATA: "no weather data",
    CONTRACT: "contract availability limit exceeded",
    RESOURCE: "resource capacity full",
    NO_RESOURCE: "no resource covers region",
}


@dataclass(frozen=True)
class Slot:
    day: str  # ISO date
    day_index: int  # 1-based position in the planning horizon
    window: str
    window_index: int  # 0-based position inside the day

    @property
    def label(self) -> str:
        return f"{self.day} {self.window}"


@dataclass
class Problem:
    jobs: pd.DataFrame  # scored summary in processing order
    slots: list[Slot]
    days: list[str]
    workable: dict[tuple[str, str, str], bool]  # (site, day, window); absent = no data
    capacity: dict[str, int]  # resource -> jobs per day
    eligible: dict[str, list[str]]  # turbine -> resources covering its region
    customer_cap: dict[str, int]  # customer -> additional turbines down per day
    override: set[str] = field(default_factory=set)  # turbines exempt from the cap


@dataclass
class State:
    resource_used: Counter = field(default_factory=Counter)  # (resource, day)
    customer_used: Counter = field(default_factory=Counter)  # (customer, day)


@dataclass
class ScheduleResult:
    method: str
    assignments: pd.DataFrame
    diagnostics: pd.DataFrame
    cost: float
    solver_status: str = "ok"


def build_problem(jobs: pd.DataFrame, ds: Dataset, cfg: Mapping[str, Any]) -> Problem:
    sched = cfg["scheduling"]
    weather = ds.weather
    if cfg["weather"]["workability_source"] == "rules" or "Workable" not in weather.columns:
        weather = weather.assign(
            Workable=derive_workability(weather, cfg["weather"]["rules"]["default"])
        )

    days = sorted(weather["Date"].dropna().unique())[: int(sched["horizon_days"])]
    slots = [
        Slot(day, d_idx, window, w_idx)
        for d_idx, day in enumerate(days, start=1)
        for w_idx, window in enumerate(sched["time_windows"])
    ]
    workable = {
        (row.Site, row.Date, row.Time_Window): bool(row.Workable)
        for row in weather.itertuples(index=False)
    }

    capacity = {
        str(row.Resource_ID): int(row.Daily_Capacity) for row in ds.resources.itertuples(index=False)
    }
    coverage = {
        str(row.Resource_ID): {part.strip() for part in str(row.Region).split("|")}
        for row in ds.resources.itertuples(index=False)
    }
    # Dedicated crews first: a crew that covers one region is preferred over a
    # multi-region or roaming one, which keeps the flexible crews free.
    def flexibility(res: str) -> tuple[int, str]:
        regions = coverage[res]
        return (10_000 if "*" in regions else len(regions), res)

    eligible = {
        str(job.Turbine_ID): sorted(
            (res for res, regions in coverage.items() if "*" in regions or job.Region in regions),
            key=flexibility,
        )
        for job in jobs.itertuples(index=False)
    }

    caps = jobs.dropna(subset=["Allowed_Additional_Down"]).drop_duplicates("Customer_Name")
    customer_cap = {
        row.Customer_Name: int(row.Allowed_Additional_Down) for row in caps.itertuples(index=False)
    }

    override: set[str] = set()
    if cfg["contracts"]["critical_override"]:
        minimum = float(cfg["contracts"]["override_min_severity"])
        override = set(jobs.loc[jobs["Max_Severity"] >= minimum, "Turbine_ID"].astype(str))

    return Problem(jobs, slots, list(days), workable, capacity, eligible, customer_cap, override)


def evaluate_slot(
    problem: Problem, state: State, turbine: str, site: str, customer: str, slot: Slot
) -> tuple[list[str], str | None]:
    """Check *all* constraints for one job in one slot.

    Returns ``(blocks, resource)``. Unlike the legacy script this does not stop
    at the first failing check, so a slot blocked by weather *and* contract is
    reported as such - which is what root-cause analysis needs.
    """
    blocks: list[str] = []

    flag = problem.workable.get((site, slot.day, slot.window))
    if flag is None:
        blocks.append(NO_WEATHER_DATA)
    elif not flag:
        blocks.append(WEATHER)

    cap = problem.customer_cap.get(customer)
    if (
        cap is not None
        and turbine not in problem.override
        and state.customer_used[(customer, slot.day)] >= cap
    ):
        blocks.append(CONTRACT)

    resource: str | None = None
    candidates = problem.eligible.get(turbine, [])
    if not candidates:
        blocks.append(NO_RESOURCE)
    else:
        # ``candidates`` is ordered most-dedicated first; take the first with room.
        resource = next(
            (
                res
                for res in candidates
                if problem.capacity[res] - state.resource_used[(res, slot.day)] > 0
            ),
            None,
        )
        if resource is None:
            blocks.append(RESOURCE)

    return blocks, resource


def _diagnostic_row(turbine: str, slot: Slot, blocks: list[str]) -> dict[str, Any]:
    return {
        "Turbine_ID": turbine,
        "Day": slot.day,
        "Day_Index": slot.day_index,
        "Window": slot.window,
        "Weather_OK": WEATHER not in blocks and NO_WEATHER_DATA not in blocks,
        "Contract_OK": CONTRACT not in blocks,
        "Resource_OK": RESOURCE not in blocks and NO_RESOURCE not in blocks,
        "Blocks": "+".join(blocks),
    }


DIAGNOSTIC_COLUMNS = [
    "Turbine_ID",
    "Day",
    "Day_Index",
    "Window",
    "Weather_OK",
    "Contract_OK",
    "Resource_OK",
    "Blocks",
]


def job_weights(jobs: pd.DataFrame, cfg: Mapping[str, Any]) -> pd.Series:
    """Importance of each job in the plan cost, indexed by turbine id."""
    weights = jobs["Final_Priority_Score"].astype(float).copy()
    if cfg["scoring"]["ranking"]["critical_first"]:
        bonus = float(cfg["scheduling"]["milp"]["critical_weight_bonus"])
        weights = weights + bonus * (jobs["Category"] == "Critical")
    weights.index = jobs["Turbine_ID"].astype(str)
    return weights


def slot_cost(slot: Slot, due_days: float, cfg: Mapping[str, Any]) -> float:
    """Delay cost of doing a job in ``slot``, in day-equivalents."""
    m = cfg["scheduling"]["milp"]
    lateness = max(0.0, slot.day_index - float(due_days))
    return (
        (slot.day_index - 1)
        + float(m["window_tiebreak"]) * slot.window_index
        + float(m["late_penalty_per_day"]) * lateness
    )


def backlog_cost(problem: Problem, due_days: float, cfg: Mapping[str, Any]) -> float:
    """Cost of leaving a job unscheduled, in day-equivalents.

    Defined as the cost of the job's worst slot inside the horizon plus the
    backlog penalty, so that "scheduled late" is always better than "not
    scheduled" - also on long horizons where lateness penalties add up.
    """
    penalty = float(cfg["scheduling"]["milp"]["backlog_penalty_days"])
    if not problem.slots:
        return penalty
    return slot_cost(problem.slots[-1], due_days, cfg) + penalty


def plan_cost(
    problem: Problem, assigned: Mapping[str, tuple[Slot, str]], cfg: Mapping[str, Any]
) -> float:
    """Objective shared by all schedulers (lower is better).

    ``sum_j weight_j * (delay cost of its slot, or backlog cost)``.
    """
    weights = job_weights(problem.jobs, cfg)
    due = dict(zip(problem.jobs["Turbine_ID"].astype(str), problem.jobs["Recommended_Due_Days"]))
    total = 0.0
    for turbine, weight in weights.items():
        if turbine in assigned:
            total += weight * slot_cost(assigned[turbine][0], due[turbine], cfg)
        else:
            total += weight * backlog_cost(problem, due[turbine], cfg)
    return float(total)


def finalise(
    problem: Problem,
    assigned: Mapping[str, tuple[Slot, str]],
    diagnostics: list[dict[str, Any]],
    method: str,
    cfg: Mapping[str, Any],
    solver_status: str = "ok",
) -> ScheduleResult:
    """Turn raw assignments into the result tables."""
    customers = dict(zip(problem.jobs["Turbine_ID"].astype(str), problem.jobs["Customer_Name"]))
    used: Counter = Counter()
    for turbine, (slot, _) in assigned.items():
        if turbine not in problem.override:
            used[(customers[turbine], slot.day)] += 1

    rows = []
    for job in problem.jobs.itertuples(index=False):
        turbine = str(job.Turbine_ID)
        if turbine in assigned:
            slot, resource = assigned[turbine]
            cap = problem.customer_cap.get(job.Customer_Name)
            over = (
                turbine in problem.override
                and cap is not None
                and used[(job.Customer_Name, slot.day)] >= cap
            )
            rows.append(
                {
                    "Turbine_ID": job.Turbine_ID,
                    "Schedule_Status": "Scheduled",
                    "Scheduled_Day": slot.day,
                    "Scheduled_Day_Index": slot.day_index,
                    "Scheduled_Window": slot.window,
                    "Assigned_Resource": resource,
                    "Contract_Override": bool(over),
                }
            )
        else:
            rows.append(
                {
                    "Turbine_ID": job.Turbine_ID,
                    "Schedule_Status": "Backlog",
                    "Scheduled_Day": None,
                    "Scheduled_Day_Index": None,
                    "Scheduled_Window": None,
                    "Assigned_Resource": None,
                    "Contract_Override": False,
                }
            )
    assignments = pd.DataFrame(rows)
    assignments["Scheduled_Day_Index"] = assignments["Scheduled_Day_Index"].astype("Int64")
    return ScheduleResult(
        method=method,
        assignments=assignments,
        diagnostics=pd.DataFrame(diagnostics, columns=DIAGNOSTIC_COLUMNS),
        cost=plan_cost(problem, assigned, cfg),
        solver_status=solver_status,
    )


def schedule_greedy(problem: Problem, cfg: Mapping[str, Any]) -> ScheduleResult:
    """Baseline: jobs in priority order, each takes its first feasible slot.

    Fast and easy to explain, but myopic: an early assignment can consume a
    contract or crew slot that a later job needed more (see ``optimizer``).
    """
    state = State()
    assigned: dict[str, tuple[Slot, str]] = {}
    diagnostics: list[dict[str, Any]] = []

    for job in problem.jobs.itertuples(index=False):
        turbine = str(job.Turbine_ID)
        for slot in problem.slots:
            blocks, resource = evaluate_slot(
                problem, state, turbine, job.Site, job.Customer_Name, slot
            )
            if blocks:
                diagnostics.append(_diagnostic_row(turbine, slot, blocks))
                continue
            assert resource is not None
            assigned[turbine] = (slot, resource)
            state.resource_used[(resource, slot.day)] += 1
            if turbine not in problem.override:
                state.customer_used[(job.Customer_Name, slot.day)] += 1
            break

    return finalise(problem, assigned, diagnostics, "greedy", cfg)


def explain_backlog(
    problem: Problem, assigned: Mapping[str, tuple[Slot, str]]
) -> list[dict[str, Any]]:
    """Slot-by-slot blocks for unscheduled jobs, measured against a finished plan."""
    state = State()
    customers = dict(zip(problem.jobs["Turbine_ID"].astype(str), problem.jobs["Customer_Name"]))
    for turbine, (slot, resource) in assigned.items():
        state.resource_used[(resource, slot.day)] += 1
        if turbine not in problem.override:
            state.customer_used[(customers[turbine], slot.day)] += 1

    rows: list[dict[str, Any]] = []
    for job in problem.jobs.itertuples(index=False):
        turbine = str(job.Turbine_ID)
        if turbine in assigned:
            continue
        for slot in problem.slots:
            blocks, _ = evaluate_slot(problem, state, turbine, job.Site, job.Customer_Name, slot)
            # In an optimal plan a free slot can remain unused only if using it
            # is not worth its cost; report it rather than hide it.
            rows.append(_diagnostic_row(turbine, slot, blocks or ["NOT_SELECTED"]))
    return rows
