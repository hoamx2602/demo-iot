"""
Rebuild PumpGuard_AI_Workshop.ipynb with:
- Removed assert-only cells
- Simplified .env creation (write once, no re-read)
- Code explanations after each file upload section
- No decorative output / no redundant checks
"""

import json, textwrap

def md(src): return {"cell_type": "markdown", "metadata": {}, "source": textwrap.dedent(src).strip().splitlines(True)}
def code(src): return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": textwrap.dedent(src).strip().splitlines(True)}


cells = []

# ── Title ──────────────────────────────────────────────────────────────────
cells.append(md("""
# PumpGuard AI — IoT Predictive Maintenance Workshop

Goal: Build a real-time industrial pump monitoring system that detects anomalies and recommends maintenance actions using AI.

**Stack:** MQTT · Node-RED · FastAPI · Groq LLM · WebSocket Dashboard
"""))

# ── Part 1: Setup ──────────────────────────────────────────────────────────
cells.append(md("---\n## Part 1 — Environment Check & Directory Setup"))

cells.append(code("""
import sys, os, platform
print(f"Python  : {sys.version.split()[0]}")
print(f"Platform: {platform.platform()}")
"""))

cells.append(code("""
import os
PROJ = '/content/pump-iot-demo'
for d in ['backend', 'dashboard', 'nodered', 'scripts', 'data']:
    os.makedirs(os.path.join(PROJ, d), exist_ok=True)
print("Directories created:", PROJ)
"""))

# ── Part 2: Install ─────────────────────────────────────────────────────────
cells.append(md("""---
## Part 2 — Install Dependencies

### 2.1 Upload `requirements.txt` → `backend/`

Lists all Python packages required by the backend.
After uploading, run the cell below to install them.
"""))

cells.append(code("""
import subprocess, sys
result = subprocess.run(
    [sys.executable, '-m', 'pip', 'install', '-q', '-r',
     '/content/pump-iot-demo/backend/requirements.txt'],
    capture_output=True, text=True
)
if result.returncode == 0:
    print("Packages installed.")
else:
    print(result.stderr[-500:])
"""))

cells.append(md("""
### 2.2 Install Mosquitto & Node-RED

- **Mosquitto**: MQTT broker — receives and routes messages between components
- **Node-RED**: flow-based programming tool for edge data processing
"""))

cells.append(code("""
import subprocess
subprocess.run(['apt-get', 'install', '-y', '-q', 'mosquitto', 'mosquitto-clients'],
               capture_output=True)
subprocess.run(['npm', 'install', '-g', '--quiet', 'node-red'], capture_output=True)
print("Mosquitto and Node-RED installed.")
"""))

# ── Part 3: Backend ─────────────────────────────────────────────────────────
cells.append(md("""---
## Part 3 — Backend: `server.py`

### Upload these 2 files into `backend/`:
1. `server.py` — main FastAPI backend
2. `email_alert.html` — HTML template for alert emails

---

### What does `server.py` do?

**`MQTTBridge`** — subscribes to `pump/sensors`, receives payloads from the simulator, caches the latest reading, and forwards it over WebSocket.

**`ConnectionManager`** — manages all connected WebSocket clients (dashboards). Uses a per-connection write lock to prevent frame corruption when multiple clients receive data simultaneously.

**`_broadcast_loop`** — runs at 2 Hz (every 0.5s), pushing the latest sensor data to all clients. Decouples the ingest rate (MQTT) from the send rate (WebSocket) to prevent backlogs.

**`POST /alert`** — called by Node-RED when an anomaly is detected. The backend calls Groq AI for analysis and sends an alert email.

**`POST /analyze`** — accepts a sensor snapshot and returns a structured JSON analysis from Groq: `risk_level`, `recommended_actions`, `estimated_hours_to_failure`.

**`_ai_semaphore`** — limits concurrent Groq calls to 2 to stay within the free-tier rate limit.
"""))

