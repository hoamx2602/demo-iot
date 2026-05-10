# PumpGuard AI — System Components & Discussion Questions

> Reference material for workshop instructors and participants.

---

## Part A — Component Roles in the IoT Architecture

### 1. Sensor Simulator (`mqtt_replay.py`)
**Role: Perception Layer — Data Source**

In a real deployment, this layer would be physical sensors mounted on the pump (vibration probes, thermocouples, pressure transducers, flow meters). In this workshop, the simulator replays 220,000 rows of real industrial sensor data from a CSV file at 360× compression.

**Why it matters in IoT:**
The perception layer is where the physical world becomes digital. Every IoT system starts here — without reliable, high-frequency sensor data, no downstream intelligence is possible. The simulator faithfully reproduces the degradation pattern of a real pump failure, giving participants a realistic signal to work with.

**Key design choices:**
- Publishes every ~167 ms → 6 Hz sampling rate
- Switches between `NORMAL` and `BROKEN` mode to simulate pump degradation
- Uses the MQTT protocol to decouple itself from all consumers

---

### 2. Mosquitto MQTT Broker
**Role: Network & Transport Layer — Message Bus**

Mosquitto is the intermediary that receives published messages and routes them to all subscribers. No publisher knows who is consuming its data, and no subscriber knows where the data originates — they only share a **topic name** (`pump/sensors`).

**Why it matters in IoT:**
MQTT was designed for constrained networks (low bandwidth, high latency, unreliable links). Its publish/subscribe model naturally supports **one-to-many** distribution — a single sensor publish reaches Node-RED, the FastAPI backend, and any other subscriber simultaneously, with no extra work.

**Key properties in this system:**
- QoS 1: at-least-once delivery guarantee — no data loss even on brief disconnects
- Persistent connection: broker maintains the session, reducing reconnect overhead
- Topic hierarchy: `pump/sensors` is extensible (e.g. `pump/alerts`, `pump/control`)

---

### 3. Node-RED
**Role: Processing Layer — Edge Intelligence**

Node-RED sits between the raw sensor stream and the backend. It subscribes to MQTT, accumulates a **rolling buffer** of 60 readings (~30 seconds of history), computes statistical trends, and decides whether to trigger an AI alert.

**Why it matters in IoT:**
In industrial IoT, "edge processing" means computing anomaly scores close to the data source — before sending anything to the cloud. This reduces bandwidth, latency, and cloud compute cost. Node-RED makes this logic **visual and auditable**: each processing step is a node you can inspect and debug individually.

**What it computes:**
| Metric | Purpose |
|--------|---------|
| Rolling average | Smooth out sensor noise |
| Linear slope | Detect rising/falling trends |
| Standard deviation | Measure signal stability |
| `anomaly_score` (0–1) | Composite health indicator |

**Decision rule:** If `anomaly_detected = true` (any sensor in WARNING or CRITICAL state based on trends), Node-RED throttles to 1 call per 60 s and posts an `AlertRequest` to the FastAPI `/alert` endpoint.

---

### 4. FastAPI Backend (`server.py`)
**Role: Processing Layer — Data Hub & API Gateway**

The backend is the central nervous system of the application. It:
- Bridges MQTT to WebSocket (raw sensor relay)
- Exposes REST endpoints for Node-RED (`/alert`, `/analyze`) and the dashboard (`/control`, `/simulate/inject`)
- Manages all WebSocket client connections with per-connection write locks
- Rate-limits broadcasts at 2 Hz to prevent backlog on slow connections

**Why it matters in IoT:**
A backend in IoT acts as the **aggregation and normalization point** — it translates heterogeneous data (MQTT binary, REST JSON) into a single stream that the dashboard and AI service can consume. The WebSocket hub enables real-time push without polling.

