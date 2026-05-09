"""
Step 1: Analyse Kaggle Pump Sensor Dataset
- Read sensor.csv
- Identify which sensors have a clear signal before/after BROKEN
- Group 52 sensors into 4 representative groups
- Export sensor_groups.json for use by other scripts

Usage:
    python scripts/analyze_sensors.py --csv data/sensor.csv
"""

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd


# ─── Default sensor group configuration ─────────────────────────────────────
# Based on physical properties of industrial pumps:
#   • Vibration:   sensor_00–12 (mechanical vibration)
#   • Temperature: sensor_13–24 (temperature measurement points)
#   • Pressure:    sensor_25–38 (inlet/outlet pressure)
#   • Flow Rate:   sensor_39–51 (volumetric flow)
DEFAULT_GROUPS = {
    "vibration":   list(range(0, 13)),   # sensor_00 – sensor_12
    "temperature": list(range(13, 25)),  # sensor_13 – sensor_24
    "pressure":    list(range(25, 39)),  # sensor_25 – sensor_38
    "flow_rate":   list(range(39, 52)),  # sensor_39 – sensor_51
}

GROUP_META = {
    "vibration":   {"unit": "mm/s", "display": "Vibration",   "icon": "⚡", "normal_range": [0, 4.5],   "warning": 4.5,  "critical": 7.0},
    "temperature": {"unit": "°C",   "display": "Temperature", "icon": "🌡", "normal_range": [60, 85],   "warning": 85,   "critical": 95},
    "pressure":    {"unit": "bar",  "display": "Pressure",    "icon": "📊", "normal_range": [4, 8],     "warning": 8.5,  "critical": 10},
    "flow_rate":   {"unit": "m³/h", "display": "Flow Rate",   "icon": "💧", "normal_range": [120, 200], "warning": 110,  "critical": 90},
}


def load_data(csv_path: str) -> pd.DataFrame:
    print(f"[INFO] Reading file: {csv_path}")
    df = pd.read_csv(csv_path)

    # Normalise column names
    df.columns = df.columns.str.strip().str.lower()

    # Find timestamp column
    ts_col = next((c for c in df.columns if "timestamp" in c or "time" in c), None)
    if ts_col:
        df["timestamp"] = pd.to_datetime(df[ts_col])
        df = df.sort_values("timestamp").reset_index(drop=True)
    else:
        df["timestamp"] = pd.date_range("2024-01-01", periods=len(df), freq="1min")

    # Find machine_status column
    status_col = next((c for c in df.columns if "status" in c or "machine" in c), None)
    if status_col:
        df["machine_status"] = df[status_col].str.upper().str.strip()
    else:
        # Fallback: assume NORMAL for first 80%, then BROKEN, then RECOVERING
        n = len(df)
        status = ["NORMAL"] * int(n * 0.8) + ["BROKEN"] * int(n * 0.1) + ["RECOVERING"] * (n - int(n * 0.8) - int(n * 0.1))
        df["machine_status"] = status

    # Build sensor column list
    sensor_cols = [f"sensor_{i:02d}" for i in range(52)]
    missing = [c for c in sensor_cols if c not in df.columns]
    if missing:
        actual = [c for c in df.columns if c.startswith("sensor")]
        print(f"[WARN] {len(missing)} sensor columns not found. Actual columns: {actual[:5]}...")

    available = [c for c in sensor_cols if c in df.columns]
    print(f"[INFO] Found {len(available)}/52 sensor columns")
    print(f"[INFO] Total rows: {len(df):,}")
    print(f"[INFO] machine_status distribution:\n{df['machine_status'].value_counts()}")
    return df


def compute_group_signal(df: pd.DataFrame, group_indices: list[int]) -> pd.Series:
    """Return the average signal for a sensor group (mean of available columns)."""
    cols = [f"sensor_{i:02d}" for i in group_indices if f"sensor_{i:02d}" in df.columns]
    if not cols:
        return pd.Series([0.0] * len(df))
    return df[cols].mean(axis=1)


def analyze_group_divergence(df: pd.DataFrame) -> dict:
    """
    Compute how much each sensor group changes before BROKEN.
    Used to identify which groups have the clearest fault signal.
    """
    broken_mask = df["machine_status"] == "BROKEN"
    normal_mask = df["machine_status"] == "NORMAL"

    results = {}
    for group_name, indices in DEFAULT_GROUPS.items():
        signal = compute_group_signal(df, indices)
        normal_mean = signal[normal_mask].mean() if normal_mask.any() else 0
        broken_mean = signal[broken_mask].mean() if broken_mask.any() else 0
        normal_std  = signal[normal_mask].std()  if normal_mask.any() else 1
        divergence  = abs(broken_mean - normal_mean) / (normal_std + 1e-9)
        results[group_name] = {
            "normal_mean": round(float(normal_mean), 4),
            "broken_mean": round(float(broken_mean), 4),
            "divergence_score": round(float(divergence), 2),
        }
        print(f"  [{group_name:12s}] normal={normal_mean:.3f} | broken={broken_mean:.3f} | divergence={divergence:.2f}σ")

    return results


