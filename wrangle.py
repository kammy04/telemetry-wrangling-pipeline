"""
wrangle.py - turn a folder of messy hourly telemetry logs into clean,
dashboard-ready KPI tables.

Pipeline:
    1) COLLECT   read every raw_logs_*.csv into one table
    2) CLEAN     fix types, normalise text, drop duplicates, handle bad readings
    3) ENRICH    join each system to the manager who owns it
    4) VALIDATE  quarantine rows that can't be trusted, record every fix made
    5) TRANSFORM roll hourly rows up to daily KPIs per system and per manager
    6) STORE     write CSVs + a SQLite database + charts + a data-quality report

Usage:
    python wrangle.py                 # read data/, write outputs/
    python wrangle.py --no-charts     # skip the matplotlib step
"""

import argparse
import os
import sqlite3
from glob import glob

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
LOGS_DIR = os.path.join(DATA_DIR, "raw_logs")
OWNERS_CSV = os.path.join(DATA_DIR, "system_owners.csv")
OUT_DIR = os.path.join(BASE_DIR, "outputs")

VALID_STATUS = {"UP", "DOWN"}
MINUTES_PER_HOUR = 60


# ---------------------------------------------------------------------------
# 1) COLLECT
# ---------------------------------------------------------------------------
def collect(logs_dir=LOGS_DIR):
    files = sorted(glob(os.path.join(logs_dir, "raw_logs_*.csv")))
    if not files:
        raise SystemExit(f"No raw log files found in {logs_dir}. Run `python simulate.py` first.")
    # read everything as text first: we decide the types, not pandas
    frames = [pd.read_csv(f, dtype=str, keep_default_na=False).assign(source_file=os.path.basename(f))
              for f in files]
    return pd.concat(frames, ignore_index=True), len(files)


# ---------------------------------------------------------------------------
# 2) CLEAN
# ---------------------------------------------------------------------------
def to_number(series):
    """'45%', ' 52 ', '' -> 45.0, 52.0, NaN"""
    cleaned = series.astype(str).str.strip().str.rstrip("%").replace("", None)
    return pd.to_numeric(cleaned, errors="coerce")


def clean(raw):
    """Return (clean_df, fixes) where fixes counts every correction made."""
    df = raw.copy()
    fixes = {}

    df["system_id"] = df["system_id"].str.strip().str.upper()
    df["timestamp"] = pd.to_datetime(df["timestamp"].str.strip(), errors="coerce")

    # numbers that arrived as text
    fixes["cpu_stored_as_text"] = int(df["cpu_usage_pct"].str.contains("%", na=False).sum())
    df["uptime_minutes"] = to_number(df["uptime_minutes"])
    df["cpu_usage_pct"] = to_number(df["cpu_usage_pct"])
    fixes["uptime_missing"] = int(df["uptime_minutes"].isna().sum())

    # status casing / whitespace
    status_raw = df["status"].copy()
    df["status"] = df["status"].str.strip().str.upper()
    fixes["status_normalised"] = int((status_raw != df["status"]).sum())
    df.loc[~df["status"].isin(VALID_STATUS), "status"] = "UNKNOWN"

    df["error_code"] = df["error_code"].fillna("").str.strip().str.upper()

    # impossible readings: keep the row, null out the bad value
    bad_cpu = (df["cpu_usage_pct"] < 0) | (df["cpu_usage_pct"] > 100)
    fixes["cpu_out_of_range"] = int(bad_cpu.sum())
    df.loc[bad_cpu, "cpu_usage_pct"] = float("nan")

    bad_uptime = df["uptime_minutes"] > MINUTES_PER_HOUR
    fixes["uptime_over_60_clipped"] = int(bad_uptime.sum())
    df.loc[bad_uptime, "uptime_minutes"] = MINUTES_PER_HOUR

    # duplicates: same system + same hour = same record (checked after normalising,
    # so " up " and "UP" are recognised as the same row)
    before = len(df)
    df = df.drop_duplicates(subset=["timestamp", "system_id"], keep="first")
    fixes["duplicates_removed"] = before - len(df)

    return df.reset_index(drop=True), fixes


# ---------------------------------------------------------------------------
# 3) ENRICH + 4) VALIDATE
# ---------------------------------------------------------------------------
def enrich_and_validate(df, owners):
    """Attach manager names; move rows we can't trust into a quarantine table."""
    df = df.merge(owners, on="system_id", how="left")

    reasons = pd.Series("", index=df.index)
    reasons[df["timestamp"].isna()] += "bad_timestamp;"
    reasons[df["manager_name"].isna()] += "unknown_system;"

    quarantine = df[reasons != ""].assign(reason=reasons[reasons != ""].str.rstrip(";"))
    good = df[reasons == ""].copy()
    return good.reset_index(drop=True), quarantine.reset_index(drop=True)


