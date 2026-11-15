from __future__ import annotations

import pandas as pd
import pytest

from windmaint.pipeline import run
from windmaint.weather import (
    classify_site_weather,
    derive_workability,
    flag_rule_conflicts,
    hourly_to_windows,
    parse_open_meteo,
)


def test_near_miss_points_at_the_actionable_constraint(sample, cfg):
    result = run(cfg=cfg, dataset=sample, scheduler="greedy")
    root = result.backlog.set_index("Turbine_ID")
    # T1: weather blocks most slots, but the one workable slot failed on contract.
    assert root.loc["T1", "Primary_Blocker"] == "Weather"
    assert root.loc["T1", "Near_Miss_Slot"] == "2026-04-15 Morning"
    assert root.loc["T1", "Near_Miss_Blocker"] == "Contract availability cap"
    assert bool(root.loc["T1", "Actionable"])
    # T4: site C has no workable slot at all.
    assert root.loc["T4", "Weather_Blocks"] == 6
    assert not bool(root.loc["T4", "Actionable"])


def test_recommendations_are_derived_from_the_plan(sample, cfg):
    result = run(cfg=cfg, dataset=sample, scheduler="greedy")
    recs = result.recommendations.set_index("Turbine_ID")
    assert set(recs.index) == set(result.plan["Turbine_ID"])
    assert recs.loc["T5", "Level"] == "Escalate"
    assert "no outage budget" in recs.loc["T5", "Actions"]
    assert "turn on the critical override" in recs.loc["T5", "Actions"]  # severity 5
    assert "T2" in recs.loc["T1", "Actions"]  # names the job that uses the cap
    assert recs.loc["T2", "Level"] == "On track"
    assert "Team_4" in recs.loc["T2", "Headline"]
    assert "No workable weather window" in recs.loc["T4", "Actions"]
    # Most urgent first.
    assert result.recommendations["Level"].iloc[0] == "Escalate"


def test_score_explanation_uses_real_contributions(sample, cfg):
    result = run(cfg=cfg, dataset=sample)
    row = result.plan.set_index("Turbine_ID").loc["T5"]
    text = result.recommendations.set_index("Turbine_ID").loc["T5", "Rationale"]
    assert f"technical {row['Technical_Contribution']:.2f}" in text
    assert f"Priority {row['Final_Priority_Score']:.2f}" in text


def test_workability_rules():
    weather = pd.DataFrame(
        {
            "Wind_Speed": [8.0, 13.0, 9.0, None],
            "Precip_Probability": [10.0, 10.0, 80.0, 10.0],
            "Workable": [True, True, True, True],
        }
    )
    rules = {"max_wind_speed": 12, "max_precip_probability": 60, "max_wind_gust": 18}
    assert derive_workability(weather, rules).tolist() == [True, False, False, False]
    assert len(flag_rule_conflicts(weather, rules)) == 3


def test_rules_source_overrides_the_flag(sample, cfg):
    cfg["weather"]["workability_source"] = "rules"
    result = run(cfg=cfg, dataset=sample)
    # Site C, 16.04 afternoon: 9 m/s is workable under the 12 m/s rule although
    # the hand-entered flag says No.
    assert result.plan.set_index("Turbine_ID").loc["T4", "Schedule_Status"] == "Scheduled"


def test_site_status(cfg):
    frame = pd.DataFrame({"Wind_Speed": [8, 13, 15, None]})
    assert classify_site_weather(frame, cfg).tolist() == [
        "Feasible",
        "Risky Conditions",
        "Maintenance Blocked",
        "Unknown",
    ]


def test_open_meteo_payload_becomes_scheduler_slots():
    hours = pd.date_range("2026-04-15 00:00", periods=24, freq="h")
    payload = {
        "hourly": {
            "time": [h.strftime("%Y-%m-%dT%H:%M") for h in hours],
            "wind_speed_80m": [5.0] * 9 + [14.0] + [6.0] * 14,  # gust of wind at 09:00
            "wind_gusts_10m": [8.0] * 24,
            "precipitation_probability": [0] * 24,
            "temperature_2m": [12.0] * 24,
        }
    }
    hourly = parse_open_meteo(payload, "A", "wind_speed_80m")
    slots = hourly_to_windows(hourly)
    assert slots[["Site", "Date", "Time_Window"]].values.tolist() == [
        ["A", "2026-04-15", "Morning"],
        ["A", "2026-04-15", "Afternoon"],
    ]
    assert slots["Wind_Speed"].tolist() == [14.0, 6.0]  # worst hour of each window
    assert derive_workability(slots, {"max_wind_speed": 12}).tolist() == [False, True]


def test_kpis_are_consistent(sample, cfg):
    k = run(cfg=cfg, dataset=sample).kpis
    assert k["jobs"] == 5 and k["scheduled"] + k["backlog"] == 5
    assert k["priority_covered"] == pytest.approx(0.4107, abs=1e-3)


def test_dashboard_aggregations(sample, cfg):
    from windmaint.analytics import calendar, resource_utilisation, site_overview

    result = run(cfg=cfg, dataset=sample, scheduler="greedy")
    sites = site_overview(result).set_index("Site")
    assert sites.loc["D", "Site_Status"] == "Critical backlog"
    assert sites.loc["B", ["Jobs", "Scheduled", "Backlog"]].tolist() == [2, 2, 0]
    assert sites.loc["B", "Site_Status"] == "On plan"  # critical, but scheduled
    assert sites.loc["C", "Site_Status"] == "Critical backlog"
    assert sites.loc["C", "Weather_Status"] == "Maintenance Blocked"
    assert {"Latitude", "Longitude"} <= set(sites.columns)

    usage = resource_utilisation(result)
    assert len(usage) == 4 * 3
    assert (usage["Used"] <= usage["Capacity"]).all()
    assert usage["Used"].sum() == result.kpis["scheduled"]

    board = calendar(result)
    assert board["2026-04-15"]["Morning"]["Turbine_ID"].tolist() == ["T2"]
    assert board["2026-04-17"]["Afternoon"].empty