cells.append(md("""
### 3.2 Create `.env` file

Fill in your API keys below and run the cell — the `.env` file will be created automatically.
"""))

cells.append(code("""
GROQ_API_KEY   = ''   # https://console.groq.com → API Keys
RESEND_API_KEY = ''   # https://resend.com → API Keys (leave empty to skip emails)
ALERT_FROM     = ''   # sender email address (must be verified on Resend)
ALERT_TO       = ''   # recipient email address for alerts

env_content = f\"\"\"MQTT_HOST=localhost
MQTT_PORT=1883
GROQ_API_KEY={GROQ_API_KEY}
RESEND_API_KEY={RESEND_API_KEY}
ALERT_FROM={ALERT_FROM}
ALERT_TO={ALERT_TO}
\"\"\"

with open('/content/pump-iot-demo/backend/.env', 'w') as f:
    f.write(env_content)
print(".env created.")
"""))

# ── Part 4: MQTT ────────────────────────────────────────────────────────────
cells.append(md("""---
## Part 4 — MQTT Broker (Mosquitto)

MQTT follows a pub/sub model:
- `mqtt_replay.py` **publishes** data to topic `pump/sensors`
- Both `server.py` and Node-RED **subscribe** to that topic → receive data in parallel

Mosquitto is the broker in between — it receives from publishers and delivers to all subscribers.

### 4.1 Start Mosquitto
"""))

cells.append(code("""
import subprocess, time

subprocess.run(['pkill', '-f', 'mosquitto'], capture_output=True)
time.sleep(0.5)

with open('/tmp/mosquitto.conf', 'w') as f:
    f.write('listener 1883\\nallow_anonymous true\\n')

mosq_proc = subprocess.Popen(
    ['mosquitto', '-c', '/tmp/mosquitto.conf'],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
time.sleep(1)
print("Mosquitto running." if mosq_proc.poll() is None else "Failed to start Mosquitto.")
"""))

cells.append(md("### 4.2 Verify MQTT connection"))

cells.append(code("""
import paho.mqtt.client as mqtt, threading, time

ok = threading.Event()
def _on_connect(c, ud, f, rc, p=None):
    ok.set() if rc == 0 else None

c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='test')
c.on_connect = _on_connect
c.connect('localhost', 1883, keepalive=5)
c.loop_start()
connected = ok.wait(timeout=3)
c.loop_stop(); c.disconnect()
print("MQTT: connected" if connected else "MQTT: connection failed")
"""))

# ── Part 5: Node-RED ─────────────────────────────────────────────────────────
cells.append(md("""---
## Part 5 — Node-RED Pipeline

### Upload `flows.json` → `nodered/`

---

### What does `flows.json` do?

Node-RED processes sensor data step by step through connected nodes:

1. **MQTT In** — subscribes to `pump/sensors`, receives JSON payloads from the simulator
2. **Parse & Validate** — parses the JSON and verifies all required fields are present
3. **Rolling Buffer** — accumulates the last 60 readings (~30 seconds of history)
4. **Compute Trends** — calculates slope, std_dev, and anomaly_score per sensor group
5. **Throttle (1/60s)** — limits AI calls to at most once every 60 seconds
6. **POST /alert** — sends a sensor snapshot to FastAPI when an anomaly is detected

→ Node-RED acts as the "edge intelligence" layer: it processes and filters data before invoking AI, rather than calling AI on every raw reading.

### 5.2 Start Node-RED
"""))

