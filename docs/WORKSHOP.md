# 🏭 PumpGuard AI — Workshop Guide

> **Duration:** 3–4 hours | **Level:** Beginner–Intermediate
> **Objective:** Build a complete IoT industrial pump monitoring system with AI from scratch on Google Colab.

---

## Architecture Overview

```
[sensor.csv]
     │
     ▼
[mqtt_replay.py]  ──publish──▶  [Mosquitto Broker :1883]
                                         │
                                    subscribe
                                         │
                                         ▼
                              [Node-RED :1880]
                              ┌──────────────────┐
                              │ 1. Parse data     │
                              │ 2. Rolling buffer │
                              │ 3. Compute trends │
                              │ 4. Detect anomaly │
                              └────────┬─────────┘
                                       │ POST /analyze
                                       ▼
                              [FastAPI Backend :8000] ◀─── [Gemini AI API]
                                       │
                                  WebSocket
                                       │
                                       ▼
                              [Dashboard HTML]
                              (student's browser)
```

### Why this architecture?

| Component | Reason |
|-----------|--------|
| **MQTT** | The IoT industry standard — lightweight, pub/sub, ideal for sensor data |
| **Mosquitto** | The world's most widely used MQTT broker — free, runs anywhere |
| **Node-RED** | Visual programming — data flow is visible, minimal coding required |
| **FastAPI** | Python, fast, WebSocket built-in, auto-generates API docs |
| **WebSocket** | Real-time push from server → browser without polling |
| **Gemini AI** | Free tier available, JSON output, well-suited for technical analysis |
| **Cloudflare Tunnel** | Expose localhost to the internet — no account required |

---

## Pre-Workshop Preparation

