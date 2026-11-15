# Data dictionary

Input is a folder of CSV files. Delimiter (`;`, `,` or tab) and encoding
(UTF-8 or Windows-1254) are detected per file. Extra columns are ignored.
Run `windmaint --data <folder> validate` to check a folder.

Two synthetic sets ship with the repository: `data/fleet` (150 turbines; rebuilt
with `python scripts/generate_fleet.py`) and `data/sample` (5 turbines).

## Required files

### `turbines.csv`
| Column | Type | Meaning |
|---|---|---|
| `Turbine_ID` | text, unique | Turbine identifier |
| `Site` | text | Wind farm; joins to contracts, weather, locations |
| `Region` | text | Service region; decides which resources can work on it |
| `Rated_MW`, `Commissioned` | optional | Shown only; not used by the model yet |

### `damages.csv`
One row per damage. Only turbines with at least one row become jobs.
| Column | Type | Meaning |
|---|---|---|
| `Damage_ID` | text, unique | |
| `Turbine_ID` | text | Must exist in `turbines.csv` |
| `Current_Severity` | 0–5 | Severity at the latest inspection |
| `Previous_Severity` | 0–5 | Severity at the previous inspection (0 = new) |
| `Component`, `Damage_Type` | optional | Shown on the Turbine detail page |
| `Inspection_Date`, `Previous_Inspection_Date` | optional | Kept for a future growth-rate model |

### `weather.csv`
One row per site, day and time window.
| Column | Type | Meaning |
|---|---|---|
| `Site` | text | |
| `Date` | `DD.MM.YYYY`, `YYYY-MM-DD` or `DD/MM/YYYY` | |
| `Time_Window` | text | Must match `scheduling.time_windows` (default `Morning`, `Afternoon`) |
| `Workable` | Yes / No | Required unless `Wind_Speed` is given |
| `Wind_Speed` | m/s, optional | Used when `weather.workability_source: rules` |
| `Wind_Gust`, `Precip_Probability`, `Temperature` | optional | Additional rule inputs |

The first `scheduling.horizon_days` dates (default 10) form the planning
horizon; if the file holds fewer dates, all of them are used.

### `resources.csv` (or legacy `teams.csv`)
| Column | Type | Meaning |
|---|---|---|
| `Resource_ID` (or `Team_ID`) | text, unique | |
| `Region` | text | Region covered. Several: `North\|East`. All: `*` |
| `Daily_Capacity` | integer ≥ 0 | Jobs per day |
| `Resource_Type` | optional | e.g. blade crew, rope access, crane |
| `Contractor` | optional | In-house or contractor name |

### `planning_rules.csv`
| Column | Type | Meaning |
|---|---|---|
| `Category` | `Critical`, `Non-Critical` or `Default` | A `Default` row is required |
| `Min_Increasing_Count`, `Max_Increasing_Count` | integer | Inclusive range of growing damages |
| `Due_Days` | number > 0 | Recommended days until intervention |

The first matching row wins; otherwise the `Default` row applies.

### `site_contracts.csv`
| Column | Type | Meaning |
|---|---|---|
| `Site` | text, unique | |
| `Customer_Name` | text | Joins to `customer_contracts.csv` |
| `Priority_Boost` | number, 0–2 by default | Soft priority increase for sites below target |
| `Contract_Target_Availability`, `Current_Availability`, `Contract_Status` | optional | Shown in explanations |

### `customer_contracts.csv`
| Column | Type | Meaning |
|---|---|---|
| `Customer_Name` | text, unique | |
| `Total_Turbines` | integer | Turbines under the contract |
| `Contract_Target_Availability` | percent | e.g. `97` |
| `Current_Down_Turbines` | integer | Turbines currently not available |
| `Contract_Month`, `Contract_Total_Months` | integer | Position inside the contract period |

## Optional files

### `site_locations.csv`
`Site`, `Latitude`, `Longitude` (optional `Province`) — enables the map and `fetch-weather`.

### `site_weather.csv`
`Site`, `Wind_Speed` (m/s), optional `Rain_Risk`, `Temperature` — a snapshot shown
on the map only. The scheduler never reads it.

## Output columns (`plan.csv`, sheet *Summary*)

| Column | Meaning |
|---|---|
| `Priority_Rank` | Processing order, 1 = first |
| `Category` | `Critical` if any damage is at or above the critical severity |
| `Max_Severity`, `Total_Damage_Count`, `Critical_Count`, `Increasing_Count`, `Delta_Sum` | Damage metrics per turbine |
| `Technical_Score`, `Contract_Score`, `Due_Score` | Raw component scores |
| `*_Normalized` | Components on a 0–1 scale |
| `Technical_Contribution`, `Contract_Contribution`, `Due_Contribution` | Weight × normalised component; they sum to `Final_Priority_Score` |
| `Final_Priority_Score` | Relative priority index, 0–1. Not a probability |
| `Priority_Band` | High / Medium / Low display band |
| `Contract_Time_Factor` | Elapsed share of the contract period |
| `Allowed_Additional_Down` | Customer's remaining daily outage budget (empty = no cap known) |
| `Schedule_Status` | `Scheduled` or `Backlog` |
| `Scheduled_Day`, `Scheduled_Day_Index`, `Scheduled_Window`, `Assigned_Resource` | The slot |
| `Due_Status` | `On Time`, `Late` or `Backlog` |
| `Contract_Override` | Scheduled beyond the availability cap (only with `critical_override`) |
| `Skip_Reason` | Every rejected slot with all the constraints that blocked it |
| `Weather_Blocks`, `Contract_Blocks`, `Resource_Blocks`, `NoData_Blocks` | Blocked slots by constraint (backlog only) |
| `Primary_Blocker` | Constraint that blocked the most slots |
| `Near_Miss_Slot`, `Near_Miss_Blocker` | A slot that failed on exactly one constraint |
| `Actionable` | The near miss failed on contract or resources, not weather |
