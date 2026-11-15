"""Generate the synthetic 150-turbine demo fleet in ``data/fleet``.

Everything here is invented: site codes, coordinates (roughly placed in windy
regions of Türkiye), customers, contracts, damages and weather. The generator
is seeded, so the dataset can be rebuilt or resized:

    python scripts/generate_fleet.py                 # rebuild data/fleet
    python scripts/generate_fleet.py --seed 7 --out data/fleet_b

Realism choices
* ~45 % of turbines carry open inspection findings; most are severity 1-3.
* Weather is correlated in time (a storm lasts days) and within a region.
* ``Workable`` is derived from the measurements with the default limits, so
  flag and measurements never disagree.
* Customer E is deliberately small with a high availability target: its outage
  budget is zero, which reproduces the "cannot be scheduled at all" case.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from windmaint.config import DEFAULTS  # noqa: E402
from windmaint.weather import derive_workability  # noqa: E402

# site, province, service region, lat, lon, customer, turbines, rated MW
SITES = [
    ("AEG-01", "İzmir", "Aegean", 38.32, 26.40, "Customer A", 18, 3.6),
    ("AEG-02", "İzmir", "Aegean", 38.60, 26.75, "Customer A", 16, 3.0),
    ("AEG-03", "Manisa", "Aegean", 39.15, 27.65, "Customer A", 14, 2.5),
    ("AEG-04", "Aydın", "Aegean", 37.75, 27.45, "Customer B", 12, 3.0),
    ("MAR-01", "Balıkesir", "Marmara", 40.30, 27.95, "Customer B", 16, 3.6),
    ("MAR-02", "Çanakkale", "Marmara", 40.05, 26.45, "Customer B", 12, 4.2),
    ("MAR-03", "Kırklareli", "Marmara", 41.65, 27.35, "Customer C", 14, 3.0),
    ("MAR-04", "İstanbul", "Marmara", 41.25, 28.45, "Customer C", 16, 2.5),
    ("MED-01", "Hatay", "Mediterranean", 36.50, 36.20, "Customer D", 12, 3.0),
    ("MED-02", "Osmaniye", "Mediterranean", 37.10, 36.40, "Customer D", 8, 2.5),
    ("CAN-01", "Kayseri", "Central", 38.55, 35.60, "Customer E", 7, 3.6),
    ("CAN-02", "Sivas", "Central", 39.60, 37.10, "Customer E", 5, 3.0),
]

# customer -> (target availability %, turbines currently down, contract month, total months)
CUSTOMERS = {
    "Customer A": (95.0, 0, 10, 12),
    "Customer B": (95.0, 1, 4, 12),
    "Customer C": (96.0, 0, 8, 12),
    "Customer D": (95.0, 0, 6, 12),
    "Customer E": (97.0, 0, 11, 12),
}

RESOURCES = [
    ("CREW-AEG-1", "Blade crew", "In-house", "Aegean", "İzmir", 1, 4),
    ("CREW-AEG-2", "Rope access", "Contractor North", "Aegean", "Manisa", 1, 3),
    ("CREW-MAR-1", "Blade crew", "In-house", "Marmara", "Balıkesir", 1, 4),
    ("CREW-MAR-2", "Rope access", "Contractor North", "Marmara|Aegean", "Çanakkale", 1, 3),
    ("CREW-MED-1", "Blade crew", "Contractor South", "Mediterranean", "Hatay", 1, 4),
    ("CREW-CAN-1", "Blade crew", "Contractor South", "Central|Mediterranean", "Kayseri", 1, 3),
]

RULES = [
    ("R1", "Critical", 2, 99, 5, "Critical + high trend"),
    ("R2", "Critical", 0, 1, 10, "Critical + low trend"),
    ("R3", "Non-Critical", 2, 99, 14, "Non-critical + high trend"),
    ("R4", "Default", 0, 99, 30, "Default rule"),
]

DAMAGE_TYPES = [
    "Leading edge erosion",
    "Coating damage",
    "Trailing edge crack",
    "Lightning damage",
    "Delamination",
    "Surface crack",
]
COMPONENTS = ["Blade A", "Blade B", "Blade C"]
REGION_WIND = {"Aegean": 9.0, "Marmara": 10.0, "Mediterranean": 8.0, "Central": 8.5}
WINDOWS = ["Morning", "Afternoon"]


def build(seed: int, start: str, days: int, damaged_share: float) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=days)  # working days only

    turbines, damages = [], []
    for site, _prov, region, _lat, _lon, _cust, count, mw in SITES:
        site_share = float(np.clip(damaged_share + rng.normal(0, 0.10), 0.2, 0.7))
        for number in range(1, count + 1):
            turbine = f"{site.replace('-', '')}-T{number:02d}"
            turbines.append(
                {
                    "Turbine_ID": turbine,
                    "Site": site,
                    "Region": region,
                    "Rated_MW": mw,
                    "Commissioned": int(rng.integers(2012, 2023)),
                }
            )
            if rng.random() > site_share:
                continue
            last = pd.Timestamp("2026-10-01") + pd.Timedelta(days=int(rng.integers(0, 40)))
            previous = last - pd.Timedelta(days=int(rng.integers(170, 380)))
            for index in range(1, 1 + min(5, 1 + rng.poisson(0.9))):
                current = int(rng.choice([1, 2, 3, 4, 5], p=[0.28, 0.34, 0.24, 0.10, 0.04]))
                if rng.random() < 0.20:
                    before = 0  # found for the first time
                else:
                    before = max(1, current - int(rng.choice([0, 1, 2], p=[0.55, 0.35, 0.10])))
                damages.append(
                    {
                        "Damage_ID": f"{turbine}-D{index}",
                        "Turbine_ID": turbine,
                        "Component": str(rng.choice(COMPONENTS)),
                        "Damage_Type": str(rng.choice(DAMAGE_TYPES)),
                        "Current_Severity": current,
                        "Previous_Severity": before,
                        "Inspection_Date": last.strftime("%Y-%m-%d"),
                        "Previous_Inspection_Date": previous.strftime("%Y-%m-%d"),
                    }
                )

    site_contracts = []
    for site, _prov, _region, _lat, _lon, customer, _count, _mw in SITES:
        target = CUSTOMERS[customer][0]
        current = round(target + float(rng.normal(-0.2, 0.7)), 1)
        gap = target - current
        status, boost = (
            ("Below Target", 2) if gap > 0.5 else ("Slightly Below", 1) if gap > 0 else ("Above Target", 0)
        )
        site_contracts.append(
            {
                "Site": site,
                "Customer_Name": customer,
                "Contract_Target_Availability": target,
                "Current_Availability": current,
                "Contract_Status": status,
                "Priority_Boost": boost,
            }
        )

    totals = pd.DataFrame(SITES).groupby(5)[6].sum()
    customer_contracts = [
        {
            "Customer_Name": name,
            "Total_Turbines": int(totals[name]),
            "Contract_Target_Availability": target,
            "Current_Down_Turbines": down,
            "Contract_Month": month,
            "Contract_Total_Months": total,
        }
        for name, (target, down, month, total) in CUSTOMERS.items()
    ]

    # Weather: a regional AR(1) wind regime per day, a site offset, windier afternoons.
    weather = []
    regime = {region: mean + rng.normal(0, 2.5) for region, mean in REGION_WIND.items()}
    rain = {region: float(rng.uniform(10, 50)) for region in REGION_WIND}
    offsets = {row[0]: float(rng.normal(0, 1.0)) for row in SITES}
    for day_number, date in enumerate(dates):
        for region, mean in REGION_WIND.items():
            regime[region] = mean + 0.65 * (regime[region] - mean) + rng.normal(0, 2.4)
            rain[region] = float(np.clip(0.6 * rain[region] + rng.normal(14, 18), 0, 95))
        for site, _prov, region, lat, _lon, _cust, _count, _mw in SITES:
            for window in WINDOWS:
                wind = regime[region] + offsets[site] + (rng.normal(1.2, 1.2) if window == "Afternoon" else 0)
                wind = float(np.clip(wind + rng.normal(0, 0.8), 1.5, 24))
                weather.append(
                    {
                        "Site": site,
                        "Date": date.strftime("%Y-%m-%d"),
                        "Time_Window": window,
                        "Wind_Speed": round(wind, 1),
                        "Wind_Gust": round(wind * float(rng.uniform(1.25, 1.55)), 1),
                        "Precip_Probability": int(np.clip(rain[region] + rng.normal(0, 8), 0, 100)),
                        "Temperature": round(
                            14 - 0.9 * (lat - 36) - 0.3 * day_number
                            + (3 if window == "Afternoon" else 0) + float(rng.normal(0, 1.2)),
                            1,
                        ),
                    }
                )
    weather = pd.DataFrame(weather)
    workable = derive_workability(weather, DEFAULTS["weather"]["rules"]["default"])
    weather["Workable"] = workable.map({True: "Yes", False: "No"})

    first = weather[(weather["Date"] == weather["Date"].min()) & (weather["Time_Window"] == "Morning")]
    site_weather = first[["Site", "Wind_Speed", "Temperature"]].copy()
    site_weather.insert(
        2,
        "Rain_Risk",
        pd.cut(first["Precip_Probability"], [-1, 30, 60, 100], labels=["Low", "Medium", "High"]).astype(str),
    )

    return {
        "turbines": pd.DataFrame(turbines),
        "damages": pd.DataFrame(damages),
        "weather": weather,
        "resources": pd.DataFrame(
            RESOURCES,
            columns=[
                "Resource_ID", "Resource_Type", "Contractor", "Region",
                "Start_Point", "Daily_Capacity", "Team_Size",
            ],
        ),
        "planning_rules": pd.DataFrame(
            RULES,
            columns=[
                "Rule_ID", "Category", "Min_Increasing_Count",
                "Max_Increasing_Count", "Due_Days", "Description",
            ],
        ),
        "site_contracts": pd.DataFrame(site_contracts),
        "customer_contracts": pd.DataFrame(customer_contracts),
        "site_locations": pd.DataFrame(
            [(s[0], s[1], s[3], s[4]) for s in SITES],
            columns=["Site", "Province", "Latitude", "Longitude"],
        ),
        "site_weather": site_weather,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", default=str(ROOT / "data" / "fleet"))
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--start", default="2026-11-16", help="first planning day")
    parser.add_argument("--days", type=int, default=10, help="working days of weather")
    parser.add_argument("--damaged-share", type=float, default=0.45)
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tables = build(args.seed, args.start, args.days, args.damaged_share)
    for name, frame in tables.items():
        frame.to_csv(out / f"{name}.csv", sep=";", index=False, encoding="utf-8", lineterminator="\n")
    jobs = tables["damages"]["Turbine_ID"].nunique()
    print(
        f"{len(tables['turbines'])} turbines on {len(SITES)} sites, {len(tables['damages'])} "
        f"damages on {jobs} turbines, {len(tables['weather'])} weather slots -> {out}"
    )


if __name__ == "__main__":
    main()