# ---------------------------------------------------------------------------
# 5) TRANSFORM
# ---------------------------------------------------------------------------
def daily_kpis_by_system(df):
    df = df.assign(date=df["timestamp"].dt.date, has_error=df["error_code"] != "")
    daily = df.groupby(["system_id", "manager_name", "date"]).agg(
        hours_logged=("timestamp", "count"),
        hours_with_uptime=("uptime_minutes", "count"),
        uptime_minutes=("uptime_minutes", "sum"),
        hours_down=("status", lambda s: int((s == "DOWN").sum())),
        avg_cpu=("cpu_usage_pct", "mean"),
        peak_cpu=("cpu_usage_pct", "max"),
        errors=("has_error", "sum"),
    ).reset_index()

    # uptime % is measured only over hours where uptime was actually reported
    daily["uptime_pct"] = (daily["uptime_minutes"] / (daily["hours_with_uptime"] * MINUTES_PER_HOUR) * 100).round(2)
    daily["avg_cpu"] = daily["avg_cpu"].round(2)
    daily["errors"] = daily["errors"].astype(int)
    return daily.drop(columns=["uptime_minutes"]).sort_values(["date", "system_id"]).reset_index(drop=True)


def daily_kpis_by_manager(by_system):
    return by_system.groupby(["manager_name", "date"]).agg(
        systems=("system_id", "nunique"),
        uptime_avg=("uptime_pct", "mean"),
        cpu_avg=("avg_cpu", "mean"),
        total_errors=("errors", "sum"),
        hours_down=("hours_down", "sum"),
    ).round(2).reset_index().sort_values(["date", "manager_name"]).reset_index(drop=True)


def error_heatmap(df):
    errs = df[df["error_code"] != ""].assign(date=lambda d: d["timestamp"].dt.date)
    return errs.pivot_table(index="system_id", columns="date", values="error_code",
                            aggfunc="count", fill_value=0)


def error_breakdown(df):
    return (df[df["error_code"] != ""].groupby(["system_id", "error_code"]).size()
            .unstack(fill_value=0))


# ---------------------------------------------------------------------------
# 6) STORE
# ---------------------------------------------------------------------------
def write_quality_report(path, n_files, n_raw, fixes, quarantine, n_clean):
    lines = [
        "# Data quality report",
        "",
        f"- Files processed: **{n_files}**",
        f"- Raw rows read: **{n_raw}**",
        f"- Clean rows kept: **{n_clean}**",
        f"- Rows quarantined: **{len(quarantine)}**",
        "",
        "| Issue | Rows affected | Action |",
        "|---|---:|---|",
        f"| Exact / near duplicates | {fixes['duplicates_removed']} | dropped (kept first) |",
        f"| CPU stored as text (`45%`) | {fixes['cpu_stored_as_text']} | parsed to number |",
        f"| Status casing / whitespace | {fixes['status_normalised']} | normalised to UP / DOWN |",
        f"| CPU outside 0-100 | {fixes['cpu_out_of_range']} | set to null, excluded from averages |",
        f"| Uptime over 60 min/hour | {fixes['uptime_over_60_clipped']} | clipped to 60 |",
        f"| Uptime missing | {fixes['uptime_missing']} | excluded from uptime % |",
    ]
    if len(quarantine):
        for reason, n in quarantine["reason"].value_counts().items():
            lines.append(f"| Quarantined: `{reason}` | {n} | moved to quarantine.csv |")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def write_sqlite(path, tables):
    if os.path.exists(path):
        os.remove(path)
    with sqlite3.connect(path) as con:
        for name, frame in tables.items():
            frame.to_sql(name, con, index=False)


def run(make_charts=True, logs_dir=LOGS_DIR, owners_csv=OWNERS_CSV, out_dir=OUT_DIR):
    os.makedirs(out_dir, exist_ok=True)

    raw, n_files = collect(logs_dir)
    cleaned, fixes = clean(raw)
    owners = pd.read_csv(owners_csv)
    good, quarantine = enrich_and_validate(cleaned, owners)

    by_system = daily_kpis_by_system(good)
    by_manager = daily_kpis_by_manager(by_system)
    heatmap = error_heatmap(good)
    breakdown = error_breakdown(good)

    good.drop(columns=["source_file"]).to_csv(os.path.join(out_dir, "clean_logs.csv"), index=False)
    by_system.to_csv(os.path.join(out_dir, "kpis_by_system.csv"), index=False)
    by_manager.to_csv(os.path.join(out_dir, "kpis_by_manager.csv"), index=False)
    heatmap.to_csv(os.path.join(out_dir, "error_heatmap.csv"))
    breakdown.to_csv(os.path.join(out_dir, "error_breakdown.csv"))
    quarantine.to_csv(os.path.join(out_dir, "quarantine.csv"), index=False)
    write_quality_report(os.path.join(out_dir, "data_quality_report.md"),
                         n_files, len(raw), fixes, quarantine, len(good))
    write_sqlite(os.path.join(out_dir, "telemetry.db"), {
        "clean_logs": good.drop(columns=["source_file"]).assign(timestamp=lambda d: d["timestamp"].astype(str)),
        "kpis_by_system": by_system.assign(date=lambda d: d["date"].astype(str)),
        "kpis_by_manager": by_manager.assign(date=lambda d: d["date"].astype(str)),
    })

    if make_charts:
        from charts import make_all
        make_all(good, by_system, heatmap, os.path.join(out_dir, "charts"))

    print(f"Processed {n_files} files: {len(raw)} raw rows -> {len(good)} clean rows "
          f"({fixes['duplicates_removed']} duplicates, {len(quarantine)} quarantined).")
    print(f"Outputs written to {out_dir}")
    return {"clean": good, "by_system": by_system, "by_manager": by_manager,
            "quarantine": quarantine, "fixes": fixes}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Wrangle hourly telemetry logs into KPI tables.")
    p.add_argument("--no-charts", action="store_true")
    run(make_charts=not p.parse_args().no_charts)