cells.append(code("""
import subprocess, time, os, requests, json as _json

NR_HOME = '/root/.node-red'
os.makedirs(NR_HOME, exist_ok=True)

flow_src = '/content/pump-iot-demo/nodered/flows.json'
with open(flow_src, 'rb') as r, open(NR_HOME + '/flows.json', 'wb') as w:
    w.write(r.read())

with open(NR_HOME + '/settings.js', 'w') as f:
    f.write('module.exports = {\\n  uiPort: 1880,\\n  httpAdminRoot: "/",\\n  userDir: "/root/.node-red",\\n  flowFile: "flows.json",\\n  logging: { console: { level: "warn" } }\\n};\\n')

nr_proc = subprocess.Popen(
    ['node-red', '--userDir', NR_HOME],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
print("Waiting for Node-RED...")
for _ in range(20):
    time.sleep(2)
    try:
        r = requests.get('http://localhost:1880', timeout=2)
        if r.status_code == 200:
            print("Node-RED running.")
            break
    except: pass
else:
    print("Node-RED may still be starting — wait 10s and retry.")
"""))

cells.append(code("""
import requests, json as _json

flow_src = '/content/pump-iot-demo/nodered/flows.json'
with open(flow_src) as f:
    flows = _json.load(f)

r = requests.post('http://localhost:1880/flows',
                  json=flows,
                  headers={'Content-Type': 'application/json', 'Node-RED-Deployment-Type': 'full'})
print("Flow deployed." if r.status_code in (200, 204) else f"Deploy failed: {r.status_code} {r.text[:200]}")
"""))

# ── Part 6: FastAPI + Dashboard ───────────────────────────────────────────────
cells.append(md("""---
## Part 6 — Dashboard & FastAPI Server

### Upload these 2 files into `dashboard/`:
1. `index.html` — main dashboard (sensor charts, AI panel, failure timeline)
2. `control.html` — demo control panel (switch between Normal / Warning / Critical)

---

### What does `index.html` do?

Opens a WebSocket connection to `ws://…/ws` and receives continuous `sensor_update` messages from the backend.

Displays:
- **Health Ring** — overall machine health (0–100%)
- **Sensor Gauges** — live readings for 4 sensor groups with threshold zones
- **Failure Timeline** — 4-milestone journey: Start → Anomaly Detected → Now → Estimated Failure
- **AI Panel** — risk level, recommended actions, and estimated savings from Groq

### 6.1 Start FastAPI
"""))

cells.append(code("""
import subprocess, time

fastapi_proc = subprocess.Popen(
    ['python', '-m', 'uvicorn', 'server:app', '--host', '0.0.0.0', '--port', '8000'],
    cwd='/content/pump-iot-demo/backend',
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
)
for _ in range(20):
    time.sleep(1)
    line = fastapi_proc.stdout.readline().decode('utf-8', errors='replace')
    if 'Application startup complete' in line or 'Uvicorn running' in line:
        print(f"FastAPI running (PID {fastapi_proc.pid})")
        break
else:
    print("FastAPI may still be starting — check logs if dashboard is blank.")
"""))

cells.append(md("### 6.2 Create public URL (ngrok)"))

cells.append(code("""
import os, subprocess, time, requests

NGROK_TOKEN = ''   # https://dashboard.ngrok.com/get-started/your-authtoken

PUBLIC_URL  = None
NODERED_URL = None

subprocess.run(['pkill', '-f', 'ngrok'], capture_output=True)
subprocess.run(['ngrok', 'authtoken', NGROK_TOKEN], capture_output=True)

ngrok_proc = subprocess.Popen(
    ['ngrok', 'start', '--all', '--config', '/dev/stdin'],
    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
ngrok_config = f\"\"\"version: "2"
authtoken: {NGROK_TOKEN}
tunnels:
  api:
    addr: 8000
    proto: http
  nodered:
    addr: 1880
    proto: http
\"\"\"
ngrok_proc.stdin.write(ngrok_config.encode())
ngrok_proc.stdin.close()
time.sleep(4)

try:
    tunnels = requests.get('http://localhost:4040/api/tunnels', timeout=5).json()['tunnels']
    for t in tunnels:
        url = t['public_url'].replace('http://', 'https://')
        if '8000' in t['config']['addr']:
            PUBLIC_URL = url
        else:
            NODERED_URL = url
    print(f"Dashboard : {PUBLIC_URL}/dashboard/")
    print(f"Node-RED  : {NODERED_URL}")
    # Update .env with public URL
    env_path = '/content/pump-iot-demo/backend/.env'
    with open(env_path) as f: env = f.read()
    if 'PUBLIC_URL' not in env:
        with open(env_path, 'a') as f: f.write(f'\\nPUBLIC_URL={PUBLIC_URL}\\n')
except Exception as e:
    print(f"ngrok error: {e}")
"""))

