"""Wind Turbine Maintenance Intelligence.

Multi-criteria prioritisation, constraint-based scheduling and explainable
backlog analysis for wind turbine maintenance operations.
"""

from windmaint.config import load_config
from windmaint.pipeline import RunResult, compare_schedulers, run

__version__ = "1.0.0"

__all__ = ["RunResult", "compare_schedulers", "load_config", "run", "__version__"]
