"""Data loading and validation.

Input is a folder of CSV files (see ``docs/data-dictionary.md``). Delimiter
(``;`` ``,`` or tab) and encoding (UTF-8 / Windows-1254) are detected per file,
because spreadsheets exported on Turkish-locale machines mix both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd


class DataValidationError(ValueError):
    """Raised when the input data cannot be used. Carries all issues found."""

    def __init__(self, issues: list[Issue]):
        self.issues = issues
        errors = [i for i in issues if i.level == "error"]
        super().__init__(
            "Input data is invalid:\n" + "\n".join(f"  - [{i.table}] {i.message}" for i in errors)
        )


@dataclass(frozen=True)
class Issue:
    level: str  # "error" | "warning"
    table: str
    message: str


@dataclass
class Dataset:
    turbines: pd.DataFrame
    damages: pd.DataFrame
    weather: pd.DataFrame
    resources: pd.DataFrame
    planning_rules: pd.DataFrame
    site_contracts: pd.DataFrame
    customer_contracts: pd.DataFrame
    site_locations: pd.DataFrame | None = None
    site_weather: pd.DataFrame | None = None
    issues: list[Issue] = field(default_factory=list)


# table -> accepted file names (first match wins)
FILES: dict[str, list[str]] = {
    "turbines": ["turbines.csv"],
    "damages": ["damages.csv"],
    "weather": ["weather.csv"],
    "resources": ["resources.csv", "teams.csv"],
    "planning_rules": ["planning_rules.csv"],
    "site_contracts": ["site_contracts.csv"],
    "customer_contracts": ["customer_contracts.csv"],
}
OPTIONAL_FILES: dict[str, list[str]] = {
    "site_locations": ["site_locations.csv"],
    "site_weather": ["site_weather.csv"],
}

REQUIRED_COLUMNS: dict[str, list[str]] = {
    "turbines": ["Turbine_ID", "Site", "Region"],
    "damages": ["Damage_ID", "Turbine_ID", "Current_Severity", "Previous_Severity"],
    "weather": ["Site", "Date", "Time_Window"],
    "resources": ["Resource_ID", "Region", "Daily_Capacity"],
    "planning_rules": ["Category", "Min_Increasing_Count", "Max_Increasing_Count", "Due_Days"],
    "site_contracts": ["Site", "Customer_Name", "Priority_Boost"],
    "customer_contracts": [
        "Customer_Name",
        "Total_Turbines",
        "Contract_Target_Availability",
        "Current_Down_Turbines",
        "Contract_Month",
        "Contract_Total_Months",
    ],
    "site_locations": ["Site", "Latitude", "Longitude"],
    "site_weather": ["Site", "Wind_Speed"],
}

NUMERIC_COLUMNS: dict[str, list[str]] = {
    "damages": ["Current_Severity", "Previous_Severity"],
    "weather": ["Wind_Speed", "Wind_Gust", "Precip_Probability", "Temperature"],
    "resources": ["Daily_Capacity"],
    "planning_rules": ["Min_Increasing_Count", "Max_Increasing_Count", "Due_Days"],
    "site_contracts": ["Priority_Boost", "Contract_Target_Availability", "Current_Availability"],
    "customer_contracts": REQUIRED_COLUMNS["customer_contracts"][1:],
    "site_locations": ["Latitude", "Longitude"],
    "site_weather": ["Wind_Speed", "Temperature"],
}

_DATE_FORMATS = ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y")
_TRUE = {"yes", "y", "true", "1", "evet", "workable"}
_FALSE = {"no", "n", "false", "0", "hayir", "hayır", "not workable"}


def read_table(path: str | Path) -> pd.DataFrame:
    """Read a delimited text file, detecting delimiter and encoding.

    Header names and string cells are stripped; missing values stay missing
    (the legacy script turned them into the literal string ``"nan"``).
    """
    path = Path(path)
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1254"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - cp1254 decodes almost any byte sequence
        raise ValueError(f"Cannot decode {path}")

    header = text.splitlines()[0] if text.strip() else ""
    delimiter = max((";", ",", "\t"), key=header.count)
    df = pd.read_csv(path, sep=delimiter, encoding=encoding)
    df = df.dropna(how="all").reset_index(drop=True)
    df.columns = [str(c).strip() for c in df.columns]
    for col in df.columns:
        if not pd.api.types.is_numeric_dtype(df[col]) and not pd.api.types.is_bool_dtype(df[col]):
            df[col] = df[col].astype("string").str.strip().astype(object)
            df[col] = df[col].where(df[col].notna(), None)
    return df


def _find(data_dir: Path, names: list[str]) -> Path | None:
    for name in names:
        candidate = data_dir / name
        if candidate.exists():
            return candidate
    return None


def _parse_dates(series: pd.Series) -> pd.Series | None:
    """Return ISO ``YYYY-MM-DD`` strings, or ``None`` if no known format fits."""
    for fmt in _DATE_FORMATS:
        parsed = pd.to_datetime(series, format=fmt, errors="coerce")
        if parsed.notna().all():
            return parsed.dt.strftime("%Y-%m-%d")
    return None


def _to_bool(value: object) -> bool | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return None


def load_dataset(data_dir: str | Path, strict: bool = True) -> Dataset:
    """Load, clean and validate all input tables from ``data_dir``.

    With ``strict=True`` (default) a :class:`DataValidationError` is raised when
    any error-level issue is found. Warnings are returned in ``Dataset.issues``.
    """
    data_dir = Path(data_dir)
    issues: list[Issue] = []
    tables: dict[str, pd.DataFrame | None] = {}

    for name, candidates in {**FILES, **OPTIONAL_FILES}.items():
        path = _find(data_dir, candidates)
        if path is None:
            tables[name] = None
            if name in FILES:
                issues.append(Issue("error", name, f"file not found: {' or '.join(candidates)}"))
            continue
        df = read_table(path)
        if name == "resources" and "Resource_ID" not in df.columns and "Team_ID" in df.columns:
            df = df.rename(columns={"Team_ID": "Resource_ID"})
        missing = [c for c in REQUIRED_COLUMNS[name] if c not in df.columns]
        if missing:
            issues.append(Issue("error", name, f"missing columns: {', '.join(missing)}"))
            tables[name] = None
            continue
        for col in NUMERIC_COLUMNS.get(name, []):
            if col in df.columns:
                converted = pd.to_numeric(df[col], errors="coerce")
                bad = int((converted.isna() & df[col].notna()).sum())
                if bad:
                    issues.append(Issue("error", name, f"{bad} non-numeric value(s) in {col}"))
                df[col] = converted
        tables[name] = df

    if any(i.level == "error" for i in issues):
        if strict:
            raise DataValidationError(issues)
        return Dataset(*(tables.get(k) for k in FILES), issues=issues)  # type: ignore[arg-type]

    weather = tables["weather"]
    assert weather is not None
    dates = _parse_dates(weather["Date"])
    if dates is None:
        issues.append(
            Issue("error", "weather", "Date must be DD.MM.YYYY, YYYY-MM-DD or DD/MM/YYYY")
        )
    else:
        weather["Date"] = dates
    if "Workable" in weather.columns:
        flags = weather["Workable"].map(_to_bool)
        unknown = int(flags.isna().sum())
        if unknown:
            issues.append(
                Issue("warning", "weather", f"{unknown} Workable value(s) not Yes/No; treated as No")
            )
        weather["Workable"] = flags.fillna(False).astype(bool)
    elif "Wind_Speed" not in weather.columns:
        issues.append(Issue("error", "weather", "needs a Workable or a Wind_Speed column"))

    resources = tables["resources"]
    assert resources is not None
    for col, default in (("Resource_Type", "Crew"), ("Contractor", "In-house")):
        if col not in resources.columns:
            resources[col] = default

    dataset = Dataset(
        turbines=tables["turbines"],  # type: ignore[arg-type]
        damages=tables["damages"],  # type: ignore[arg-type]
        weather=weather,
        resources=resources,
        planning_rules=tables["planning_rules"],  # type: ignore[arg-type]
        site_contracts=tables["site_contracts"],  # type: ignore[arg-type]
        customer_contracts=tables["customer_contracts"],  # type: ignore[arg-type]
        site_locations=tables["site_locations"],
        site_weather=tables["site_weather"],
        issues=issues,
    )
    issues.extend(validate(dataset))
    if strict and any(i.level == "error" for i in issues):
        raise DataValidationError(issues)
    return dataset


def _duplicates(df: pd.DataFrame, keys: list[str]) -> list[str]:
    dup = df[df.duplicated(keys, keep=False)]
    return sorted({" / ".join(str(v) for v in row) for row in dup[keys].itertuples(index=False)})


def validate(ds: Dataset) -> list[Issue]:
    """Cross-table consistency checks. Returns issues; raises nothing."""
    issues: list[Issue] = []

    def err(table: str, message: str) -> None:
        issues.append(Issue("error", table, message))

    def warn(table: str, message: str) -> None:
        issues.append(Issue("warning", table, message))

    for table, df, keys in (
        ("turbines", ds.turbines, ["Turbine_ID"]),
        ("damages", ds.damages, ["Damage_ID"]),
        ("resources", ds.resources, ["Resource_ID"]),
        ("site_contracts", ds.site_contracts, ["Site"]),
        ("customer_contracts", ds.customer_contracts, ["Customer_Name"]),
        ("weather", ds.weather, ["Site", "Date", "Time_Window"]),
    ):
        dup = _duplicates(df, keys)
        if dup:
            err(table, f"duplicate {'+'.join(keys)}: {', '.join(dup[:5])}")

    known_turbines = set(ds.turbines["Turbine_ID"])
    orphan = sorted(set(ds.damages["Turbine_ID"]) - known_turbines)
    if orphan:
        err("damages", f"unknown Turbine_ID: {', '.join(map(str, orphan[:5]))}")

    for col in ("Current_Severity", "Previous_Severity"):
        values = ds.damages[col]
        if values.isna().any():
            err("damages", f"{col} has missing values")
        elif ((values < 0) | (values > 5)).any():
            err("damages", f"{col} must be within 0-5")

    undamaged = sorted(known_turbines - set(ds.damages["Turbine_ID"]))
    if undamaged:
        warn("turbines", f"{len(undamaged)} turbine(s) without damage records are not planned")

    sites = set(ds.turbines["Site"])
    no_contract = sorted(sites - set(ds.site_contracts["Site"]))
    if no_contract:
        warn("site_contracts", f"no contract for site(s) {', '.join(no_contract)}; boost = 0")
    customers = set(ds.site_contracts["Customer_Name"].dropna())
    no_limits = sorted(customers - set(ds.customer_contracts["Customer_Name"]))
    if no_limits:
        warn(
            "customer_contracts",
            f"no row for {', '.join(no_limits)}; availability cap not enforced for them",
        )

    cc = ds.customer_contracts
    if (cc["Contract_Total_Months"] <= 0).any():
        err("customer_contracts", "Contract_Total_Months must be > 0")
    elif (cc["Contract_Month"] > cc["Contract_Total_Months"]).any():
        warn("customer_contracts", "Contract_Month exceeds Contract_Total_Months; factor capped at 1")
    if ((cc["Contract_Target_Availability"] <= 0) | (cc["Contract_Target_Availability"] > 100)).any():
        err("customer_contracts", "Contract_Target_Availability must be a percentage in (0, 100]")

    rules = ds.planning_rules
    if not (rules["Category"] == "Default").any():
        err("planning_rules", "a 'Default' rule is required")
    if (rules["Due_Days"] <= 0).any():
        err("planning_rules", "Due_Days must be > 0")

    if (ds.resources["Daily_Capacity"] < 0).any():
        err("resources", "Daily_Capacity must be >= 0")
    covered = set()
    for region in ds.resources["Region"].dropna():
        covered.update(part.strip() for part in str(region).split("|"))
    if "*" not in covered:
        uncovered = sorted(set(ds.turbines["Region"].dropna()) - covered)
        if uncovered:
            warn("resources", f"no resource covers region(s): {', '.join(uncovered)}")

    no_weather = sorted(sites - set(ds.weather["Site"]))
    if no_weather:
        warn("weather", f"no weather rows for site(s): {', '.join(no_weather)}")

    if ds.site_locations is not None:
        loc = ds.site_locations
        bad = loc[~loc["Latitude"].between(-90, 90) | ~loc["Longitude"].between(-180, 180)]
        if not bad.empty:
            warn("site_locations", f"invalid coordinates for: {', '.join(bad['Site'].astype(str))}")
    return issues