**Key engineering details:**
- `_broadcast_loop` at 2 Hz: decouples ingest rate from send rate — prevents freeze on ngrok/mobile
- `_forced_state` lock: lets a presenter override the dashboard state during a live demo without Node-RED flickering it back
- `_ai_semaphore(2)`: caps concurrent Groq calls to prevent quota exhaustion and fd leaks
- Slow-request middleware: logs any endpoint taking > 1 s for event-loop diagnostics

---

### 5. Groq LLM — `llama-3.3-70b-versatile`
**Role: AI Analytics & Decision Layer**

When Node-RED detects an anomaly, the backend sends a structured sensor snapshot to Groq's LLM API. The model returns a **business-interpretable JSON** response: risk level, root cause hypothesis, ranked maintenance actions, estimated hours to failure, and cost savings from early intervention.

**Why it matters in IoT:**
Raw sensor thresholds tell you *that* something is wrong. AI tells you *why*, *how urgent*, and *what to do*. This moves the system from reactive alerting to **predictive maintenance** — the highest-value application in industrial IoT.

**Why Groq specifically:**
- LPU (Language Processing Unit) inference: ~10× faster than GPU-based providers
- Free tier: 30 RPM, 14,400 RPD — sufficient for multi-user workshops
- `response_format: json_object`: guarantees valid JSON output, no parsing failures
- `llama-3.3-70b-versatile`: strong technical reasoning, understands engineering context

---

### 6. Real-time Dashboard (`index.html`)
**Role: Application Layer — Human-Machine Interface**

A single-page application that connects to the backend via WebSocket and renders live sensor data, health scores, AI recommendations, and the failure prediction timeline. No framework — pure HTML/CSS/JS for zero-dependency deployability.

**Why it matters in IoT:**
The dashboard is the operator's window into the physical machine. In predictive maintenance, the value is in **time to act**: the AI recommendation panel, the countdown to estimated failure, and the maintenance cost savings are all designed to help an operator make a decision in seconds, not minutes.

**Key UI components:**
| Component | Purpose |
|-----------|---------|
| Health Ring | At-a-glance machine health (0–100%) |
| Sensor Gauges | Per-group real-time readings with threshold bands |
| Failure Prediction Timeline | 4-milestone journey: Start → Anomaly → Now → Est. Failure |
| AI Recommendation Panel | Risk level, actions, estimated savings |
| Operator Controls | Presenter state override (Normal / Warning / Critical) |

---

### 7. Resend Email Alert
**Role: Application Layer — Out-of-Band Notification**

When the `/alert` endpoint is called, after the AI analysis completes, the backend sends an HTML email via the Resend API. The email includes the sensor snapshot, AI risk level, recommended actions, and a link to the dashboard.

**Why it matters in IoT:**
A dashboard only helps if someone is watching it. Email (and in production: SMS, push notification, SCADA integration) ensures that an anomaly triggers a response even when no operator is logged in. This closes the loop between **detection** and **human action**.

---

## Part B — Technical & Discussion Questions

### 🔧 IoT Architecture & Protocol

1. **Why does this system use MQTT instead of HTTP REST for sensor data?**
   *(Hint: think about connection model, overhead, and fan-out)*

2. **What would happen to the system if the MQTT broker went down for 10 seconds? Which components would be affected and how?**

3. **QoS 1 guarantees "at-least-once" delivery. In what scenario could this cause a problem for this system? How would you handle duplicate messages?**

4. **The simulator publishes at 6 Hz (167 ms interval). If a real pump has 52 sensors each sampling at 100 Hz, what changes would be required in the architecture?**

5. **In the current system, both the MQTT bridge and Node-RED subscribe to `pump/sensors`. What are the trade-offs of this design vs. having only one subscriber that fans out internally?**

6. **The system uses a rolling buffer of 60 readings. How would you decide the right buffer size for a different IoT use case (e.g. a temperature sensor in a cold storage room)?**

---

### ⚙️ System Design & Engineering

7. **The broadcast loop runs at 2 Hz regardless of how fast Node-RED sends data. Why is this rate-limiting important? What could go wrong without it on a slow connection like ngrok?**

