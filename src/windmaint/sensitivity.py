"""How much does the ranking depend on the chosen weights?

The 0.60 / 0.25 / 0.15 weights are engineering judgement, not calibrated
values. This module perturbs them and reports which rank positions are robust
and which would flip under a slightly different, equally defensible choice.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

from windmaint.scoring import rank

COMPONENTS = ["technical", "contract", "due"]
_NORMALISED = ["Technical_Normalized", "Contract_Normalized", "Due_Normalized"]


def weight_sensitivity(
    scored: pd.DataFrame,
    cfg: Mapping[str, Any],
    draws: int = 1000,
    concentration: float = 40.0,
    seed: int = 0,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Monte-Carlo rank stability under weight uncertainty.

    Weights are drawn from ``Dirichlet(concentration * base_weights)``; with the
    default concentration the technical weight varies roughly within 0.45-0.75.

    Returns ``(per_turbine, overall)``:

    * per turbine: base rank, mean / best / worst rank, share of draws in which
      the rank equals the base rank;
    * overall: share of draws with an identical full ordering / identical
      top-3, the average share of the base top-10 that stays in the top-10,
      and the mean absolute rank shift. On a large fleet the exact order is
      rarely reproduced; the top-10 overlap is the figure to look at.
    """
    base = np.array([float(cfg["scoring"]["weights"][c]) for c in COMPONENTS])
    critical_first = bool(cfg["scoring"]["ranking"]["critical_first"])
    rng = np.random.default_rng(seed)
    alpha = np.maximum(base * concentration, 1e-3)
    samples = rng.dirichlet(alpha, size=draws)

    ids = scored["Turbine_ID"].tolist()
    base_rank = dict(zip(ids, scored["Priority_Rank"]))
    base_order = scored.sort_values("Priority_Rank")["Turbine_ID"].tolist()
    matrix = scored[_NORMALISED].to_numpy(dtype=float)

    ranks = np.empty((draws, len(ids)), dtype=int)
    same_order = 0
    same_top3 = 0
    top_k = min(10, len(ids))
    overlap = 0.0
    work = scored.drop(columns=["Priority_Rank"])
    for i, weights in enumerate(samples):
        candidate = rank(work.assign(Final_Priority_Score=matrix @ weights), critical_first)
        order = candidate["Turbine_ID"].tolist()
        position = {turbine: pos + 1 for pos, turbine in enumerate(order)}
        ranks[i] = [position[t] for t in ids]
        same_order += order == base_order
        same_top3 += order[:3] == base_order[:3]
        overlap += len(set(order[:top_k]) & set(base_order[:top_k])) / top_k

    per_turbine = pd.DataFrame(
        {
            "Turbine_ID": ids,
            "Base_Rank": [base_rank[t] for t in ids],
            "Mean_Rank": ranks.mean(axis=0).round(2),
            "Best_Rank": ranks.min(axis=0),
            "Worst_Rank": ranks.max(axis=0),
            "Rank_Stability": (ranks == np.array([base_rank[t] for t in ids])).mean(axis=0).round(3),
        }
    ).sort_values("Base_Rank").reset_index(drop=True)
    overall = {
        "identical_order_share": same_order / draws,
        "identical_top3_share": same_top3 / draws,
        "top10_overlap": overlap / draws,
        "mean_abs_rank_shift": float(
            np.abs(ranks - np.array([base_rank[t] for t in ids])).mean()
        ),
        "draws": float(draws),
    }
    return per_turbine, overall
