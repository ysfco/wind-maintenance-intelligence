"""Exact scheduler: mixed-integer linear programme solved with HiGHS (SciPy).

Decision variable
    ``x[j, s, r] = 1`` if job ``j`` is done in slot ``s`` by resource ``r``.
    Variables exist only where the slot is workable at the job's site and the
    resource covers the job's region, so weather is enforced by construction.

Objective (minimise)
    ``sum_j w_j * ( sum_{s,r} cost(j, s) * x[j,s,r] + B_j * (1 - sum_{s,r} x[j,s,r]) )``

    ``w_j``    job weight (priority score, plus a bonus for Critical turbines)
    ``cost``   days of delay + lateness penalty beyond the due date
    ``B_j``    backlog cost: the job's worst in-horizon slot cost + a penalty

Constraints
    each job at most once; resource capacity per day; customer cap per day.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from windmaint.scheduling import (
    Problem,
    ScheduleResult,
    Slot,
    backlog_cost,
    explain_backlog,
    finalise,
    job_weights,
    schedule_greedy,
    slot_cost,
)


def schedule_milp(problem: Problem, cfg: Mapping[str, Any]) -> ScheduleResult:
    m = cfg["scheduling"]["milp"]
    weights = job_weights(problem.jobs, cfg)

    variables: list[tuple[str, Slot, str]] = []
    costs: list[float] = []
    customers: dict[str, str] = {}
    for job in problem.jobs.itertuples(index=False):
        turbine = str(job.Turbine_ID)
        customers[turbine] = job.Customer_Name
        unscheduled = backlog_cost(problem, job.Recommended_Due_Days, cfg)
        for slot in problem.slots:
            if not problem.workable.get((job.Site, slot.day, slot.window), False):
                continue
            for preference, resource in enumerate(problem.eligible.get(turbine, [])):
                if problem.capacity[resource] <= 0:
                    continue
                variables.append((turbine, slot, resource))
                # Constant term w_j * B_j is dropped; it does not affect the argmin.
                # The tiny last term prefers dedicated crews among equal plans.
                costs.append(
                    weights[turbine] * (slot_cost(slot, job.Recommended_Due_Days, cfg) - unscheduled)
                    + 1e-4 * preference
                )

    if not variables:
        return finalise(problem, {}, explain_backlog(problem, {}), "milp", cfg, "no feasible slot")

    row_of: dict[tuple, int] = {}
    upper: list[float] = []
    rows: list[int] = []
    cols: list[int] = []

    def add(key: tuple, bound: float, col: int) -> None:
        if key not in row_of:
            row_of[key] = len(upper)
            upper.append(bound)
        rows.append(row_of[key])
        cols.append(col)

    for col, (turbine, slot, resource) in enumerate(variables):
        add(("job", turbine), 1, col)
        add(("resource", resource, slot.day), problem.capacity[resource], col)
        customer = customers[turbine]
        if customer in problem.customer_cap and turbine not in problem.override:
            add(("customer", customer, slot.day), problem.customer_cap[customer], col)

    matrix = coo_matrix(
        (np.ones(len(rows)), (rows, cols)), shape=(len(upper), len(variables))
    ).tocsr()
    solution = milp(
        c=np.asarray(costs),
        constraints=LinearConstraint(matrix, -np.inf, np.asarray(upper, dtype=float)),
        integrality=np.ones(len(variables)),
        bounds=Bounds(0, 1),
        options={"time_limit": float(m["time_limit_s"])},
    )
    if solution.x is None:
        # No incumbent (e.g. time limit hit immediately): fall back to the baseline.
        fallback = schedule_greedy(problem, cfg)
        fallback.solver_status = f"milp failed ({solution.message}); greedy fallback"
        return fallback

    assigned = {
        turbine: (slot, resource)
        for (turbine, slot, resource), value in zip(variables, solution.x)
        if value > 0.5
    }
    status = "optimal" if solution.status == 0 else str(solution.message)
    return finalise(problem, assigned, explain_backlog(problem, assigned), "milp", cfg, status)
