"""Contract model.

The contract acts on two levels:

* **soft**  - ``Contract_Score`` raises the priority of turbines whose site is
  below its availability target, more so late in the contract period;
* **hard**  - ``Allowed_Additional_Down`` caps how many turbines of one customer
  may be taken down for maintenance on the same day.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_customer_limits(customer_contracts: pd.DataFrame) -> pd.DataFrame:
    """Add ``Contract_Time_Factor`` and the availability budget per customer.

    ``Contract_Time_Factor = Contract_Month / Contract_Total_Months`` (0-1) is
    the elapsed share of the contract period. It is *not* a penalty estimate.

    ``Allowed_Total_Down = floor(Total_Turbines * (1 - target))``. Flooring is
    the conservative choice: 80 turbines at 98 % allow 1.6 -> 1 turbine down.
    A tiny epsilon guards against binary rounding (100 * 0.03 = 2.9999...).
    """
    cc = customer_contracts.copy()
    cc["Contract_Time_Factor"] = (cc["Contract_Month"] / cc["Contract_Total_Months"]).clip(0, 1)
    budget = cc["Total_Turbines"] * (1 - cc["Contract_Target_Availability"] / 100)
    cc["Allowed_Total_Down"] = np.floor(budget + 1e-9).astype(int)
    cc["Allowed_Additional_Down"] = (
        cc["Allowed_Total_Down"] - cc["Current_Down_Turbines"]
    ).clip(lower=0).astype(int)
    return cc


def customer_overview(summary: pd.DataFrame, limits: pd.DataFrame) -> pd.DataFrame:
    """One row per customer: contract facts plus what the plan does to them."""
    grouped = (
        summary.groupby("Customer_Name", dropna=False)
        .agg(
            Planned_Turbines=("Turbine_ID", "count"),
            Critical_Turbines=("Category", lambda s: int((s == "Critical").sum())),
            Scheduled=("Schedule_Status", lambda s: int((s == "Scheduled").sum())),
            Backlog=("Schedule_Status", lambda s: int((s == "Backlog").sum())),
            Average_Priority=("Final_Priority_Score", "mean"),
            Max_Priority=("Final_Priority_Score", "max"),
        )
        .reset_index()
    )
    scheduled = summary[summary["Schedule_Status"] == "Scheduled"]
    if scheduled.empty:
        grouped["Peak_Daily_Down_Planned"] = 0
    else:
        peak = (
            scheduled.groupby(["Customer_Name", "Scheduled_Day"]).size().groupby(level=0).max()
        )
        grouped["Peak_Daily_Down_Planned"] = (
            grouped["Customer_Name"].map(peak).fillna(0).astype(int)
        )
    columns = [
        "Customer_Name",
        "Total_Turbines",
        "Contract_Target_Availability",
        "Current_Down_Turbines",
        "Allowed_Total_Down",
        "Allowed_Additional_Down",
        "Contract_Month",
        "Contract_Total_Months",
        "Contract_Time_Factor",
    ]
    out = grouped.merge(limits[columns], on="Customer_Name", how="left")
    return out.sort_values("Contract_Time_Factor", ascending=False).reset_index(drop=True)
