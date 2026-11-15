"""End-to-end run: load -> score -> schedule -> explain."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from windmaint.backlog import root_causes, skip_reasons
from windmaint.config import load_config
from windmaint.contracts import customer_overview
from windmaint.io import Dataset, load_dataset
from windmaint.optimizer import schedule_milp
from windmaint.recommendations import recommend
from windmaint.scheduling import ScheduleResult, build_problem, schedule_greedy
from windmaint.scoring import build_summary, score

SCHEDULERS = {"greedy": schedule_greedy, "milp": schedule_milp}


@dataclass
class RunResult:
    plan: pd.DataFrame  # one row per turbine: metrics, scores, schedule, root cause
    diagnostics: pd.DataFrame  # one row per (turbine, slot) that was rejected
    backlog: pd.DataFrame  # root-cause table for unscheduled turbines
    recommendations: pd.DataFrame
    customers: pd.DataFrame
    kpis: dict[str, Any]
    dataset: Dataset
    config: dict[str, Any]

    @property
    def scheduler(self) -> str:
        return str(self.kpis["scheduler"])


def _due_status(row: pd.Series) -> str:
    if row["Schedule_Status"] != "Scheduled":
        return "Backlog"
    return "On Time" if row["Scheduled_Day_Index"] <= row["Recommended_Due_Days"] else "Late"


def _assemble(
    scored: pd.DataFrame, result: ScheduleResult, ds: Dataset, limits: pd.DataFrame, cfg: dict
) -> RunResult:
    plan = scored.merge(result.assignments, on="Turbine_ID", how="left")
    plan["Due_Status"] = plan.apply(_due_status, axis=1)

    reasons = skip_reasons(result.diagnostics)
    plan["Skip_Reason"] = plan["Turbine_ID"].astype(str).map(reasons).fillna("OK")

    backlog_ids = plan.loc[plan["Schedule_Status"] == "Backlog", "Turbine_ID"].tolist()
    backlog = root_causes(result.diagnostics, backlog_ids)
    plan = plan.merge(backlog, on="Turbine_ID", how="left")

    scheduled = plan["Schedule_Status"] == "Scheduled"
    critical = plan["Category"] == "Critical"
    planning_days = sorted(ds.weather["Date"].unique())[: int(cfg["scheduling"]["horizon_days"])]
    total_priority = float(plan["Final_Priority_Score"].sum())
    kpis = {
        "scheduler": result.method,
        "solver_status": result.solver_status,
        "jobs": int(len(plan)),
        "scheduled": int(scheduled.sum()),
        "backlog": int((~scheduled).sum()),
        "critical": int(critical.sum()),
        "critical_backlog": int((critical & ~scheduled).sum()),
        "late": int((plan["Due_Status"] == "Late").sum()),
        "priority_covered": (
            float(plan.loc[scheduled, "Final_Priority_Score"].sum()) / total_priority
            if total_priority
            else 0.0
        ),
        "plan_cost": round(result.cost, 4),
        "planning_days": planning_days,
    }
    return RunResult(
        plan=plan,
        diagnostics=result.diagnostics,
        backlog=backlog,
        recommendations=recommend(plan, cfg, len(planning_days)),
        customers=customer_overview(plan, limits),
        kpis=kpis,
        dataset=ds,
        config=cfg,
    )


def run(
    data_dir: str | Path | None = None,
    cfg: Mapping[str, Any] | None = None,
    dataset: Dataset | None = None,
    scheduler: str | None = None,
) -> RunResult:
    """Run the whole decision chain once.

    Pass either ``data_dir`` or an already loaded ``dataset``. ``scheduler``
    overrides ``cfg['scheduling']['scheduler']``.
    """
    config = dict(cfg) if cfg is not None else load_config()
    ds = dataset if dataset is not None else load_dataset(data_dir)  # type: ignore[arg-type]
    method = scheduler or config["scheduling"]["scheduler"]
    if method not in SCHEDULERS:
        raise ValueError(f"unknown scheduler {method!r}; choose from {sorted(SCHEDULERS)}")

    summary, limits = build_summary(ds, config)
    scored = score(summary, ds.planning_rules, config)
    problem = build_problem(scored, ds, config)
    return _assemble(scored, SCHEDULERS[method](problem, config), ds, limits, config)


def compare_schedulers(
    data_dir: str | Path | None = None,
    cfg: Mapping[str, Any] | None = None,
    dataset: Dataset | None = None,
) -> tuple[pd.DataFrame, dict[str, RunResult]]:
    """Run greedy and MILP on the same problem.

    Returns a KPI table (one row per scheduler) and both full results.
    """
    config = dict(cfg) if cfg is not None else load_config()
    ds = dataset if dataset is not None else load_dataset(data_dir)  # type: ignore[arg-type]
    results = {name: run(cfg=config, dataset=ds, scheduler=name) for name in SCHEDULERS}
    keys = ["scheduled", "backlog", "critical_backlog", "late", "priority_covered", "plan_cost"]
    table = pd.DataFrame(
        [{"scheduler": name, **{k: res.kpis[k] for k in keys}} for name, res in results.items()]
    )
    return table, results