# ── Part 7: Simulator ──────────────────────────────────────────────────────
cells.append(md("""---
## Part 7 — Sensor Simulator

### Upload these 2 files:
1. `analyze_sensors.py` → `scripts/`
2. `mqtt_replay.py` → `scripts/`

`sensor.csv` will be downloaded automatically in the cell below.

---

### What does `analyze_sensors.py` do?

Reads `sensor.csv` (220k rows, 52 sensor columns) and:
1. Computes **divergence score** — measures how much each sensor group shifts from NORMAL to BROKEN
2. Computes **scale + offset** — maps raw values (0–1) to real-world units (mm/s, °C, bar, m³/h)
3. Outputs `sensor_groups.json` — the config file used by `mqtt_replay.py`

Run **once** during setup.

### What does `mqtt_replay.py` do?

Reads the CSV row by row, applies scale/offset from config, and publishes a JSON payload to MQTT topic `pump/sensors`.
- Default: 1 row/second (`--compression 60`)
- Can jump directly to the fault row: `--start-at-anomaly`
- Accepts commands via topic `pump/control`: PAUSE / RESUME / STOP / JUMP:<row>

### 7.1 Download dataset & analyse
"""))

cells.append(code("""
import urllib.request, subprocess, sys, os

CSV_URL = 'https://smath-link-dev.s3.dualstack.ap-southeast-1.amazonaws.com/iot/sensor.csv'
csv_path = '/content/pump-iot-demo/data/sensor.csv'

if not os.path.exists(csv_path):
    print("Downloading sensor.csv ...")
    urllib.request.urlretrieve(CSV_URL, csv_path)
    size_mb = os.path.getsize(csv_path) / 1024 / 1024
    print(f"Downloaded: {size_mb:.1f} MB")
else:
    print("sensor.csv already exists — skipping download.")

result = subprocess.run(
    [sys.executable, 'scripts/analyze_sensors.py',
     '--csv', 'data/sensor.csv',
     '--out', 'data/sensor_groups.json'],
    cwd='/content/pump-iot-demo',
    capture_output=True, text=True,
)
print(result.stdout[-800:] if result.returncode == 0 else result.stderr[-500:])
"""))

cells.append(md("### 7.2 Start simulator (NORMAL mode)"))

cells.append(code("""
import subprocess, sys, time

sim_proc = subprocess.Popen(
    [sys.executable, 'scripts/mqtt_replay.py',
     '--csv', 'data/sensor.csv',
     '--config', 'data/sensor_groups.json',
     '--row-start', '0', '--quiet'],
    cwd='/content/pump-iot-demo',
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
time.sleep(2)
print("Simulator running — NORMAL state.")
if 'PUBLIC_URL' in dir() and PUBLIC_URL:
    print(f"Dashboard : {PUBLIC_URL}/dashboard/")
"""))

# ── Part 8: Pipeline Check ─────────────────────────────────────────────────
cells.append(md("---\n## Part 8 — Full Pipeline Check"))

