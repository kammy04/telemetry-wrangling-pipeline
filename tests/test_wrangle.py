import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wrangle  # noqa: E402


def raw_rows(rows):
    cols = ["timestamp", "system_id", "uptime_minutes", "cpu_usage_pct", "status", "error_code"]
    return pd.DataFrame([dict(zip(cols, r)) for r in rows]).astype(str).assign(source_file="t.csv")


def test_to_number_handles_percent_and_blanks():
    out = wrangle.to_number(pd.Series(["45%", " 52 ", "", "abc"]))
    assert out.iloc[0] == 45 and out.iloc[1] == 52
    assert out.iloc[2:].isna().all()


def test_clean_normalises_and_dedupes():
    raw = raw_rows([
        ("2025-09-08 00:00:00", "SYS-001", "60", "40", "UP", ""),
        ("2025-09-08 00:00:00", "SYS-001", "60", "40", " up ", ""),   # same record, messy status
        ("2025-09-08 01:00:00", "SYS-001", "75", "140", "down", "E-IO"),
        ("2025-09-08 02:00:00", "SYS-001", "", "33%", "UP", ""),
    ])
    df, fixes = wrangle.clean(raw)
    assert len(df) == 3
    assert fixes["duplicates_removed"] == 1
    assert set(df["status"]) == {"UP", "DOWN"}
    assert df.loc[1, "uptime_minutes"] == 60          # clipped
    assert pd.isna(df.loc[1, "cpu_usage_pct"])        # out of range -> null
    assert df.loc[2, "cpu_usage_pct"] == 33           # "33%" parsed
    assert pd.isna(df.loc[2, "uptime_minutes"])


def test_unknown_system_is_quarantined():
    raw = raw_rows([
        ("2025-09-08 00:00:00", "SYS-001", "60", "40", "UP", ""),
        ("2025-09-08 00:00:00", "SYS-999", "60", "40", "UP", ""),
        ("not a date", "SYS-001", "60", "40", "UP", ""),
    ])
    df, _ = wrangle.clean(raw)
    owners = pd.DataFrame({"system_id": ["SYS-001"], "manager_name": ["Ava"]})
    good, quarantine = wrangle.enrich_and_validate(df, owners)
    assert len(good) == 1
    assert sorted(quarantine["reason"]) == ["bad_timestamp", "unknown_system"]


def test_uptime_pct_ignores_missing_hours():
    raw = raw_rows([
        ("2025-09-08 00:00:00", "SYS-001", "60", "40", "UP", ""),
        ("2025-09-08 01:00:00", "SYS-001", "30", "40", "DOWN", "E-500"),
        ("2025-09-08 02:00:00", "SYS-001", "", "40", "UP", ""),
    ])
    df, _ = wrangle.clean(raw)
    owners = pd.DataFrame({"system_id": ["SYS-001"], "manager_name": ["Ava"]})
    good, _ = wrangle.enrich_and_validate(df, owners)
    kpi = wrangle.daily_kpis_by_system(good).iloc[0]
    assert kpi["uptime_pct"] == pytest.approx(75.0)   # 90 of 120 reported minutes
    assert kpi["errors"] == 1
    assert kpi["hours_down"] == 1
    assert kpi["hours_logged"] == 3


def test_end_to_end(tmp_path):
    logs = tmp_path / "raw_logs"
    logs.mkdir()
    raw_rows([("2025-09-08 00:00:00", "SYS-001", "60", "40", "UP", "")]).drop(columns="source_file") \
        .to_csv(logs / "raw_logs_20250908_00.csv", index=False)
    owners = tmp_path / "owners.csv"
    pd.DataFrame({"system_id": ["SYS-001"], "manager_name": ["Ava"]}).to_csv(owners, index=False)
    out = tmp_path / "out"
    result = wrangle.run(make_charts=False, logs_dir=str(logs), owners_csv=str(owners), out_dir=str(out))
    assert len(result["by_system"]) == 1
    for f in ["kpis_by_system.csv", "kpis_by_manager.csv", "data_quality_report.md", "telemetry.db"]:
        assert (out / f).exists()
