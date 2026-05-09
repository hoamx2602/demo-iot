# PumpGuard AI — Predictive Maintenance Platform
### Industrial Pump Monitoring · Oil & Gas · Powered by Claude AI

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        DATA FLOW                                │
│                                                                 │
│  sensor.csv  ──►  mqtt_replay.py  ──►  Mosquitto  ──►  Node-RED│
│  (dataset)        (replay script)     (MQTT broker)  (pipeline) │
│                                                                 │
│  Node-RED  ──►  FastAPI Backend  ──►  Claude API               │
│             ◄──  (AI analysis)    ◄──  (result)                │
│                       │                                         │
│                   WebSocket                                     │
│                       │                                         │
│               Web Dashboard  ◄──── Operator                    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Component Overview

### 1. `sensor.csv` — Dataset
Real data from an industrial pump (Kaggle Pump Sensor Data):
- 220,320 readings at 1-minute intervals (~5 months of operation)
- 52 real sensors: vibration, temperature, pressure, flow rate
- `machine_status` labels: NORMAL / BROKEN / RECOVERING

### 2. `scripts/analyze_sensors.py` — Initial Analysis
Run once during setup:
- Reads the CSV and groups 52 sensors into 4 physically meaningful groups
- Computes a scale factor to map raw values to real-world units (mm/s, °C, bar, m³/h)
- Identifies the first row showing signs of degradation
- Outputs `data/sensor_groups.json` — the config file used throughout the system

### 3. `scripts/mqtt_replay.py` — Replay Script
Simulates a real IoT device in the field:
- Reads the CSV row by row and computes a representative value for each sensor group
- Adds ±0.8% random noise so readings look natural
- Publishes to MQTT topic `pump/sensors` every ~0.17 s (equivalent to 1 real minute)
- Accepts control commands on topic `pump/control`: PAUSE / RESUME / JUMP:<row>

**Time compression:** 1 hour of real data = 10 seconds in the system.
Use `--start-at-anomaly` to start just before the degradation phase begins.

### 4. Mosquitto — MQTT Broker
Message routing hub between all components:
- MQTT (Message Queuing Telemetry Transport) — the industry standard for IoT
- Runs locally at `localhost:1883`
- Stateless — routes messages only, stores no logic
- Production equivalent: AWS IoT Core / Azure IoT Hub

### 5. `nodered/flows.json` — Node-RED Pipeline
Intermediate processing layer with three responsibilities:

**Rolling Buffer:** Keeps the last 60 readings (~60 real minutes) in memory.

**Compute Trends:** For each sensor group, calculates:
- `avg_60`: rolling average over 60 readings
- `slope`: rising/falling trend (linear regression)
- `std_dev`: signal stability
- `rate_of_change`: change vs. the previous reading
- `status`: NORMAL / WARNING / CRITICAL

**Throttle & Route:** Forwards to AI only on anomaly, at most once per 10 seconds — avoids burning API quota while the machine is healthy.

### 6. `backend/server.py` — FastAPI Backend
Central coordinator:
- **MQTT Bridge:** Subscribes to `pump/sensors` and pushes data to all dashboard WebSocket clients
- **POST /analyze:** Receives a snapshot from Node-RED or the dashboard and calls the Claude API
- **WebSocket /ws:** Real-time broadcast to all connected dashboard clients
- **AI integration:** System prompt tuned for business-friendly, actionable output

### 7. `dashboard/index.html` — Web Dashboard
The single user-facing interface:

**Tab 1 — Live Sensor Feed:**
Health score ring, 4 sensor cards with sparkline charts, NORMAL/WARNING/CRITICAL badge updating in real time.

**Tab 2 — Machine Health Timeline:**
Timeline from NORMAL → DEGRADING → (predicted) FAILURE. First AI detection point is marked. 60-reading multi-sensor trend chart. Event log.

**Tab 3 — AI Recommendation:**
Risk level + confidence score. List of anomalous sensors with physical explanations. 4 prioritised actions with timelines and owners. Cost impact: unplanned failure cost vs. planned maintenance cost.

---

## Starting the System

### Startup order (important)

```bash
# Terminal 1 — MQTT Broker (must start first)
mosquitto -p 1883

# Terminal 2 — Backend + WebSocket
cd pump-iot-demo
source venv/bin/activate
uvicorn backend.server:app --host 0.0.0.0 --port 8000

# Terminal 3 — Replay data
cd pump-iot-demo
source venv/bin/activate
python scripts/mqtt_replay.py \
  --csv data/sensor.csv \
  --config data/sensor_groups.json \
  --start-at-anomaly

# Browser — Dashboard
open http://localhost:8000/dashboard/
```

### Quick component check

| Component | How to verify |
|-----------|---------------|
| Mosquitto | `mosquitto_pub -t test -m hello` — no error |
| Backend | http://localhost:8000/health → `{"status":"ok"}` |
| WebSocket | Dashboard badge turns green "Live" |
| MQTT data | Replay terminal is printing rows |
| Node-RED | http://localhost:1880 — badge shows "connected" |

---

## API Key Configuration

```bash
cp backend/.env.example backend/.env
```