def find_first_anomaly_index(df: pd.DataFrame) -> int:
    """Return the row index of the first BROKEN entry in the dataset.

    mqtt_replay.py --start-at-anomaly will rewind 200 rows from here,
    providing ~33s of NORMAL context before BROKEN appears (360x compression).
    """
    broken_rows = df[df["machine_status"] == "BROKEN"].index.tolist()
    if not broken_rows:
        return len(df) // 2
    return broken_rows[0]


def build_sensor_groups_config(df: pd.DataFrame, divergence: dict) -> dict:
    """
    Build the config file used by downstream scripts.
    Includes:
    - Sensor → group mapping
    - Scale factor to normalise raw values to dashboard-friendly units
    - Alert thresholds
    """
    first_anomaly_idx = find_first_anomaly_index(df)

    config = {
        "groups": {},
        "demo": {
            "first_anomaly_row": first_anomaly_idx,
            "total_rows": len(df),
            "time_compression_ratio": 360,  # 1h real = 10s demo
            "status_column": "machine_status",
        }
    }

    for group_name, indices in DEFAULT_GROUPS.items():
        available_cols = [f"sensor_{i:02d}" for i in indices if f"sensor_{i:02d}" in df.columns]
        signal = compute_group_signal(df, indices)

        # Compute scale factor to map raw signal into the real-world unit range
        sig_min, sig_max = signal.min(), signal.max()
        meta = GROUP_META[group_name]
        target_min, target_max = meta["normal_range"]

        if sig_max - sig_min > 0:
            scale = (target_max - target_min) / (sig_max - sig_min)
            offset = target_min - sig_min * scale
        else:
            scale, offset = 1.0, 0.0

        config["groups"][group_name] = {
            "sensor_columns": available_cols,
            "aggregation": "mean",
            "scale": round(float(scale), 6),
            "offset": round(float(offset), 6),
            "unit": meta["unit"],
            "display_name": meta["display"],
            "icon": meta["icon"],
            "thresholds": {
                "warning": meta["warning"],
                "critical": meta["critical"],
                "normal_min": meta["normal_range"][0],
                "normal_max": meta["normal_range"][1],
            },
            "divergence_score": divergence[group_name]["divergence_score"],
            "normal_mean_raw": divergence[group_name]["normal_mean"],
            "broken_mean_raw": divergence[group_name]["broken_mean"],
        }

    return config


def main():
    parser = argparse.ArgumentParser(description="Analyse Pump Sensor Dataset")
    parser.add_argument("--csv", default="data/sensor.csv",          help="Path to CSV file")
    parser.add_argument("--out", default="data/sensor_groups.json",  help="Output config JSON path")
    args = parser.parse_args()

    if not os.path.exists(args.csv):
        print(f"[ERROR] File not found: {args.csv}")
        print("  → Place the CSV file in the data/ directory and name it sensor.csv")
        sys.exit(1)

    print("\n" + "="*60)
    print(" PUMP SENSOR DATA ANALYSIS")
    print("="*60)

    df = load_data(args.csv)

    print("\n[STEP 2] Computing divergence score for each sensor group:")
    divergence = analyze_group_divergence(df)

    print("\n[STEP 3] Building sensor group config...")
    config = build_sensor_groups_config(df, divergence)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(config, f, indent=2)

    print(f"\n✅ Config saved: {args.out}")
    print(f"   • {len(config['groups'])} sensor groups")
    print(f"   • Demo starts at row: {config['demo']['first_anomaly_row']:,}")
    print(f"   • Total rows: {config['demo']['total_rows']:,}")

    print("\n[SENSOR GROUPS SUMMARY]")
    print(f"{'Group':<15} {'Sensors':>7} {'Divergence':>12} {'Unit':>8}")
    print("-" * 45)
    for g, v in config["groups"].items():
        print(f"{g:<15} {len(v['sensor_columns']):>7} {v['divergence_score']:>10.2f}σ {v['unit']:>8}")

    print("\n[NEXT STEP] Run MQTT replay:")
    print(f"  python scripts/mqtt_replay.py --csv {args.csv} --config {args.out}")


if __name__ == "__main__":
    main()
