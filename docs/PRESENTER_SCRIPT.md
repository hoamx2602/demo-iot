# PumpGuard AI — Presenter Script

> **How to use this script**
> Lines in *italics* are suggested spoken words. Lines in `[brackets]` are stage directions for you. Normal text is background context you can draw from when answering questions.
> Estimated total time: **60–75 minutes** including live demo.

---

## Opening (5 min)

*"Welcome everyone. Today we're going to build something real — a system that monitors an industrial pump in real time, detects the early signs of mechanical failure, and uses AI to explain what's going wrong and recommend what to do about it.*

*This isn't a toy example. The data we're working with is real sensor data collected from an actual pump running to failure. And every component we use today — MQTT, Node-RED, FastAPI, Groq — is what you'd find in a production IoT system at a factory or utility plant.*

*By the end of this session, you'll have a live dashboard showing 4 sensor streams, an AI that reads the anomalies and tells you how many hours you have before the machine breaks down, and optionally an email alert sent to your inbox.*

*Let's get started."*

---

## Part 1 — Environment Check (3 min)

`[Run cells 1.1 and 1.2]`

*"We're running on Google Colab — a free cloud environment that gives us a Linux machine to work with. The first thing we do is verify the Python version and create the folder structure our project expects.*

*Notice we're creating five folders: backend, dashboard, nodered, scripts, and data. Each one maps to a specific layer of the system. We'll fill them in as we go."*

**Talking point if asked about Colab:**
> Colab is convenient for workshops — no local install required. In production you'd run this on a VM, a Raspberry Pi, or a Docker container on an edge gateway. The code doesn't change; only the host changes.

---

## Part 2 — Install Dependencies (5 min)

`[Run cells 2.1 and 2.2]`

*"We're installing two categories of things here.*

*First, the Python packages — FastAPI for the REST API and WebSocket server, Paho for MQTT, Groq's SDK for AI, and a few utilities.*

*Second, two system-level tools: Mosquitto, which is our MQTT broker, and Node-RED, which is our edge processing engine. These two are the backbone of the IoT message flow."*

**Pause for question:**
*"Before we move on — does anyone know what MQTT stands for, and why it's used in IoT instead of regular HTTP?"*

> **Answer:** MQTT = Message Queuing Telemetry Transport. Designed by IBM in the 90s for oil pipeline telemetry over satellite links. It's lightweight (fixed header = 2 bytes), supports pub/sub (one publisher, many subscribers), and handles unreliable networks gracefully with QoS levels. HTTP requires a request for every piece of data; MQTT pushes data automatically when available.

---

## Part 3 — Backend: server.py (8 min)

`[Upload server.py and email_alert.html, then run the .env cell]`

*"This is the central nervous system of the whole system. Let me walk you through the key pieces.*

*MQTTBridge — this component connects to Mosquitto and subscribes to the topic pump/sensors. Every time the simulator publishes a reading, MQTTBridge receives it immediately, stores it in memory, and makes it available to the WebSocket broadcaster.*

*ConnectionManager — think of it as a switchboard operator. Every browser tab that opens the dashboard connects here over WebSocket. The manager keeps track of all active connections and uses a per-connection write lock — so 20 students viewing the dashboard simultaneously don't interfere with each other.*

*_broadcast_loop — runs at 2 Hz, twice per second. It takes the latest sensor reading and pushes it to every connected client. The simulator might send 6 readings per second, but we only broadcast 2 per second. This prevents browsers from receiving a backlog of 50 messages and having to render them all at once.*

*POST /alert — called by Node-RED when an anomaly crosses the threshold. We call Groq AI and, if configured, send an email.*

*POST /analyze — accepts a sensor snapshot and returns a full AI analysis: risk level, root cause hypothesis, recommended actions, estimated hours to failure, estimated financial savings.*

