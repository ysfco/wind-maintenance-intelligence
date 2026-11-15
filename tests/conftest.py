from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from windmaint.config import load_config  # noqa: E402
from windmaint.io import Dataset, load_dataset  # noqa: E402

SAMPLE = ROOT / "data" / "sample"


@pytest.fixture()
def cfg():
    return load_config()


@pytest.fixture()
def legacy_cfg():
    """Settings that reproduce the original single-file prototype."""
    return load_config(
        overrides={
            "scoring": {"normalization": {"method": "minmax"}, "ranking": {"critical_first": False}}
        }
    )


@pytest.fixture()
def sample():
    return load_dataset(SAMPLE)


def random_dataset(seed: int, turbines: int = 30) -> Dataset:
    """A random but valid fleet for property tests."""
    rng = np.random.default_rng(seed)
    sites = [f"S{i}" for i in range(6)]
    regions = ["North", "South", "East"]
    customers = ["C1", "C2", "C3"]
    days = ["2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04"]

    turbine_rows = [
        {"Turbine_ID": f"T{i:03d}", "Site": rng.choice(sites), "Region": rng.choice(regions)}
        for i in range(turbines)
    ]
    damage_rows = []
    for row in turbine_rows:
        for k in range(int(rng.integers(1, 5))):
            previous = int(rng.integers(0, 5))
            damage_rows.append(
                {
                    "Damage_ID": f"{row['Turbine_ID']}-D{k}",
                    "Turbine_ID": row["Turbine_ID"],
                    "Current_Severity": int(min(5, previous + rng.integers(0, 3))),
                    "Previous_Severity": previous,
                }
            )
    weather_rows = [
        {"Site": s, "Date": d, "Time_Window": w, "Workable": bool(rng.random() < 0.6)}
        for s in sites
        for d in days
        for w in ("Morning", "Afternoon")
    ]
    return Dataset(
        turbines=pd.DataFrame(turbine_rows),
        damages=pd.DataFrame(damage_rows),
        weather=pd.DataFrame(weather_rows),
        resources=pd.DataFrame(
            {
                "Resource_ID": ["R1", "R2", "R3", "R4"],
                "Region": ["North", "South", "East", "North|East"],
                "Daily_Capacity": [2, 1, 1, 1],
            }
        ),
        planning_rules=pd.DataFrame(
            {
                "Category": ["Critical", "Critical", "Non-Critical", "Default"],
                "Min_Increasing_Count": [2, 0, 2, 0],
                "Max_Increasing_Count": [99, 1, 99, 99],
                "Due_Days": [5, 10, 14, 30],
            }
        ),
        site_contracts=pd.DataFrame(
            {
                "Site": sites,
                "Customer_Name": [customers[i % 3] for i in range(len(sites))],
                "Priority_Boost": [float(rng.integers(0, 3)) for _ in sites],
            }
        ),
        customer_contracts=pd.DataFrame(
            {
                "Customer_Name": customers,
                "Total_Turbines": [100, 80, 60],
                "Contract_Target_Availability": [97.0, 96.0, 95.0],
                "Current_Down_Turbines": [1, 1, 1],
                "Contract_Month": [10, 6, 2],
                "Contract_Total_Months": [12, 12, 12],
            }
        ),
    )
