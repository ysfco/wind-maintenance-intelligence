from __future__ import annotations

import pandas as pd
import pytest

from windmaint.contracts import compute_customer_limits
from windmaint.scoring import build_summary, score, summarise_damages
from windmaint.sensitivity import weight_sensitivity


def scored(dataset, cfg):
    summary, _ = build_summary(dataset, cfg)
    return score(summary, dataset.planning_rules, cfg).set_index("Turbine_ID")


def test_damage_metrics(sample):
    s = summarise_damages(sample.damages, 4).set_index("Turbine_ID")
    assert s.loc["T5", ["Max_Severity", "Total_Damage_Count", "Critical_Count"]].tolist() == [5, 3, 1]
    assert s.loc["T5", "Delta_Sum"] == 8
    assert s.loc["T5", "Increasing_Count"] == 3
    assert s.loc["T1", "Category"] == "Non-Critical"
    assert s.loc["T4", "Category"] == "Critical"


def test_legacy_scores_are_reproduced(sample, legacy_cfg):
    """minmax + no tiering must give the numbers of the original prototype."""
    s = scored(sample, legacy_cfg)
    expected = {"T5": 0.863636, "T2": 0.691818, "T4": 0.504545, "T3": 0.366104, "T1": 0.250000}
    for turbine, value in expected.items():
        assert s.loc[turbine, "Final_Priority_Score"] == pytest.approx(value, abs=1e-6)
    assert s.sort_values("Priority_Rank").index.tolist() == ["T5", "T2", "T4", "T3", "T1"]
    assert s.loc["T5", "Technical_Score"] == 20.5
    assert s.loc["T2", "Contract_Score"] == pytest.approx(2 * (1 + 10 / 12))


@pytest.mark.parametrize("method", ["reference", "minmax"])
def test_contributions_sum_to_final_score(sample, cfg, method):
    cfg["scoring"]["normalization"]["method"] = method
    s = scored(sample, cfg)
    total = s["Technical_Contribution"] + s["Contract_Contribution"] + s["Due_Contribution"]
    pd.testing.assert_series_equal(total, s["Final_Priority_Score"], check_names=False)
    assert s["Final_Priority_Score"].between(0, 1).all()


def test_reference_scores_do_not_depend_on_the_rest_of_the_fleet(sample, cfg):
    full = scored(sample, cfg)
    sample.damages = sample.damages[sample.damages["Turbine_ID"] != "T5"]
    reduced = scored(sample, cfg)
    for turbine in reduced.index:
        assert reduced.loc[turbine, "Final_Priority_Score"] == pytest.approx(
            full.loc[turbine, "Final_Priority_Score"]
        )


def test_minmax_scores_do_depend_on_the_rest_of_the_fleet(sample, legacy_cfg):
    full = scored(sample, legacy_cfg)
    sample.damages = sample.damages[sample.damages["Turbine_ID"] != "T5"]
    reduced = scored(sample, legacy_cfg)
    assert reduced.loc["T2", "Final_Priority_Score"] != pytest.approx(
        full.loc["T2", "Final_Priority_Score"]
    )


def test_critical_first_keeps_critical_turbines_on_top(sample, cfg):
    # Make contract dominate so that a non-critical turbine gets the best score.
    cfg["scoring"]["weights"] = {"technical": 0.1, "contract": 0.8, "due": 0.1}
    s = scored(sample, cfg).sort_values("Priority_Rank")
    categories = s["Category"].tolist()
    assert categories == sorted(categories, key=lambda c: c != "Critical")
    cfg["scoring"]["ranking"]["critical_first"] = False
    free = scored(sample, cfg).sort_values("Priority_Rank")
    assert free["Final_Priority_Score"].is_monotonic_decreasing


def test_due_days_follow_the_rule_table(sample, cfg):
    s = scored(sample, cfg)
    assert s["Recommended_Due_Days"].to_dict() == {"T1": 30, "T2": 10, "T3": 14, "T4": 5, "T5": 5}


def test_customer_limits_floor_and_float_safety():
    cc = pd.DataFrame(
        {
            "Customer_Name": ["a", "b", "c", "d"],
            "Total_Turbines": [100, 80, 60, 50],
            "Contract_Target_Availability": [97.0, 98.0, 95.0, 94.0],
            "Current_Down_Turbines": [2, 1, 5, 0],
            "Contract_Month": [10, 8, 4, 15],
            "Contract_Total_Months": [12, 12, 12, 12],
        }
    )
    out = compute_customer_limits(cc).set_index("Customer_Name")
    assert out["Allowed_Total_Down"].tolist() == [3, 1, 3, 3]  # 1.6 floors to 1
    assert out["Allowed_Additional_Down"].tolist() == [1, 0, 0, 3]  # never negative
    assert out.loc["a", "Contract_Time_Factor"] == pytest.approx(10 / 12)
    assert out.loc["d", "Contract_Time_Factor"] == 1.0  # capped


def test_missing_contract_means_no_boost_and_no_cap(sample, cfg):
    sample.site_contracts = sample.site_contracts[sample.site_contracts["Site"] != "D"]
    s = scored(sample, cfg)
    assert s.loc["T5", "Customer_Name"] == "Unassigned"
    assert s.loc["T5", "Contract_Contribution"] == 0
    assert pd.isna(s.loc["T5", "Allowed_Additional_Down"])


def test_sensitivity_is_deterministic_and_bounded(sample, cfg):
    plan = scored(sample, cfg).reset_index()
    table, overall = weight_sensitivity(plan, cfg, draws=200, seed=1)
    again, _ = weight_sensitivity(plan, cfg, draws=200, seed=1)
    pd.testing.assert_frame_equal(table, again)
    assert table["Rank_Stability"].between(0, 1).all()
    assert 0 <= overall["identical_order_share"] <= 1
    # With critical-first tiers the two non-critical turbines can never move up.
    assert table.set_index("Turbine_ID").loc["T3", "Best_Rank"] == 4