cells.append(code("""
import requests

for name, url in [
    ('FastAPI /health', 'http://localhost:8000/health'),
    ('Latest sensor',   'http://localhost:8000/latest'),
]:
    try:
        d = requests.get(url, timeout=3).json()
        if name == 'FastAPI /health':
            print(f"FastAPI  : ok | MQTT={'connected' if d.get('mqtt_connected') else 'NO'} | ws_clients={d.get('ws_clients',0)}")
        else:
            print(f"Simulator: status={d.get('machine_status','?')} health={round(d.get('health_score',0),1)}")
    except Exception as e:
        print(f"{name}: {e}")

if 'PUBLIC_URL' in dir() and PUBLIC_URL:
    print(f"\\nDashboard: {PUBLIC_URL}/dashboard/")
"""))

# ── Part 9: AI & Email ─────────────────────────────────────────────────────
cells.append(md("""---
## Part 9 — AI Analysis & Email Alerts

Node-RED automatically calls `/alert` when it detects an anomaly.
The cells below allow you to test both endpoints manually.
"""))

cells.append(code("""
import requests, datetime

_base = PUBLIC_URL if 'PUBLIC_URL' in dir() and PUBLIC_URL else 'http://localhost:8000'

snapshot = {
    'timestamp': datetime.datetime.utcnow().isoformat(),
    'machine_status': 'BROKEN', 'health_score': 22.5, 'overall_status': 'CRITICAL',
    'sensors': {
        'vibration':   {'current': 7.82, 'avg_60_readings': 5.4,  'trend': 'DEGRADING', 'status': 'CRITICAL', 'rate_of_change': 0.12},
        'temperature': {'current': 96.5, 'avg_60_readings': 82.1, 'trend': 'DEGRADING', 'status': 'CRITICAL', 'rate_of_change': 0.8},
        'pressure':    {'current': 9.8,  'avg_60_readings': 8.1,  'trend': 'STABLE',    'status': 'WARNING',  'rate_of_change': 0.05},
        'flow_rate':   {'current': 85.0, 'avg_60_readings': 140.0,'trend': 'DEGRADING', 'status': 'CRITICAL', 'rate_of_change': -2.1},
    }
}
r = requests.post(_base + '/analyze', json={'snapshot': snapshot}, timeout=30)
if r.status_code == 200:
    ai = r.json()
    print(f"Risk      : {ai.get('risk_level')}")
    print(f"Confidence: {ai.get('confidence')}")
    print(f"Summary   : {ai.get('summary','')[:120]}")
    print(f"Est. hours: {ai.get('estimated_hours_to_failure')}")
else:
    print(f"Error {r.status_code}: {r.text[:200]}")
"""))

cells.append(code("""
import requests

_base = PUBLIC_URL if 'PUBLIC_URL' in dir() and PUBLIC_URL else 'http://localhost:8000'

r = requests.post(_base + '/alert', json={
    'level': 'CRITICAL', 'health_score': 22.5,
    'message': 'Test alert from workshop notebook.',
    'sensor_summary': {'Vibration': '7.82 mm/s', 'Temperature': '96.5 °C', 'Pressure': '9.8 bar', 'Flow Rate': '85.0 m³/h'},
    'sensor_statuses': {'Vibration': 'CRITICAL', 'Temperature': 'CRITICAL', 'Pressure': 'WARNING', 'Flow Rate': 'CRITICAL'},
    'ai_risk_level': 'CRITICAL', 'estimated_hours_to_failure': 6, 'estimated_savings': 513500,
}, timeout=10)
result = r.json()
status = result.get('status', '?')
if status == 'sent':
    print(f"Email sent → {result.get('to')}")
elif status == 'skipped':
    print(f"Skipped: {result.get('reason')} (configure RESEND_API_KEY in .env to enable)")
else:
    print(result)
"""))

# ── Part 10: Demo ──────────────────────────────────────────────────────────
cells.append(md("""---
## Part 10 — Demo: Simulate a Fault

### Scenario A — Switch to CRITICAL (machine failure)

Restarts the simulator from the fault row. Node-RED will detect the anomaly within ~60s and trigger an AI analysis.
"""))