### What students need
- [ ] A Google account (for Colab)
- [ ] **Gemini API Key** (free) → [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
- [ ] The code bundle provided by the instructor (zip file)

### Code bundle contents
```
pumpguard/
├── backend/
│   ├── server.py          ← FastAPI server (~700 lines)
│   ├── requirements.txt   ← Python dependencies list
│   └── .env.example       ← Environment variable template
├── dashboard/
│   └── index.html         ← Real-time web interface
├── data/
│   ├── sensor_groups.json ← Sensor threshold config
│   └── sensor.csv         ← Real sensor dataset (124 MB)
├── scripts/
│   └── mqtt_replay.py     ← CSV replay script
└── nodered/
    └── flows.json         ← Node-RED flow (ready to import)
```

---

## Part 0 — Introduction & Colab Setup (15 min)

### Objective
Understand the big picture and prepare the working environment.

### Step 0.1 — Open Google Colab
1. Go to [colab.research.google.com](https://colab.research.google.com)
2. Click **"New notebook"**
3. Rename it: click "Untitled0.ipynb" → type **PumpGuard_Workshop**
4. Confirm Runtime type is **Python 3** (Runtime menu → Change runtime type)

### Step 0.2 — Create the directory structure

**Why do this first?**
Colab works inside `/content/`. Every Python file, HTML file, and config file must be in the right place so the server can find them.

Create a new cell and run:

```python
import os

folders = [
    '/content/pumpguard/backend',
    '/content/pumpguard/dashboard',
    '/content/pumpguard/data',
    '/content/pumpguard/scripts',
    '/content/pumpguard/nodered',
]
for folder in folders:
    os.makedirs(folder, exist_ok=True)

os.chdir('/content/pumpguard')

print("✅ Directory structure ready:")
for f in folders:
    print(f"   {f.replace('/content/pumpguard', '.')}/")
```

**Explanation:**
- `os.makedirs(..., exist_ok=True)` — creates the folder; no error if it already exists
- `os.chdir(...)` — sets the working directory so subsequent commands don't need the full path

---

## Part 1 — MQTT: The Backbone of IoT (30 min)

### Objective
Understand the MQTT protocol and start the MQTT broker.

### Theory: What is MQTT?

**MQTT** (Message Queuing Telemetry Transport) is a messaging protocol designed specifically for IoT:

```
Publisher                Broker              Subscriber
(sensor/script)        (Mosquitto)          (backend)

publish("pump/sensors", data) ──▶ [route] ──▶ on_message(data)
```

**Why not use REST API instead of MQTT?**

| | REST API | MQTT |
|---|---|---|
| Connection model | Pull (client requests server) | Push (server sends to client) |
| Overhead | Large HTTP headers | Very small (2-byte header) |
| Multiple subscribers | Not native | Built-in (1 publish → many subscribers) |
| Suited for IoT | ❌ | ✅ |

**Key concepts:**
- **Topic**: message "channel", path-style. e.g. `pump/sensors`, `pump/alerts`
- **QoS 1**: guaranteed delivery — message arrives at least once
- **Broker**: the intermediary server — receives publishes and routes to subscribers

### Step 1.1 — Install Mosquitto

```bash
!apt-get install -y -q mosquitto
!mosquitto --version
```

**Why Mosquitto?**
Mosquitto is the most widely deployed MQTT broker in the world — lightweight (~1 MB RAM), open source, supports MQTT 3.1.1 and 5.0, used in both industrial production and on Raspberry Pis.

### Step 1.2 — Create config and start the broker

**Create the config file** — allows anonymous connections (suitable for a lab environment):

```bash
%%writefile /tmp/mosquitto.conf
listener 1883
allow_anonymous true
```

**Start the broker** in the background:

```python
import subprocess, time

subprocess.run(["pkill", "-f", "mosquitto"], capture_output=True)
time.sleep(0.5)

proc = subprocess.Popen(
    ["mosquitto", "-c", "/tmp/mosquitto.conf"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)
time.sleep(1)

print(f"✅ Mosquitto running (PID {proc.pid}) — localhost:1883" if proc.poll() is None else "❌ Failed to start")
```

**Config explained:**
- `listener 1883` — listen on port 1883 (the MQTT standard port)
- `allow_anonymous true` — allow connections without credentials (lab/dev only)

### Step 1.3 — Test MQTT (Publish & Subscribe)

This step is essential for **understanding how MQTT works** before integrating it into the system.

```python
import paho.mqtt.client as mqtt
import json, time, threading

received_messages = []

# ── Subscriber ────────────────────────────────────────────
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        client.subscribe("pump/test", qos=1)
        print("📡 Subscriber connected and listening on topic 'pump/test'")

def on_message(client, userdata, msg):
    data = json.loads(msg.payload.decode())
    received_messages.append(data)
    print(f"📨 Received: {json.dumps(data)}")

sub = mqtt.Client(client_id="test-subscriber", protocol=mqtt.MQTTv311)
sub.on_connect = on_connect
sub.on_message = on_message
sub.connect("localhost", 1883)
sub.loop_start()
time.sleep(0.5)

# ── Publisher ─────────────────────────────────────────────
pub = mqtt.Client(client_id="test-publisher", protocol=mqtt.MQTTv311)
pub.connect("localhost", 1883)

test_data = {
    "sensor": "vibration",
    "value": 3.2,
    "unit": "mm/s",
    "status": "NORMAL"
}

print(f"\n📤 Publisher sending: {json.dumps(test_data)}")
pub.publish("pump/test", json.dumps(test_data), qos=1)

time.sleep(0.5)
pub.disconnect()
sub.loop_stop()
sub.disconnect()

print(f"\n✅ MQTT working! Sent 1 message, received {len(received_messages)} message(s)")
```

**Observe:** Publisher and Subscriber don't know about each other — they only know the broker and the topic. This is **decoupled architecture** — the foundation of IoT.

---

## Part 2 — FastAPI Backend: File by File (45 min)

### Objective
Understand the backend structure and place each file in the right location.

### Theory: FastAPI + WebSocket

**FastAPI** is a modern Python web framework:
- Fast (on par with Node.js/Go)
- Auto-generates API docs at `/docs`
- WebSocket support built in
- Type hints → fewer bugs

**WebSocket vs REST API:**
```
REST:      Browser ──request──▶ Server ──response──▶ Browser  (one-way per request)
WebSocket: Browser ◀──────────────────────────────── Server  (bidirectional, persistent)
```

The dashboard needs WebSocket because sensors send data every second — the browser cannot make 1 HTTP request per second.

---

### Step 2.1 — Upload `requirements.txt`

**Purpose:** List all Python libraries that server.py depends on.

**How to upload to Colab:**
1. In the left panel, click the 📁 icon (Files)
2. Navigate to `/content/pumpguard/backend/`
3. Click **↑ Upload** → select `requirements.txt`

After uploading, verify:

```python
print(open('/content/pumpguard/backend/requirements.txt').read())
```

**Package overview:**
```
fastapi          ← main web framework
uvicorn          ← ASGI server that runs FastAPI
paho-mqtt        ← MQTT client library for Python
google-generativeai ← Gemini AI SDK
python-dotenv    ← reads .env file into os.environ
httpx            ← async HTTP client (used for AI API calls)
```

Install all:

```bash
!pip install -q -r /content/pumpguard/backend/requirements.txt
```

---

### Step 2.2 — Upload `data/sensor_groups.json`

**Purpose:** Config file defining normal/warning/critical thresholds for each sensor group.
Both Node-RED and the backend read this file.

**Upload:** Navigate to `/content/pumpguard/data/` → Upload `sensor_groups.json`

Verify:

```python
import json
cfg = json.load(open('/content/pumpguard/data/sensor_groups.json'))
print("Configured sensor groups:")
for name, info in cfg.get('groups', {}).items():
    t = info.get('thresholds', {})
    print(f"  {name}: warning={t.get('warning')}, critical={t.get('critical')}")
```

---

### Step 2.3 — Create the `.env` file (API key config)

**Purpose:** Store sensitive credentials (API keys) outside the code.
**Why not hardcode in code?** Code is committed to Git — hardcoded keys get exposed.

```python
# Get a free Gemini key at: https://aistudio.google.com/apikey
GEMINI_API_KEY = "AIzaSy-xxxx"   # ← REPLACE WITH YOUR REAL KEY

env_content = f"""# AI Provider
AI_PROVIDER=gemini
GEMINI_API_KEY={GEMINI_API_KEY}

# MQTT
MQTT_HOST=localhost
MQTT_PORT=1883
"""

with open('/content/pumpguard/backend/.env', 'w') as f:
    f.write(env_content)

# Verify without printing the key
if GEMINI_API_KEY.startswith("AIzaSy") and len(GEMINI_API_KEY) > 20:
    print(f"✅ .env created with valid key: {GEMINI_API_KEY[:14]}...")
else:
    print("⚠️  Enter your real API key — the system will run but AI will use mock data")
```

---

### Step 2.4 — Upload `backend/server.py`

**Purpose:** The brain of the system — FastAPI server with WebSocket, MQTT bridge, and AI integration.

**Upload:** Navigate to `/content/pumpguard/backend/` → Upload `server.py`

After uploading, inspect the structure:

```python
lines = open('/content/pumpguard/backend/server.py').readlines()
print(f"Total lines: {len(lines)}")

import re
for i, line in enumerate(lines, 1):
    if any(x in line for x in ['def ', 'class ', '@app.', '# ──']):
        print(f"  Line {i:4d}: {line.rstrip()}")
```

**server.py architecture:**
```
server.py
├── Config            ← reads .env (AI key, MQTT host/port)
├── ConnectionManager ← manages WebSocket clients
├── MQTTBridge        ← subscribes MQTT → broadcasts to WebSocket
├── FastAPI app
│   ├── GET /health   ← status check
│   ├── WS  /ws       ← WebSocket endpoint
│   ├── POST /analyze ← call AI for analysis
│   └── /dashboard/   ← serve static HTML
└── AI functions      ← call Gemini/Claude/OpenAI
```

---

### Step 2.5 — Upload `dashboard/index.html`

**Purpose:** The web interface that displays real-time data over WebSocket.

**Upload:** Navigate to `/content/pumpguard/dashboard/` → Upload `index.html`

```python
size = os.path.getsize('/content/pumpguard/dashboard/index.html')
print(f"✅ Dashboard: {size:,} bytes ({size//1024} KB)")
print("   Contains: HTML structure + CSS styling + JavaScript WebSocket client")
```

---

### Step 2.6 — Verify all files

```python
import os

files_to_check = {
    'backend/server.py': 'FastAPI server',
    'backend/requirements.txt': 'Python dependencies',
    'backend/.env': 'API keys config',
    'dashboard/index.html': 'Web dashboard',
    'data/sensor_groups.json': 'Sensor config',
    'scripts/mqtt_replay.py': 'Data replay script (optional)',
}

print("📋 File check:")
all_ok = True
for filepath, desc in files_to_check.items():
    full = f'/content/pumpguard/{filepath}'
    if os.path.exists(full):
        size = os.path.getsize(full)
        print(f"  ✅ {filepath:<35} ({size:>8,} bytes)  ← {desc}")
    else:
        required = 'optional' not in desc.lower()
        print(f"  {'❌' if required else '⚠️ '} {filepath:<35}  ← {desc}")
        if required:
            all_ok = False

print()
print("✅ Ready to start the backend!" if all_ok else "❌ Upload the missing files before continuing.")
```

---

### Step 2.7 — Start the Backend

```python
import subprocess, sys, os, time, requests

os.chdir('/content/pumpguard')

# Load .env into environment variables
env_vars = {}
for line in open('backend/.env').read().splitlines():
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env_vars[k.strip()] = v.strip()

# Stop any existing instance
subprocess.run(['pkill', '-f', 'uvicorn'], capture_output=True)
time.sleep(1)

log = open('/tmp/backend.log', 'w')
proc = subprocess.Popen(
    [sys.executable, '-m', 'uvicorn', 'backend.server:app',
     '--host', '0.0.0.0', '--port', '8000'],
    stdout=log, stderr=log,
    env={**os.environ, **env_vars}
)

print("⏳ Starting backend", end='')
for _ in range(20):
    time.sleep(1)
    try:
        r = requests.get('http://localhost:8000/health', timeout=2)
        if r.status_code == 200:
            d = r.json()
            print(f"\n✅ Backend running! (PID {proc.pid})")
            print(f"   AI Provider : {d.get('ai_provider')}")
            print(f"   API Key     : {'✅ OK' if d.get('api_key_configured') else '❌ Not set'}")
            break
    except:
        print('.', end='', flush=True)
else:
    print("\n❌ Failed. Check log:")
    print(open('/tmp/backend.log').read()[-2000:])
```

### Step 2.8 — Test the API

FastAPI auto-generates API docs at `/docs`. Test the `/analyze` endpoint:

```python
import requests, json

test_payload = {
    "snapshot": {
        "machine_status": "WARNING",
        "health_score": 42.5,
        "overall_status": "WARNING",
        "sensors": {
            "vibration":   {"current": 6.8,   "status": "WARNING", "trending": "DEGRADING"},
            "temperature": {"current": 91.2,  "status": "WARNING", "trending": "STABLE"},
            "pressure":    {"current": 5.1,   "status": "NORMAL",  "trending": "STABLE"},
            "flow_rate":   {"current": 118.0, "status": "NORMAL",  "trending": "STABLE"},
        }
    }
}

print("🤖 Calling AI analysis...")
r = requests.post('http://localhost:8000/analyze', json=test_payload, timeout=30)

if r.status_code == 200:
    result = r.json()
    print(f"\n📊 AI Result:")
    print(f"  Risk Level   : {result.get('risk_level')}")
    print(f"  Confidence   : {result.get('confidence', 0)*100:.0f}%")
    print(f"  Summary      : {result.get('summary')}")
    print(f"  ETF (hours)  : {result.get('estimated_hours_to_failure')}")
    if result.get('recommended_actions'):
        print(f"  Action #1    : {result['recommended_actions'][0].get('action')}")
else:
    print(f"❌ HTTP {r.status_code}: {r.text[:300]}")
```

---

## Part 3 — Node-RED: Visual Data Pipeline (45 min)

### Objective
Install Node-RED on Colab, import the pre-built flow, and understand each processing node.

### Theory: What is Node-RED?

**Node-RED** is a visual programming tool for IoT developed by IBM:
- Drag and drop "nodes" to build data processing flows
- Each node does one specific thing: receive MQTT, process data, call an API, push to WebSocket
- Minimal coding required — ideal for prototyping and teaching

**Why use Node-RED in this system?**

Instead of writing Python code to:
1. Subscribe to MQTT
2. Validate data
3. Compute a rolling average
4. Detect anomalies
5. Call the AI API

→ Node-RED does all of this via a drag-and-drop interface that is easy to debug step by step.

**The flow in this system:**
```
[MQTT in] → [Parse & Validate] → [Rolling Buffer 60 readings]
               → [Compute Trends & Stats] → [Route: NORMAL/ANOMALY]
                                                    │
                                              If ANOMALY:
                                            [Throttle AI calls]
                                            [Build AI Payload]
                                            [POST /analyze]
                                            [Process AI Response]
                                            [WS → Dashboard]
```

### Step 3.1 — Install Node.js and Node-RED

```bash
# Step 1: Add Node.js 20 LTS repo and install
!curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
!apt-get install -y -q nodejs
!node --version
```

```bash
# Step 2: Install Node-RED globally (takes 1–2 minutes)
!npm install -g --unsafe-perm --silent node-red
!node-red --version
```

### Step 3.2 — Upload `nodered/flows.json`

**Upload:** Navigate to `/content/pumpguard/nodered/` → Upload `flows.json`

```python
import json
flows = json.load(open('/content/pumpguard/nodered/flows.json'))
print(f"✅ flows.json valid — {len(flows)} nodes")
node_types = set(n.get('type') for n in flows if 'type' in n)
print(f"   Node types: {', '.join(sorted(node_types))}")
```

### Step 3.3 — Start Node-RED

```python
import subprocess, time, os

nr_home = '/root/.node-red'
os.makedirs(nr_home, exist_ok=True)

import shutil
shutil.copy('/content/pumpguard/nodered/flows.json', f'{nr_home}/flows.json')

subprocess.run(['pkill', '-f', 'node-red'], capture_output=True)
time.sleep(1)

log = open('/tmp/nodered.log', 'w')
proc = subprocess.Popen(
    ['node-red', '--port', '1880', '--userDir', nr_home],
    stdout=log, stderr=subprocess.STDOUT
)

print("⏳ Starting Node-RED", end='')
for _ in range(20):
    time.sleep(2)
    try:
        r = requests.get('http://localhost:1880', timeout=3)
        if r.status_code == 200:
            print(f"\n✅ Node-RED running! (PID {proc.pid})")
            print(f"   UI: http://localhost:1880")
            break
    except:
        print('.', end='', flush=True)
else:
    print("\n❌ Failed. Check log:")
    print(open('/tmp/nodered.log').read()[-1500:])
```

### Step 3.4 — Expose Node-RED via public URL

```python
import subprocess, time, re

subprocess.run(['pkill', '-f', 'cloudflared'], capture_output=True)
time.sleep(1)

log_be = open('/tmp/cf_backend.log', 'w')
subprocess.Popen(
    ['cloudflared', 'tunnel', '--url', 'http://localhost:8000', '--no-autoupdate'],
    stdout=log_be, stderr=subprocess.STDOUT
)

log_nr = open('/tmp/cf_nodered.log', 'w')
subprocess.Popen(
    ['cloudflared', 'tunnel', '--url', 'http://localhost:1880', '--no-autoupdate'],
    stdout=log_nr, stderr=subprocess.STDOUT
)

print("⏳ Waiting for Cloudflare URLs", end='')
be_url = nr_url = None
for _ in range(30):
    time.sleep(2)
    try:
        m1 = re.search(r'https://[\w-]+\.trycloudflare\.com', open('/tmp/cf_backend.log').read())
        m2 = re.search(r'https://[\w-]+\.trycloudflare\.com', open('/tmp/cf_nodered.log').read())
        if m1: be_url = m1.group(0)
        if m2: nr_url = m2.group(0)
        if be_url and nr_url: break
    except: pass
    print('.', end='', flush=True)

print()
print("=" * 62)
print("🎉  SYSTEM IS LIVE!")
print("=" * 62)
if be_url:
    print(f"\n🌐  Dashboard  →  {be_url}/dashboard/")
    print(f"🔗  API Docs   →  {be_url}/docs")
if nr_url:
    print(f"🔧  Node-RED   →  {nr_url}")
print("=" * 62)
```

### Step 3.5 — Explore the Node-RED UI

Open the Node-RED URL in your browser. You will see the pre-imported flow.

**Node descriptions:**

| Node | Type | Purpose |
|------|------|---------|
| **Subscribe pump/sensors** | MQTT in | Listen on the topic, receive raw sensor data |
| **Parse & Validate** | Function | Validate JSON structure, add timestamp |
| **Rolling Buffer (60)** | Function | Keep the last 60 readings in context |
| **Compute Trends & Stats** | Function | Calculate avg, slope, std dev, detect trend |
| **If NORMAL / If ANOMALY** | Switch | Route messages based on status |
| **Throttle AI (1/10s)** | Delay | Limit AI call frequency — avoid API quota waste |
| **Build AI Payload** | Function | Package compact data for the AI call |
| **POST → /analyze** | HTTP Request | Call FastAPI backend for AI analysis |
| **WS → Dashboard** | WebSocket out | Push results to the browser in real time |

**Why a Rolling Buffer?**
A single data point is not enough to detect a trend. 60 readings × ~1 s = the last ~60 seconds, enabling calculation of:
- Slope (rising or falling trend)
- Standard deviation (signal stability)
- Rate of change (speed of change)

**Why throttle AI calls?**
Gemini's free tier has a rate limit. If an anomaly lasts 60 seconds, there is no need to call the AI 60 times — once every 10 seconds is sufficient.

---

## Part 4 — Data Replay & Live Demo (20 min)

### Step 4.1 — Upload `scripts/mqtt_replay.py`

**Upload:** Navigate to `/content/pumpguard/scripts/` → Upload `mqtt_replay.py`

**Purpose:** Reads `sensor.csv` (real data) and publishes it to MQTT one row at a time, simulating a real sensor device.

### Step 4.2 — Upload `data/sensor.csv` *(if available)*

> ⚠️ Large file (~124 MB). Upload takes 1–2 minutes.

**If you don't have the CSV:** Use the **Operator Controls** on the dashboard (⚙ button, top-right).

### Step 4.3 — Run data replay

> ⚠️ This cell runs **continuously** — press ⏹ to stop.

```python
import os, sys
os.chdir('/content/pumpguard')

if os.path.exists('data/sensor.csv') and os.path.exists('data/sensor_groups.json'):
    print("▶ Streaming sensor data to dashboard...")
    print("  compression=360: 1 minute of data = 1/6 second in demo")
    print("  start-at-anomaly: begin near the degradation point for a fast demo")
    print("-" * 50)
    os.system(
        f"{sys.executable} scripts/mqtt_replay.py "
        "--csv data/sensor.csv "
        "--config data/sensor_groups.json "
        "--start-at-anomaly "
        "--compression 360"
    )
else:
    print("ℹ️  No sensor.csv found")
    print("   → Open Dashboard → click ⚙ → select '⚠ Simulate Anomaly'")
    print("   AI will analyse automatically after ~30 seconds")
```

### Step 4.4 — Observe the Dashboard

Open the Dashboard URL in your browser. Key tabs to watch:

| Tab | Shows |
|-----|-------|
| **Overview** | Overall health score, machine status, 4 sensor cards |
| **Sensor Status** | All sensor readings, click any sensor for detail |
| **AI Recommendations** | Gemini analysis: risk level, actions, cost impact |
| **Alerts** | Alert history |

**To trigger AI analysis quickly:**
1. Click **⚙ Operator Controls** (top-right)
2. Select **"⚠ Simulate Anomaly"** or **"🔴 Simulate Critical"**
3. Switch to **AI Recommendations** — result appears in ~10–30 s

---

## Troubleshooting

| Problem | Cause | Solution |
|---------|-------|---------|
| Backend fails to start | Missing file or import error | Re-check that all files are uploaded correctly |
| AI shows `[MOCK]` | API key not set | Re-run Step 2.3 with a real key |
| Node-RED flow not imported | flows.json in wrong location | Ensure it is copied to `~/.node-red/flows.json` |
| Dashboard WebSocket not connecting | Tunnel URL has reset | Re-run the Cloudflare step |
| Colab session disconnects (~1.5 h) | Runtime timeout | Re-run from Part 1 Step 1.2 |
| No MQTT data | Replay not running | Run Part 4 or use Operator Controls |

### Quick debug commands

```python
# View backend log
print(open('/tmp/backend.log').read()[-3000:])

# View Node-RED log
print(open('/tmp/nodered.log').read()[-2000:])

# Check running services
import subprocess
out = subprocess.run(['ps', 'aux'], capture_output=True, text=True).stdout
for svc in ['mosquitto', 'uvicorn', 'node-red', 'cloudflared']:
    running = svc in out
    print(f"  {'✅' if running else '❌'} {svc}")
```

### Quick restart after Colab timeout

When the Colab Runtime disconnects:
1. ✅ Skip: Part 0 (directories) and Steps 2.1–2.5 (file uploads) — files are preserved
2. 🔄 Re-run: Part 1 Step 1.2 (MQTT) → Part 2 Step 2.7 (Backend) → Part 3 Step 3.3 (Node-RED) → Cloudflare

---

## Workshop Summary

By the end of this workshop you have built:

```
✅ MQTT Broker (Mosquitto)    ← receives data from the simulator
✅ Data Pipeline (Node-RED)   ← processes, computes trends, detects anomalies
✅ AI Backend (FastAPI)       ← connects to AI, manages WebSocket clients
✅ Dashboard (HTML/JS)        ← real-time browser interface
✅ AI Integration (Gemini)    ← analysis and maintenance recommendations
✅ Public URL (Cloudflare)    ← shareable link for anyone
```

**Review questions:**
1. Why does IoT use MQTT instead of REST API?
2. What is the purpose of a 60-reading rolling buffer?
3. Why do we throttle AI calls?
4. How does WebSocket differ from HTTP polling?
5. Why store API keys in `.env` rather than hardcoding them in the source?