*_ai_semaphore — limits concurrent Groq calls to 2. On the free tier, Groq allows a generous number of requests per day, but we don't want 30 students triggering simultaneous calls and hitting rate limits."*

*"Three things to configure: your Groq API key — free at console.groq.com — your Resend key if you want email alerts, and the recipient address. The sender address is fixed as Resend's onboarding domain."*

`[Run the .env cell]`

---

## Part 4 — MQTT Broker: Mosquitto (5 min)

`[Run cells 4.1 and 4.2]`

*"Mosquitto is the message broker — the post office of our system. It doesn't care what the messages say; it just routes them from publishers to subscribers.*

*In our architecture, one publisher: the sensor simulator. Two subscribers: the FastAPI backend, which forwards data to the dashboard, and Node-RED, which analyses the data for anomalies.*

*Both subscribers receive every message independently. This is the power of pub/sub — you can add a third subscriber at any time, say a database writer or an alert aggregator, without changing the publisher at all.*

*Cell 4.2 tests the connection by briefly connecting a MQTT client, verifying it reaches the broker, then disconnecting. MQTT: connected means we're good."*

**Talking point:**
> In production, the broker would be HiveMQ Cloud, AWS IoT Core, or Azure IoT Hub. These add TLS encryption, authentication, and message persistence. Mosquitto is the open-source local equivalent — perfect for development and edge deployments.

---

## Part 5 — Node-RED Pipeline (8 min)

`[Upload flows.json, run cells 5.2 and 5.3]`

*"Node-RED is a visual flow-based programming tool — you connect nodes together like a flowchart, and data flows through them in sequence. Our flow has six steps.*

*Step 1 — MQTT In. Subscribes to pump/sensors. Every message from the simulator arrives here as JSON.*

*Step 2 — Parse and Validate. We parse the JSON and verify all required fields are present. Malformed messages are dropped.*

*Step 3 — Rolling Buffer. We accumulate the last 60 readings — a 60-second window of sensor history.*

*Step 4 — Compute Trends. For each sensor group, we calculate the slope — is the value rising or falling? — the standard deviation, and an overall anomaly score. This is the intelligence layer.*

*Step 5 — Throttle. At most one AI call every 60 seconds. Without this, every anomalous reading would trigger a Groq call — at 6 readings per second, that's 360 calls per minute, burning through quota in minutes.*

*Step 6 — POST /alert. When the anomaly score crosses the threshold, Node-RED sends the snapshot to FastAPI. The backend handles AI and email from there.*

*The key insight: Node-RED decides when something is wrong. The AI only gets called when Node-RED is confident enough to escalate. This reduces API calls by 99.7% — while detecting anomalies just as reliably."*

**Pause for question:**
*"Why do we need Node-RED at all? Why not send raw data straight to the AI?"*

> **Answer:** Three reasons. Cost — calling an LLM at 6/s would cost hundreds of dollars per day at production scale. Latency — you need 30-60 seconds of data to calculate meaningful trends; the AI can't detect a trend from one point. Reliability — edge processing works even when the internet is down.

---

## Part 6 — Dashboard & FastAPI (8 min)

`[Upload index.html and control.html, run cells 6.1 and 6.2]`

*"FastAPI does three things: serves the dashboard HTML files, maintains WebSocket connections to push live data to the browser, and exposes REST endpoints for AI analysis and alerts.*

*Let me describe what the dashboard shows:*

*The Health Ring — a circular gauge from 0 to 100 percent. A healthy pump sits at 85-100. As conditions degrade, it drops through yellow into red.*

*Four sensor gauges — vibration, temperature, pressure, flow rate. Each has a green zone, an amber warning zone, and a red critical zone. The zones are computed from the actual data distribution, not hardcoded numbers.*

*The Failure Timeline — four milestones: when we started monitoring, when the first anomaly was detected, where we are now, and the AI's estimated time of failure. Operators can immediately see how much runway they have.*

