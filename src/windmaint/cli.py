"""Command line interface: ``windmaint <command>``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from windmaint.config import load_config
from windmaint.export import export
from windmaint.io import DataValidationError, load_dataset
from windmaint.pipeline import compare_schedulers, run
from windmaint.sensitivity import weight_sensitivity
from windmaint.weather import fetch_forecast, flag_rule_conflicts


def _print(title: str, frame: pd.DataFrame, limit: int | None = None) -> None:
    print(f"\n=== {title} ===")
    if frame.empty:
        print("(none)")
        return
    shown = frame if limit is None else frame.head(limit)
    print(shown.to_string(index=False))
    if len(shown) < len(frame):
        print(f"... {len(frame) - len(shown)} more row(s); see the Excel output")


def _show_issues(issues) -> None:
    for issue in issues:
        print(f"[{issue.level.upper()}] {issue.table}: {issue.message}")


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    result = run(args.data, cfg, scheduler=args.scheduler)
    _show_issues(result.dataset.issues)

    plan = result.plan
    _print(
        "PRIORITY RANKING",
        plan[
            [
                "Priority_Rank",
                "Turbine_ID",
                "Category",
                "Final_Priority_Score",
                "Technical_Contribution",
                "Contract_Contribution",
                "Due_Contribution",
            ]
        ].round(3),
        limit=args.rows,
    )
    scheduled = plan[plan["Schedule_Status"] == "Scheduled"].sort_values(
        ["Scheduled_Day_Index", "Priority_Rank"]
    )
    _print(
        f"PLAN ({result.scheduler})",
        scheduled[
            [
                "Scheduled_Day",
                "Scheduled_Window",
                "Turbine_ID",
                "Category",
                "Assigned_Resource",
                "Due_Status",
            ]
        ],
        limit=args.rows,
    )
    _print(
        "BACKLOG ROOT CAUSES",
        result.backlog[
            ["Turbine_ID", "Primary_Blocker", "Near_Miss_Slot", "Near_Miss_Blocker", "Actionable"]
        ],
        limit=args.rows,
    )
    k = result.kpis
    print(
        f"\n{k['scheduled']}/{k['jobs']} scheduled, {k['backlog']} backlog "
        f"({k['critical_backlog']} critical), priority covered {k['priority_covered']:.0%}, "
        f"plan cost {k['plan_cost']}"
    )
    paths = export(result, args.out)
    print(f"Output written to {paths['xlsx']}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    table, results = compare_schedulers(args.data, load_config(args.config))
    _print("GREEDY vs MILP", table.round(3))
    merged = results["greedy"].plan[["Turbine_ID", "Scheduled_Day", "Scheduled_Window"]].merge(
        results["milp"].plan[["Turbine_ID", "Scheduled_Day", "Scheduled_Window"]],
        on="Turbine_ID",
        suffixes=("_greedy", "_milp"),
    )
    same = (
        merged["Scheduled_Day_greedy"].fillna("") == merged["Scheduled_Day_milp"].fillna("")
    ) & (merged["Scheduled_Window_greedy"].fillna("") == merged["Scheduled_Window_milp"].fillna(""))
    _print("PLAN DIFFERENCES", merged[~same].fillna("backlog"))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    dataset = load_dataset(args.data, strict=False)
    _show_issues(dataset.issues)
    errors = [i for i in dataset.issues if i.level == "error"]
    if not errors:
        conflicts = flag_rule_conflicts(dataset.weather, cfg["weather"]["rules"]["default"])
        if not conflicts.empty:
            print(
                f"[INFO] weather: {len(conflicts)} slot(s) where the Workable flag disagrees "
                "with the configured wind/rain limits"
            )
    print("Data is valid." if not errors else f"{len(errors)} error(s) found.")
    return 1 if errors else 0


def cmd_sensitivity(args: argparse.Namespace) -> int:
    result = run(args.data, load_config(args.config))
    table, overall = weight_sensitivity(result.plan, result.config, draws=args.draws)
    _print("RANK STABILITY UNDER WEIGHT UNCERTAINTY", table, limit=args.rows)
    print(
        f"\nIdentical full ordering in {overall['identical_order_share']:.0%} of draws, "
        f"identical top-3 in {overall['identical_top3_share']:.0%}. On average "
        f"{overall['top10_overlap']:.0%} of the top 10 stays in the top 10; "
        f"mean rank shift {overall['mean_abs_rank_shift']:.1f} places."
    )
    return 0


def cmd_fetch_weather(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    dataset = load_dataset(args.data, strict=False)
    if dataset.site_locations is None:
        print("site_locations.csv is required to fetch a forecast.", file=sys.stderr)
        return 1
    table = fetch_forecast(dataset.site_locations, args.days, cfg["weather"]["rules"]["default"])
    target = Path(args.output or Path(args.data) / "weather.csv")
    table.to_csv(target, index=False, sep=";")
    print(f"{len(table)} forecast slots written to {target}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="windmaint", description="Wind turbine maintenance prioritisation and planning."
    )
    parser.add_argument(
        "--data",
        default="data/fleet",
        help="folder with the input CSV files (data/fleet: 150 turbines, data/sample: 5)",
    )
    parser.add_argument("--config", default=None, help="YAML file overriding the defaults")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="score, schedule and export")
    p_run.add_argument("--scheduler", choices=["greedy", "milp"], default=None)
    p_run.add_argument("--out", default="outputs")
    p_run.add_argument("--rows", type=int, default=15, help="rows printed per table")
    p_run.set_defaults(func=cmd_run)

    sub.add_parser("compare", help="greedy vs MILP on the same data").set_defaults(func=cmd_compare)
    sub.add_parser("validate", help="check the input data").set_defaults(func=cmd_validate)

    p_sens = sub.add_parser("sensitivity", help="rank stability under weight uncertainty")
    p_sens.add_argument("--draws", type=int, default=1000)
    p_sens.add_argument("--rows", type=int, default=15, help="rows printed")
    p_sens.set_defaults(func=cmd_sensitivity)

    p_wx = sub.add_parser("fetch-weather", help="build weather.csv from the Open-Meteo forecast")
    p_wx.add_argument("--days", type=int, default=3)
    p_wx.add_argument("--output", default=None)
    p_wx.set_defaults(func=cmd_fetch_weather)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except DataValidationError as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
