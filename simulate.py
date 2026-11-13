"""
simulate.py - generate a messy, realistic set of hourly machine-telemetry logs.

Each hour, every system writes one row: how many minutes it was up, its CPU
usage, its status and any error code. Real exports are never clean, so the
generator deliberately injects the problems the wrangler has to handle:

  * exact duplicate rows (double-sent records)
  * blank / missing values
  * numbers stored as text ("45%", " 52 ")
  * inconsistent status casing ("up", "Up ", "DOWN")
  * out-of-range readings (CPU 140%, uptime 75 min in a 60 min hour)
  * logs from systems that are not in the owners table

Usage:
    python simulate.py                      # 3 days, 10 systems, seed 42
    python simulate.py --days 7 --systems 25 --seed 7
"""

import argparse
import os
import random
from datetime import datetime, timedelta

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
LOGS_DIR = os.path.join(DATA_DIR, "raw_logs")

MANAGERS = ["Ava", "Ben", "Cara", "Dio", "Eli", "Fay", "Gus", "Hana", "Ivan", "Jae"]
ERROR_CODES = ["E-500", "E-429", "E-DB", "E-IO"]


def make_owners(n_systems):
    """One manager per system, round-robin when there are more systems than managers."""
    systems = [f"SYS-{i:03d}" for i in range(1, n_systems + 1)]
    managers = [MANAGERS[i % len(MANAGERS)] for i in range(n_systems)]
    return pd.DataFrame({"system_id": systems, "manager_name": managers})


def make_hour(ts, systems, rng, flaky):
    """Build the rows for a single hourly log file."""
    rows = []
    for sid in systems:
        # flaky systems go down more often, which makes the KPIs worth looking at
        p_down = 0.25 if sid in flaky else 0.03
        is_up = rng.random() > p_down

        uptime = rng.randint(56, 60) if is_up else rng.randint(0, 45)
        cpu = int(rng.gauss(48 if is_up else 12, 14))
        cpu = max(0, min(100, cpu))

        err = ""
        if not is_up or rng.random() < 0.08:
            err = rng.choice(ERROR_CODES)

        row = {
            "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "system_id": sid,
            "uptime_minutes": uptime,
            "cpu_usage_pct": cpu,
            "status": "UP" if is_up else "DOWN",
            "error_code": err,
        }

        # --- inject dirty data -------------------------------------------
        roll = rng.random()
        if roll < 0.03:
            row["cpu_usage_pct"] = f"{cpu}%"            # number stored as text
        elif roll < 0.05:
            row["uptime_minutes"] = ""                  # missing value
        elif roll < 0.06:
            row["cpu_usage_pct"] = rng.randint(101, 160)  # sensor glitch
        elif roll < 0.07:
            row["uptime_minutes"] = rng.randint(61, 90)   # impossible uptime

        if rng.random() < 0.10:
            row["status"] = rng.choice([row["status"].lower(), f" {row['status'].title()} "])

        rows.append(row)
        if rng.random() < 0.08:                         # double-sent record
            rows.append(dict(row))

    # a system that nobody owns shows up now and then
    if rng.random() < 0.15:
        rows.append({
            "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "system_id": "SYS-999",
            "uptime_minutes": 60,
            "cpu_usage_pct": 30,
            "status": "UP",
            "error_code": "",
        })
    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--systems", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--start", default="2025-09-08", help="first day, YYYY-MM-DD")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    os.makedirs(LOGS_DIR, exist_ok=True)

    # start from an empty folder so old runs don't mix in
    for f in os.listdir(LOGS_DIR):
        if f.startswith("raw_logs_") and f.endswith(".csv"):
            os.remove(os.path.join(LOGS_DIR, f))

    owners = make_owners(args.systems)
    owners.to_csv(os.path.join(DATA_DIR, "system_owners.csv"), index=False)
    systems = owners["system_id"].tolist()
    flaky = set(rng.sample(systems, k=max(1, len(systems) // 5)))

    start = datetime.strptime(args.start, "%Y-%m-%d")
    n_files = 0
    for h in range(args.days * 24):
        ts = start + timedelta(hours=h)
        df = make_hour(ts, systems, rng, flaky)
        df.to_csv(os.path.join(LOGS_DIR, f"raw_logs_{ts.strftime('%Y%m%d_%H')}.csv"), index=False)
        n_files += 1

    print(f"Wrote {n_files} hourly log files for {len(systems)} systems to {LOGS_DIR}")
    print(f"Flaky systems (hidden truth): {', '.join(sorted(flaky))}")


if __name__ == "__main__":
    main()