cells.append(code("""
import subprocess, sys, time

if 'sim_proc' in dir() and sim_proc.poll() is None:
    sim_proc.terminate(); time.sleep(0.5)

sim_proc = subprocess.Popen(
    [sys.executable, 'scripts/mqtt_replay.py',
     '--csv', 'data/sensor.csv',
     '--config', 'data/sensor_groups.json',
     '--start-at-anomaly', '--anomaly-offset', '0', '--quiet'],
    cwd='/content/pump-iot-demo',
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
print("Simulator → CRITICAL mode. Dashboard updates in ~3s.")
"""))

cells.append(md("### Scenario B — Reset to NORMAL"))

cells.append(code("""
import subprocess, sys

if 'sim_proc' in dir() and sim_proc.poll() is None:
    sim_proc.terminate()

sim_proc = subprocess.Popen(
    [sys.executable, 'scripts/mqtt_replay.py',
     '--csv', 'data/sensor.csv',
     '--config', 'data/sensor_groups.json',
     '--row-start', '0', '--quiet'],
    cwd='/content/pump-iot-demo',
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
print("Simulator → NORMAL mode.")
"""))

# ── Part 11: Monitor ───────────────────────────────────────────────────────
cells.append(md("""---
## Part 11 — System Status

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Dashboard blank | FastAPI not running | Re-run Part 6.1 |
| No data on chart | Simulator not running | Re-run Part 7.2 |
| AI not responding | Invalid GROQ_API_KEY | Check .env |
| Node-RED not deploying | flows.json not uploaded | Re-run Part 5 |
"""))

cells.append(code("""
import requests

def check(name, proc, url=None):
    alive = proc is not None and proc.poll() is None
    status = 'running' if alive else 'STOPPED'
    http = ''
    if alive and url:
        try: http = f" | HTTP {requests.get(url, timeout=2).status_code}"
        except: http = ' | unreachable'
    print(f"{'ok' if alive else '!!'} {name}: {status}{http}")

check('FastAPI :8000', locals().get('fastapi_proc'), 'http://localhost:8000/health')
check('Simulator',     locals().get('sim_proc'))
check('Mosquitto',     locals().get('mosq_proc'))
check('Node-RED :1880',locals().get('nr_proc'), 'http://localhost:1880')
if 'PUBLIC_URL' in dir() and PUBLIC_URL:
    print(f"\\nDashboard: {PUBLIC_URL}/dashboard/")
"""))

# ── Part 12: Wrap-up ───────────────────────────────────────────────────────
cells.append(md("""---
## Part 12 — Summary

| Component | Role |
|-----------|------|
| `mqtt_replay.py` | Simulates physical sensors, publishes MQTT data |
| Mosquitto | Message broker — pub/sub hub |
| Node-RED | Edge processing — computes trends, decides when to trigger AI |
| `server.py` | Backend hub — WebSocket, REST API, AI orchestration |
| Groq LLM | Root-cause analysis and maintenance recommendations |
| Dashboard | HMI — real-time display for the operator |

**Extension ideas:**
- Add a database (InfluxDB) → long-term trend analysis
- Replace Groq with local Ollama → fully offline (air-gapped) operation
- Scale to multiple machines: hierarchical topics `factory/{site}/pump/{id}/sensors`
"""))

# ── Write notebook ──────────────────────────────────────────────────────────
nb = {
    "nbformat": 4,
    "nbformat_minor": 5,
    "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.10.0"},
        "colab": {"name": "PumpGuard_AI_Workshop.ipynb", "provenance": []}
    },
    "cells": cells,
}

out = '/Users/hoamai/Documents/Claude/Projects/IOT/pump-iot-demo/notebooks/PumpGuard_AI_Workshop.ipynb'
print(f"Would write {len(cells)} cells to {out}")
print("Run with --write flag to save.")

import sys
if '--write' in sys.argv:
    import os
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w') as f:
        json.dump(nb, f, indent=1, ensure_ascii=False)
    print(f"Written: {out}")
