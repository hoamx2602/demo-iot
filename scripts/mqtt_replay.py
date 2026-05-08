"""
Step 2: MQTT Replay Script
- Đọc CSV row by row
- Apply scale/offset từ sensor_groups.json để ra giá trị thực tế
- Publish lên MQTT topic: pump/sensors
- Time compression: 1h thật → 10s demo (3600x nhanh hơn)

Usage:
    python scripts/mqtt_replay.py --csv data/sensor.csv --config data/sensor_groups.json
    python scripts/mqtt_replay.py --csv data/sensor.csv --config data/sensor_groups.json --start-at-anomaly
    python scripts/mqtt_replay.py --csv data/sensor.csv --config data/sensor_groups.json --row-start 1000

Requires:
    pip install paho-mqtt pandas numpy
"""

import argparse
import json
import os
import ssl
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("[ERROR] paho-mqtt chưa được cài. Chạy: pip install paho-mqtt")
    sys.exit(1)

# ─── Config ─────────────────────────────────────────────────────────────────
MQTT_HOST     = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT     = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")
MQTT_TLS      = os.getenv("MQTT_TLS", "false").lower() in ("true", "1", "yes")
TOPIC_SENSORS = "pump/sensors"
TOPIC_STATUS  = "pump/status"
TOPIC_CONTROL = "pump/control"   # subscribe để nhận lệnh pause/resume/jump

# 1 phút data thật = X giây trong demo
# Vì data là minutely: 60 rows = 1h, ta muốn 1h = 10s → mỗi row cách nhau 10/60 ≈ 0.167s
TIME_COMPRESSION = 360  # 1 phút data → 1/6 giây demo


class PumpReplay:
    def __init__(self, csv_path: str, config_path: str, row_start: int = 0, verbose: bool = True):
        self.verbose = verbose
        self.paused  = False
        self.running = True
        self.row_ptr = row_start

        print(f"[INIT] Đọc config: {config_path}")
        with open(config_path) as f:
            self.config = json.load(f)

        print(f"[INIT] Đọc CSV: {csv_path}")
        self.df = pd.read_csv(csv_path)
        self.df.columns = self.df.columns.str.strip().str.lower()

        # Timestamp
        ts_col = next((c for c in self.df.columns if "timestamp" in c), None)
        if ts_col:
            self.df["timestamp"] = pd.to_datetime(self.df[ts_col])
        else:
            self.df["timestamp"] = pd.date_range("2024-01-01", periods=len(self.df), freq="1min")

        # Status
        self.status_col = next((c for c in self.df.columns if "status" in c or "machine" in c), None)
        if self.status_col:
            self.df["machine_status"] = self.df[self.status_col].str.upper().str.strip()
        else:
            self.df["machine_status"] = "NORMAL"

        self.total_rows = len(self.df)
        self.groups = self.config["groups"]
        print(f"[INIT] Dataset: {self.total_rows:,} rows, {len(self.groups)} sensor groups")

    def compute_group_value(self, row: pd.Series, group_name: str) -> float:
        """Tính giá trị đại diện cho 1 nhóm tại 1 row."""
        g = self.groups[group_name]
        cols = [c for c in g["sensor_columns"] if c in self.df.columns]
        if not cols:
            return 0.0
        raw_vals = [row[c] for c in cols if pd.notna(row.get(c, np.nan))]
        if not raw_vals:
            return g["thresholds"]["normal_min"]
        raw_avg = np.mean(raw_vals)
        # Apply scale + offset để ra unit thực tế
        scaled = raw_avg * g["scale"] + g["offset"]
        # Thêm noise nhỏ để trông real hơn (±1%)
        noise = np.random.normal(0, abs(scaled) * 0.008)
        return round(float(scaled + noise), 3)

    def build_payload(self, row_idx: int) -> dict:
        """Tạo JSON payload từ 1 row CSV."""
        row = self.df.iloc[row_idx]
        status = str(row.get("machine_status", "NORMAL"))

        sensor_values = {}
        for group_name in self.groups:
            val = self.compute_group_value(row, group_name)
            sensor_values[group_name] = val

        # Tính health score dựa trên khoảng cách đến threshold
        health = self._compute_health_score(sensor_values, status)

        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "row": row_idx,
            "total": self.total_rows,
            "machine_status": status,
            "health_score": health,
            "sensors": sensor_values,
            "progress_pct": round(row_idx / self.total_rows * 100, 1),
        }

    def _compute_health_score(self, values: dict, status: str) -> float:
        """Health score 0-100, dựa trên threshold của mỗi nhóm."""
        if status == "BROKEN":
            return round(np.random.uniform(5, 25), 1)
        if status == "RECOVERING":
            return round(np.random.uniform(40, 65), 1)

        scores = []
        for g_name, val in values.items():
            g = self.groups[g_name]
            t = g["thresholds"]
            warn, crit = t["warning"], t["critical"]
            n_min, n_max = t["normal_min"], t["normal_max"]

            # Flow rate: thấp hơn ngưỡng là xấu
            if g_name == "flow_rate":
                if val >= n_min:
                    scores.append(100)
                elif val >= crit:
                    scores.append(50)
                else:
                    scores.append(10)
            else:
                if val <= n_max:
                    scores.append(100)
                elif val <= warn:
                    scores.append(70)
                elif val <= crit:
                    scores.append(40)
                else:
                    scores.append(10)

        return round(float(np.mean(scores)), 1) if scores else 80.0

    def setup_mqtt(self) -> mqtt.Client:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="pump-replay",
            protocol=mqtt.MQTTv311,
        )

        def on_connect(c, userdata, connect_flags, reason_code, properties):
            if not reason_code.is_failure:
                print(f"[MQTT] ✅ Kết nối thành công → {MQTT_HOST}:{MQTT_PORT}")
                c.subscribe(TOPIC_CONTROL)
            else:
                print(f"[MQTT] ❌ Kết nối thất bại: {reason_code}")

        def on_message(c, userdata, msg):
            cmd = msg.payload.decode().strip().upper()
            if cmd == "PAUSE":
                self.paused = True
                print("[CTRL] ⏸ PAUSED")
            elif cmd == "RESUME":
                self.paused = False
                print("[CTRL] ▶ RESUMED")
            elif cmd == "STOP":
                self.running = False
                print("[CTRL] ⏹ STOPPED")
            elif cmd.startswith("JUMP:"):
                try:
                    target = int(cmd.split(":")[1])
                    self.row_ptr = max(0, min(target, self.total_rows - 1))
                    print(f"[CTRL] ⏭ JUMPED to row {self.row_ptr}")
                except ValueError:
                    pass

        client.on_connect = on_connect
        client.on_message = on_message

        if MQTT_USERNAME:
            client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        use_tls = MQTT_TLS or MQTT_PORT == 8883
        if use_tls:
            client.tls_set(tls_version=ssl.PROTOCOL_TLS_CLIENT)

        try:
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        except Exception as e:
            print(f"[ERROR] Không kết nối được MQTT broker: {e}")
            print(f"  → Kiểm tra MQTT_HOST/PORT/USERNAME/PASSWORD trong .env")
            sys.exit(1)

        return client

    def run(self, compression: int = TIME_COMPRESSION):
        client = self.setup_mqtt()
        client.loop_start()

        interval_sec = 60.0 / compression  # giây giữa 2 rows
        print(f"\n[REPLAY] Bắt đầu từ row {self.row_ptr}/{self.total_rows}")
        print(f"[REPLAY] Time compression: {compression}x (1 phút data = {interval_sec*1000:.0f}ms)")
        print(f"[REPLAY] Gửi lên: mqtt://{MQTT_HOST}:{MQTT_PORT}/{TOPIC_SENSORS}")
        print(f"[REPLAY] Điều khiển: publish lên {TOPIC_CONTROL} → PAUSE / RESUME / STOP / JUMP:<row>")
        print("-" * 60)

        try:
            while self.running and self.row_ptr < self.total_rows:
                if self.paused:
                    time.sleep(0.1)
                    continue

                payload = self.build_payload(self.row_ptr)
                payload_str = json.dumps(payload)

                client.publish(TOPIC_SENSORS, payload_str, qos=1)
                client.publish(TOPIC_STATUS, payload["machine_status"], qos=0, retain=True)

                if self.verbose:
                    s = payload["sensors"]
                    status_icon = {"NORMAL": "✅", "BROKEN": "🔴", "RECOVERING": "🟡"}.get(payload["machine_status"], "❓")
                    print(
                        f"Row {self.row_ptr:5d}/{self.total_rows} {status_icon} {payload['machine_status']:10s} "
                        f"health={payload['health_score']:5.1f}% | "
                        f"vib={s.get('vibration', 0):5.2f} tmp={s.get('temperature', 0):5.1f} "
                        f"prs={s.get('pressure', 0):4.1f} flow={s.get('flow_rate', 0):6.1f}"
                    )

                self.row_ptr += 1
                time.sleep(interval_sec)

        except KeyboardInterrupt:
            print("\n[REPLAY] Dừng bởi người dùng")
        finally:
            client.loop_stop()
            client.disconnect()
            print("[REPLAY] Đã ngắt kết nối MQTT")