8. **The `/control/{state}` endpoint locks a `_forced_state` that Node-RED cannot override. Why is this lock necessary for a live demo? What is the risk if you remove it?**

9. **The Groq client is created once at startup as a singleton (`_groq_client`). What problem does this solve compared to creating a new client on every request?**

10. **The system uses `asyncio.Semaphore(2)` to cap concurrent AI calls. If you had 30 workshop participants all triggering an anomaly at the same time, what would happen? How would you scale this?**

11. **Why is the Resend email call wrapped in `asyncio.to_thread()`? What would happen to the WebSocket broadcasts if it ran synchronously in the event loop?**

12. **The `_nodered_last_inject` timestamp suppresses MQTT bridge broadcasts for 5 seconds after Node-RED posts. What is the purpose of this, and what edge case could cause the dashboard to show no data?**

---

### 🤖 AI & Machine Learning

13. **The LLM receives a structured JSON sensor snapshot, not raw time-series data. What information is lost in this abstraction? How does the rolling buffer in Node-RED partially compensate?**

14. **The system prompt instructs the LLM to return a JSON with `risk_level`, `estimated_hours_to_failure`, and `recommended_actions`. What are the risks of trusting an LLM's numerical estimates (like hours to failure) in a safety-critical context?**

15. **The AI response includes `confidence` (0–1). How should an operator interpret a `HIGH` risk level with `confidence: 0.45`? What UI change would communicate this uncertainty better?**

16. **The system throttles AI calls to 1 per 60 seconds. During a prolonged degradation, the sensor readings may change significantly. How would you design a smarter throttling strategy that triggers AI re-analysis when the situation changes meaningfully?**

17. **Node-RED computes `anomaly_score` using handcrafted thresholds and linear slope. What are the limitations of this rule-based approach vs. a trained ML model (e.g. Isolation Forest, LSTM)? What would you need to train a model for this pump?**

18. **The LLM is given the current sensor snapshot but no historical context beyond what Node-RED summarizes. How would access to the full 60-reading trend history change the quality of the AI's recommendations?**

---

### 📊 IoT Business & Use Case

19. **The AI estimates "cost savings from planned maintenance vs. emergency repair." What data would you need to make this estimate accurate for a real factory? What are the hidden costs an LLM might miss?**

20. **This system generates one alert email per anomaly (with a 60 s cooldown). In a real factory with 200 pumps, how would you manage alert fatigue? What filtering or prioritization mechanisms would you add?**

21. **The current system has no data persistence — sensor readings and AI analyses are lost when the server restarts. What would you add to support trend analysis over weeks/months? What database would you choose and why?**

22. **MQTT topics are flat strings (`pump/sensors`). If you extended this to a factory with 50 machines, how would you design the topic hierarchy? What Node-RED changes would be required?**

23. **The system runs on a single Google Colab instance. Identify 3 single points of failure and propose how you would address each in a production deployment.**

24. **A customer asks: "Can I use this system without internet?" (air-gapped factory). Which components work offline? Which require internet? What changes would enable fully offline operation?**

---

### 🔬 Advanced / Open-ended

25. **The dashboard suppresses WebSocket sensor updates while the local simulator is running (`if (!_simInterval)`). Why? What visual artifact would appear without this guard?**

26. **Design a "digital twin" extension for this system: a simulated copy of the pump that runs in parallel and predicts what will happen in the next hour based on current trends. What components would you add?**

27. **The current anomaly detection is threshold-based (vibration > 7.0 mm/s = CRITICAL). A pump running at 50% load has different normal ranges than one at 100% load. How would you make the thresholds context-aware?**

28. **If you replaced Groq/llama-3.3-70b with a small on-device model (e.g. Phi-3 mini running on a Raspberry Pi), what trade-offs would you accept? What IoT scenarios make this trade-off worthwhile?**

---

*Questions marked with a 🔧 are suitable for all participants. Questions marked ⚙️ are for software/systems track. Questions marked 🤖 are for AI/ML track. Questions marked 📊 are for business/product track.*
