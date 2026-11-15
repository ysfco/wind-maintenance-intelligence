"""Weather: workability rules, site status and an optional forecast client.

Weather never enters the priority score. It is a *feasibility constraint*:
it decides whether a slot can be used, not how important a turbine is.
"""

from __future__ import annotations

from typing import Any, Mapping

import pandas as pd

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
DEFAULT_WINDOWS: dict[str, tuple[int, int]] = {"Morning": (7, 12), "Afternoon": (12, 17)}

# rule key -> (column, comparison)
_LIMITS = {
    "max_wind_speed": ("Wind_Speed", "max"),
    "max_wind_gust": ("Wind_Gust", "max"),
    "max_precip_probability": ("Precip_Probability", "max"),
    "min_temperature": ("Temperature", "min"),
}


def derive_workability(weather: pd.DataFrame, rules: Mapping[str, float]) -> pd.Series:
    """Workable = every measured quantity is inside its limit.

    Columns that are absent are ignored; a missing measurement in a present
    column makes the slot not workable (unknown is not safe).
    """
    ok = pd.Series(True, index=weather.index)
    for key, (column, kind) in _LIMITS.items():
        if key not in rules or column not in weather.columns:
            continue
        values = weather[column]
        inside = values <= float(rules[key]) if kind == "max" else values >= float(rules[key])
        ok &= inside & values.notna()
    return ok


def flag_rule_conflicts(weather: pd.DataFrame, rules: Mapping[str, float]) -> pd.DataFrame:
    """Rows where the provided ``Workable`` flag disagrees with the rule profile."""
    if "Workable" not in weather.columns:
        return weather.iloc[0:0]
    derived = derive_workability(weather, rules)
    conflicts = weather[weather["Workable"].astype(bool) != derived].copy()
    conflicts["Rule_Says"] = derived[conflicts.index]
    return conflicts


def classify_site_weather(site_weather: pd.DataFrame, cfg: Mapping[str, Any]) -> pd.Series:
    """Coarse site status for the map (from the site-level snapshot)."""
    limits = cfg["weather"]["site_status"]

    def status(wind: float) -> str:
        if pd.isna(wind):
            return "Unknown"
        if wind >= float(limits["blocked_wind"]):
            return "Maintenance Blocked"
        if wind >= float(limits["risky_wind"]):
            return "Risky Conditions"
        return "Feasible"

    return site_weather["Wind_Speed"].map(status)


def hourly_to_windows(
    hourly: pd.DataFrame, windows: Mapping[str, tuple[int, int]] | None = None
) -> pd.DataFrame:
    """Aggregate hourly rows to scheduler slots, keeping the worst hour.

    ``hourly`` needs ``Site``, ``Time`` (datetime) and any of ``Wind_Speed``,
    ``Wind_Gust``, ``Precip_Probability``, ``Temperature``. Window bounds are
    local hours ``[start, end)``.
    """
    windows = dict(windows or DEFAULT_WINDOWS)
    frame = hourly.copy()
    frame["Time"] = pd.to_datetime(frame["Time"])
    frame["Date"] = frame["Time"].dt.strftime("%Y-%m-%d")
    hour = frame["Time"].dt.hour
    frame["Time_Window"] = None
    for name, (start, end) in windows.items():
        frame.loc[(hour >= start) & (hour < end), "Time_Window"] = name
    frame = frame.dropna(subset=["Time_Window"])

    how = {"Wind_Speed": "max", "Wind_Gust": "max", "Precip_Probability": "max", "Temperature": "min"}
    how = {col: fn for col, fn in how.items() if col in frame.columns}
    out = frame.groupby(["Site", "Date", "Time_Window"], sort=True).agg(how).reset_index()
    order = {name: i for i, name in enumerate(windows)}
    out = out.sort_values(
        ["Site", "Date", "Time_Window"], key=lambda s: s.map(order) if s.name == "Time_Window" else s
    )
    return out.reset_index(drop=True)


def parse_open_meteo(payload: Mapping[str, Any], site: str, wind_variable: str) -> pd.DataFrame:
    """Convert one Open-Meteo JSON response to the hourly schema used above."""
    hourly = payload["hourly"]
    frame = pd.DataFrame({"Site": site, "Time": pd.to_datetime(hourly["time"])})
    mapping = {
        wind_variable: "Wind_Speed",
        "wind_gusts_10m": "Wind_Gust",
        "precipitation_probability": "Precip_Probability",
        "temperature_2m": "Temperature",
    }
    for source, target in mapping.items():
        if source in hourly:
            frame[target] = pd.to_numeric(pd.Series(hourly[source]), errors="coerce")
    return frame


def fetch_forecast(
    site_locations: pd.DataFrame,
    days: int = 3,
    rules: Mapping[str, float] | None = None,
    wind_variable: str = "wind_speed_80m",
    windows: Mapping[str, tuple[int, int]] | None = None,
    timeout: float = 20.0,
) -> pd.DataFrame:
    """Download an hourly forecast per site from Open-Meteo (no API key) and
    return a table in the ``weather.csv`` schema, ``Workable`` included.

    ``wind_speed_80m`` is used by default because work happens at hub height;
    10 m wind understates it. Network errors propagate to the caller, which
    should keep using the last good ``weather.csv``.
    """
    import requests  # local import: the core engine has no network dependency

    frames = []
    for row in site_locations.itertuples(index=False):
        response = requests.get(
            OPEN_METEO_URL,
            params={
                "latitude": row.Latitude,
                "longitude": row.Longitude,
                "hourly": f"{wind_variable},wind_gusts_10m,precipitation_probability,temperature_2m",
                "wind_speed_unit": "ms",
                "timezone": "auto",
                "forecast_days": days,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        frames.append(parse_open_meteo(response.json(), row.Site, wind_variable))

    table = hourly_to_windows(pd.concat(frames, ignore_index=True), windows)
    if rules is not None:
        table["Workable"] = derive_workability(table, rules).map({True: "Yes", False: "No"})
    return table