def main():
    parser = argparse.ArgumentParser(description="Pump Sensor MQTT Replay")
    parser.add_argument("--csv",    default="data/sensor.csv",          help="Path CSV")
    parser.add_argument("--config", default="data/sensor_groups.json",  help="Config JSON")
    parser.add_argument("--row-start",       type=int, default=0,       help="Bắt đầu từ row này")
    parser.add_argument("--start-at-anomaly", action="store_true",      help="Bắt đầu từ điểm anomaly đầu tiên")
    parser.add_argument("--anomaly-offset",  type=int, default=200,     help="Số rows lùi trước điểm anomaly (0=bắt đầu đúng tại BROKEN)")
    parser.add_argument("--compression",     type=int, default=TIME_COMPRESSION, help="Time compression factor")
    parser.add_argument("--quiet", action="store_true",                 help="Giảm log output")
    args = parser.parse_args()

    for path in [args.csv, args.config]:
        if not os.path.exists(path):
            print(f"[ERROR] Không tìm thấy: {path}")
            sys.exit(1)

    replay = PumpReplay(
        csv_path=args.csv,
        config_path=args.config,
        row_start=args.row_start,
        verbose=not args.quiet,
    )

    if args.start_at_anomaly:
        anomaly_row = replay.config["demo"]["first_anomaly_row"]
        offset = args.anomaly_offset
        replay.row_ptr = max(0, anomaly_row - offset)
        label = f"{offset} rows trước điểm anomaly" if offset > 0 else "đúng tại điểm BROKEN"
        print(f"[DEMO] Bắt đầu từ row {replay.row_ptr} ({label})")

    replay.run(compression=args.compression)


if __name__ == "__main__":
    main()
