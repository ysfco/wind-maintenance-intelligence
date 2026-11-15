from __future__ import annotations

import shutil

import pandas as pd
import pytest
import yaml
from conftest import ROOT, SAMPLE

from windmaint.cli import main
from windmaint.config import DEFAULTS, ConfigError, load_config
from windmaint.export import export
from windmaint.io import DataValidationError, load_dataset, read_table
from windmaint.pipeline import run


def test_default_yaml_matches_code_defaults():
    with open(ROOT / "config" / "default.yaml", encoding="utf-8") as handle:
        assert yaml.safe_load(handle) == DEFAULTS


def test_config_validation():
    with pytest.raises(ConfigError, match="sum to 1"):
        load_config(overrides={"scoring": {"weights": {"technical": 0.9}}})
    with pytest.raises(ConfigError, match="greedy|milp"):
        load_config(overrides={"scheduling": {"scheduler": "magic"}})
    cfg = load_config(overrides={"scheduling": {"horizon_days": 5}})
    assert cfg["scheduling"]["horizon_days"] == 5
    assert DEFAULTS["scheduling"]["horizon_days"] == 10  # defaults are not mutated


def test_read_table_detects_delimiter_encoding_and_whitespace(tmp_path):
    semicolon = tmp_path / "a.csv"
    semicolon.write_bytes("Ad ;Değer\r\n Çeşme ;1\r\n;2\r\n".encode("cp1254"))
    df = read_table(semicolon)
    assert df.columns.tolist() == ["Ad", "Değer"]
    assert df.loc[0, "Ad"] == "Çeşme"
    assert df.loc[1, "Ad"] is None  # missing stays missing, not the string "nan"

    comma = tmp_path / "b.csv"
    comma.write_text("﻿Site,Latitude\nA,38.4\n", encoding="utf-8")
    assert read_table(comma).columns.tolist() == ["Site", "Latitude"]


def test_sample_loads_clean(sample):
    assert not [i for i in sample.issues if i.level == "error"]
    assert sample.weather["Date"].iloc[0] == "2026-04-15"  # DD.MM.YYYY -> ISO
    assert sample.weather["Workable"].dtype == bool
    assert sample.site_locations is not None


def test_legacy_teams_file_is_accepted(tmp_path):
    shutil.copytree(SAMPLE, tmp_path / "d")
    resources = pd.read_csv(tmp_path / "d" / "resources.csv", sep=";")
    (tmp_path / "d" / "resources.csv").unlink()
    resources.rename(columns={"Resource_ID": "Team_ID"})[
        ["Team_ID", "Region", "Daily_Capacity"]
    ].to_csv(tmp_path / "d" / "teams.csv", sep=";", index=False)
    ds = load_dataset(tmp_path / "d")
    assert ds.resources["Resource_ID"].tolist() == ["Team_1", "Team_2", "Team_3", "Team_4"]
    assert (ds.resources["Contractor"] == "In-house").all()


def test_validation_reports_all_problems(tmp_path):
    shutil.copytree(SAMPLE, tmp_path / "d")
    damages = tmp_path / "d" / "damages.csv"
    damages.write_text(damages.read_text() + "D99;T99;9;1\n")
    (tmp_path / "d" / "turbines.csv").write_text("Turbine_ID;Site;Region\nT1;A;North\nT1;A;North\n")
    with pytest.raises(DataValidationError) as error:
        load_dataset(tmp_path / "d")
    text = str(error.value)
    assert "duplicate Turbine_ID" in text
    assert "unknown Turbine_ID" in text
    assert "must be within 0-5" in text

    lenient = load_dataset(tmp_path / "d", strict=False)
    assert len([i for i in lenient.issues if i.level == "error"]) >= 3


def test_missing_file_is_an_error(tmp_path):
    shutil.copytree(SAMPLE, tmp_path / "d")
    (tmp_path / "d" / "weather.csv").unlink()
    with pytest.raises(DataValidationError, match="file not found: weather.csv"):
        load_dataset(tmp_path / "d")


def test_export_writes_workbook_and_csv(sample, cfg, tmp_path):
    result = run(cfg=cfg, dataset=sample)
    paths = export(result, tmp_path)
    book = pd.ExcelFile(paths["xlsx"])
    assert book.sheet_names == [
        "Summary",
        "Schedule",
        "Backlog",
        "Recommendations",
        "Customers",
        "Slot_Diagnostics",
        "Run_Info",
    ]
    summary = pd.read_excel(paths["xlsx"], sheet_name="Summary")
    assert summary["Turbine_ID"].tolist() == ["T5", "T2", "T4", "T3", "T1"]
    assert len(pd.read_excel(paths["xlsx"], sheet_name="Backlog")) == 3
    assert pd.read_csv(paths["plan"], sep=";").shape[0] == 5


def test_cli_commands(tmp_path, capsys):
    data = ["--data", str(SAMPLE)]
    assert main([*data, "validate"]) == 0
    assert main([*data, "run", "--scheduler", "milp", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "wind_planning_output.xlsx").exists()
    assert main([*data, "compare"]) == 0
    assert main([*data, "sensitivity", "--draws", "50"]) == 0
    out = capsys.readouterr().out
    assert "3/5 scheduled" in out and "GREEDY vs MILP" in out