*The AI Panel — risk level, a natural-language explanation of the problem, ranked recommended actions, and the estimated financial savings if maintenance is performed now versus allowing the failure."*

`[Run ngrok cell — share the URL with participants]`

*"ngrok creates a public HTTPS URL that tunnels to our local server. Share this URL — open it now on your device. You should see the dashboard with live sensor data already flowing."*

`[Pause for participants to open the dashboard]`

---

## Part 7 — Sensor Simulator (6 min)

`[Run cell 7.1 — download and analyse]`

*"The dataset contains 220,000 rows and 52 sensor columns from a real industrial pump. The analyze_sensors script does three things: computes a divergence score per sensor group — how much each sensor changes from healthy to broken — computes scale and offset to map raw values into real engineering units, and produces sensor_groups.json, the config file for the simulator.*

*The simulator then reads the CSV row by row at one reading per second, applies these mappings, adds realistic noise, and publishes over MQTT. It runs in a loop — the dashboard never goes blank."*

**Talking point:**
> In a real deployment, this simulator is replaced by physical sensors: a vibration sensor on the pump housing, a temperature probe on the motor, a pressure transducer on the outlet, a flow meter on the pipe. The MQTT messages look identical — only the source changes.

---

## Part 8 — Full Pipeline Check (3 min)

`[Run cell 8]`

*"/health confirms FastAPI is running, MQTT is connected, and shows how many WebSocket clients are connected right now. /latest gives us the most recent sensor reading — NORMAL status, health score in the 80s. Everything green — we're ready for the demo."*

---

## Part 9 — DEMO: Simulate a Fault (10 min)

> ⚡ **This is the centrepiece. Take your time here — keep the dashboard on the projector.**

**Set the scene:**

*"Imagine you're the operations manager at a water treatment plant. It's 2 AM. You have 50 pumps running. You can't physically check each one. This is what predictive maintenance solves.*

*Right now, our pump is healthy. Health Ring at 85-90%. All four gauges in the green. I'm going to simulate a mechanical fault developing."*

`[Run Scenario A — switch to CRITICAL]`

*"I've restarted the simulator from the point in the dataset where the pump begins to fail. This is real failure data — recorded as the pump was running to destruction in a controlled test.*

*Watch the dashboard — the numbers will start changing in about 3 seconds."*

`[Pause — let the audience watch]`

*"Vibration is climbing. Temperature is rising. Flow rate is dropping — the pump is moving less fluid as the impeller degrades. Pressure is fluctuating. The Health Ring is dropping through amber into red.*

*Node-RED has been watching this for the past 60 seconds, computing trends. When the anomaly score crosses the threshold..."*

`[Wait for AI analysis to appear — typically 60-90 seconds]`

*"There it is. Risk level: CRITICAL. Estimated hours to failure: [read from screen]. Recommended action: [read from screen]. Estimated savings if you act now: [read from screen dollars].*

*This analysis ran in under 2 seconds on Groq's hardware. A GPU-based model would take 10-15 seconds for the same output."*

**Expected questions:**

- *"How does Node-RED know when to trigger?"* → Anomaly score threshold. We compute slope and standard deviation for each sensor group over the 60-reading window. When the combined score crosses a configurable threshold, the throttle opens.

- *"What if the AI is wrong?"* → The AI generates a hypothesis, not a command. A human engineer reviews and decides whether to act. It's an advisory tool, not an autonomous controller.

- *"How accurate is the time estimate?"* → It's an **Early Warning heuristic**, not a true Machine Learning prediction. The LLM estimates based on a single snapshot of data and general engineering rules. To get a highly accurate Remaining Useful Life (RUL) prediction, you would need to store this data in a time-series DB and train a dedicated ML model (like LSTM or Random Forest) on the historical failure patterns of *this specific pump*. The AI estimate here is directionally correct — meant to classify urgency (e.g. stop now vs fix next week) rather than give a perfect countdown.