Open `backend/.env` and fill in one of:

```env
# Use Claude
AI_PROVIDER=claude
ANTHROPIC_API_KEY=sk-ant-...

# Or use OpenAI
AI_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

Restart the backend after any changes.

---

## Demo Script

### Preparation (5 minutes)

1. Start Mosquitto, backend, replay — **in the order above**
2. Use `--start-at-anomaly` to begin 200 rows before the degradation point
3. Open the dashboard on the "Live Sensor Feed" tab
4. Let it run for 30–60 seconds so charts have data before the audience arrives

### Part 1 — Normal operation (1–2 min)

**Open "Live Sensor Feed" tab**

Point out:
- Health Score 88%, green colour
- All 4 sensor badges showing NORMAL
- Stable sparklines, no large fluctuations

Say: *"This is the pump running normally. The system reads 52 real sensors, groups them into 4 representative metrics, and updates every second."*

### Part 2 — Fault detection (2–3 min)

**Watch badges start turning yellow** (happens naturally as the replay reaches the degradation phase)

Or manually press **"⚠ Simulate Anomaly"** in the Operator Controls.

Point out:
- Health Score drops to ~50%, yellow
- Vibration and Temperature turn WARNING
- Sparklines trend visibly upward
- Switch to "Machine Health Timeline" — AI Detection point is marked on the timeline

Say: *"Sensors are crossing thresholds. The system detects it instantly — no need to wait for a scheduled inspection."*

### Part 3 — AI analysis (3–4 min)

**Press "🤖 Run AI Analysis"** — wait 2–3 s, the dashboard switches to AI Recommendation automatically.

Walk through each section:

**Risk Level + Confidence:** *"AI rates this HIGH risk with 87% confidence, predicting failure within 18 hours."*

**Anomalous Sensors:** *"Specifically, vibration is at 5.8 mm/s against a normal range of 0–4.5. Temperature is rising simultaneously — both trending together is a classic bearing wear signature."*

**Recommended Actions:** *"AI proposes 4 prioritised actions with specific timelines and assigned owners — operators know exactly what to do next."*

**Cost Impact — the key section:** *"Doing nothing: $524,000 in losses — 48 h downtime, emergency repair, environmental fines. Planned maintenance today: $10,500. One early alert saved $513,500."*

### Closing line

*"Previously, engineers had to wait for the machine to break down — or run costly scheduled inspections even when everything was fine. With IoT + AI, the system warns 18 hours in advance — enough time to schedule maintenance during a low-demand shift, order parts, and arrange personnel. Unplanned downtime drops, maintenance costs fall, and machine lifespan increases."*

---

## Deploy to AWS

### Option A — EC2 (fastest)

```bash
# 1. Launch EC2 t3.medium, Ubuntu 22.04
# 2. Open Security Group: ports 22, 1883, 8000, 1880

# 3. On EC2:
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker ubuntu

# 4. Copy project to EC2
scp -r pump-iot-demo ubuntu@<EC2-IP>:~/

# 5. Start
cd ~/pump-iot-demo/docker
cp ../backend/.env.example ../backend/.env
nano ../backend/.env   # fill in API key
docker compose up -d

# 6. Replay from local machine, pointing at EC2
MQTT_HOST=<EC2-IP> python scripts/mqtt_replay.py \
  --csv data/sensor.csv \
  --config data/sensor_groups.json \
  --start-at-anomaly
```

Dashboard: `http://<EC2-IP>:8000/dashboard/`

### Option B — Docker local

```bash
cd pump-iot-demo/docker
cp mosquitto.conf.example mosquitto.conf 2>/dev/null || true
docker compose up -d
```

---

## Directory Structure

```
pump-iot-demo/
├── data/
│   ├── sensor.csv              ← Kaggle dataset (place here)
│   └── sensor_groups.json      ← auto-generated by analyze_sensors.py
├── scripts/
│   ├── analyze_sensors.py      ← run once at setup
│   └── mqtt_replay.py          ← run whenever you want to stream data
├── nodered/
│   ├── flows.json              ← import into Node-RED
│   └── setup_nodered.sh        ← install + import automatically (macOS)
├── backend/
│   ├── server.py               ← FastAPI + WebSocket + AI
│   ├── requirements.txt
│   └── .env.example            ← copy to .env, fill in API key
├── dashboard/
│   └── index.html              ← open directly in browser
├── docker/
│   ├── docker-compose.yml      ← deploy all services
│   ├── Dockerfile.backend
│   └── mosquitto.conf
└── README.md
```

---

## Troubleshooting

**Dashboard "Offline"**
→ Backend is not running. Run: `uvicorn backend.server:app --port 8000`

**Node-RED "connecting"**
→ Mosquitto is not running. Run: `mosquitto -p 1883`

**AI returns pre-loaded analysis**
→ API key not set. Open `backend/.env`, fill in the key, restart the backend.

**No data appearing on dashboard**
→ Check startup order: Mosquitto → Backend → Replay. Backend must start before the replay script.

**Port 1883 already in use**
→ `lsof -i :1883 | grep LISTEN` then kill that process, or `brew services restart mosquitto`