`[Run Scenario B — reset to NORMAL]`

*"Let's reset — imagine maintenance replaced the bearing and brought the pump back online. Watch the Health Ring climb back up."*

---

## Part 10 — Summary & What's Next (8 min)

*"Let's look at what we built. Seven components, each doing a specific job:*

*The simulator — your physical sensors. In production, real devices on the machine. Data format: identical.*

*Mosquitto — your message backbone. Routes reliably, knows nothing about business logic.*

*Node-RED — your edge intelligence. Filters noise, computes trends, decides what to escalate. Works even when the cloud is unreachable.*

*FastAPI — your API and WebSocket hub. Glue between edge and browser.*

*Groq LLM — turns numbers into words. A sensor snapshot becomes a human-readable explanation a maintenance engineer can act on without a data science degree.*

*The dashboard — the human interface. Everything an engineer needs to understand in 10 seconds at 2 AM.*

*Resend — your out-of-band alert. When something critical happens, you don't rely on the operator to be watching the screen."*

**Three design decisions worth highlighting:**

*"Edge filtering reduced AI calls from 6 per second to 1 per minute — 99.7% fewer calls, same detection quality. At production scale, that's the difference between $50/month and $10,000/month.*

*Decoupled broadcast — a slow connection on one student's laptop can't slow down data for everyone else.*

*Structured AI output — we force JSON, not prose. You can parse the response and create a work order in your CMMS automatically, without brittle string parsing."*

**Extension ideas:**

1. **InfluxDB + Grafana** — store every reading, query 30-day trends, correlate maintenance with degradation.
2. **Offline operation** — replace Groq with Ollama running locally, Resend with local SMTP. Full air-gapped operation.
3. **Multi-machine scale** — topic hierarchy `factory/{site}/pump/{id}/sensors`. One Node-RED wildcard subscription catches 50+ machines.
4. **Trained ML model** — replace rule-based anomaly scoring with an Isolation Forest or LSTM trained on the 220k-row dataset.
5. **Digital twin** — run a physics model alongside live data, display a predicted failure trajectory.

**Closing:**

*"The pattern you saw today — sensor data → edge processing → AI analysis → human-readable alert — is used in wind turbine monitoring, aircraft engine maintenance, and smart grid fault detection. The tools change; the architecture doesn't. Thank you."*

---

## Quick Reference

| Metric | Value | What to say |
|--------|-------|-------------|
| Dataset | 220k rows, 52 columns | "Real failure data from a controlled pump test" |
| Sensor groups | 4 (vibration, temp, pressure, flow) | "The four key indicators of pump health" |
| Simulator speed | 1 row/second | "Realistic for industrial sensors" |
| Node-RED window | 60 readings (~60s) | "One minute of context before deciding" |
| AI call rate | Max 1 per 60s | "99.7% fewer API calls than naive approach" |
| Groq response | ~1-2 seconds | "10x faster than GPU alternatives" |
| Groq free tier | 14,400 req/day | "Enough for a 40-machine workshop" |
| Broadcast rate | 2 Hz | "Smooth UI without overloading slow connections" |
| WS ping interval | 20s | "Keeps ngrok tunnels alive" |
| Groq timeout | 25s | "Under ngrok's 30s gateway limit" |

---

## Common Problems

| Symptom | What to say | Fix |
|---------|-------------|-----|
| Dashboard blank | "FastAPI may still be initialising — give it 10 seconds" | Check cell 6.1 output |
| No data on gauges | "Simulator may have stopped — restarting" | Re-run cell 7.2 |
| AI panel not updating | "Node-RED needs 60 readings to build its window — wait 60-90s after switching to CRITICAL" | Wait |
| ngrok URL not working | "ngrok rotated the URL — sharing the new one now" | Re-run cell 6.2 |
| Email not received | "Check spam, and verify the Resend key in .env" | Confirm RESEND_API_KEY is set |
