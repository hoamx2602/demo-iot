"""
PumpGuard AI — Backend Server
- FastAPI + WebSocket
- Subscribe MQTT → broadcast sensor data to dashboard via WebSocket
- POST /analyze  → Groq API (llama-3.3-70b) → returns business-friendly JSON
- POST /alert    → Resend email alert on anomaly detection

Run:
    uvicorn backend.server:app --host 0.0.0.0 --port 8000 --reload

Environment variables (set in .env):
    GROQ_API_KEY=gsk_...
    RESEND_API_KEY=re_...
    ALERT_FROM=PumpGuard AI <alerts@yourdomain.com>
    ALERT_TO=engineer@company.com
    MQTT_HOST=localhost
    MQTT_PORT=1883
"""

import asyncio
import json
import os
import ssl
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

# ─── Config ─────────────────────────────────────────────────────────────────
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
AI_MODEL     = os.getenv("AI_MODEL", "llama-3.3-70b-versatile")  # Groq free: 14,400 req/day, 30 RPM

# Singleton Groq client — created once, reused for all calls.
# Creating a new AsyncGroq() per call leaks httpx connections and
# exhausts file descriptors after many requests → server timeout.
_groq_client = None
if GROQ_API_KEY:
    from groq import AsyncGroq
    _groq_client = AsyncGroq(api_key=GROQ_API_KEY)

# Limit concurrent AI calls to 2 — prevents runaway coroutine
# accumulation when Node-RED + dashboard both trigger analysis.
_ai_semaphore = asyncio.Semaphore(2)

MQTT_HOST     = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT     = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")
MQTT_TLS      = os.getenv("MQTT_TLS", "false").lower() in ("true", "1", "yes")
TOPIC_SENSORS = "pump/sensors"

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
ALERT_FROM     = os.getenv("ALERT_FROM", "PumpGuard AI <onboarding@resend.dev>")
ALERT_TO       = [e.strip() for e in os.getenv("ALERT_TO", "").split(",") if e.strip()]

# ─── AI System Prompt ────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are an AI-powered predictive maintenance analyst specializing in industrial pump systems for oil & gas operations.

You receive real-time sensor data snapshots from a pump monitoring system. Your job is to:
1. Assess the current risk level
2. Identify which sensors show anomalies and explain WHY they matter
3. Predict the estimated time to failure (be specific)
4. Recommend concrete maintenance actions
5. Quantify the business impact with real dollar figures

CRITICAL OUTPUT RULES:
- Always output valid JSON (no markdown, no explanation outside JSON)
- Dollar estimates must be specific (not ranges like "$50K-$100K")
- Time estimates must be in hours (e.g., "estimated_hours_to_failure": 18)
- Actions must be specific and ordered by priority
- Risk level: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
- Confidence: 0.0 to 1.0

COST BENCHMARKS for oil & gas industrial pumps:
- Unplanned shutdown cost: $8,000–$15,000/hour (lost production + emergency labor)
- Emergency bearing replacement: $25,000–$45,000 (parts + labor + expedited)
- Planned maintenance bearing replacement: $8,000–$12,000 (scheduled)
- Seal failure + spillage cleanup: $35,000–$80,000 (environmental + regulatory)
- Full pump replacement: $180,000–$350,000

Respond ONLY with this exact JSON schema:
{
  "risk_level": "HIGH",
  "confidence": 0.87,
  "summary": "One-sentence executive summary",
  "anomalous_sensors": [
    {
      "sensor": "vibration",
      "current_value": 6.2,
      "unit": "mm/s",
      "normal_range": "0–4.5",
      "deviation": "+38%",
      "interpretation": "What this means physically for the pump"
    }
  ],
  "root_cause_hypothesis": "Most likely physical cause",
  "estimated_hours_to_failure": 18,
  "predicted_failure_mode": "Bearing failure due to...",
  "recommended_actions": [
    {
      "priority": 1,
      "action": "Specific action to take",
      "timeline": "Within 4 hours",
      "responsible": "Maintenance team"
    }
  ],
  "cost_impact": {
    "if_no_action": {
      "estimated_downtime_hours": 48,
      "estimated_cost_usd": 520000,
      "breakdown": "Brief breakdown of costs"
    },
    "if_planned_maintenance": {
      "estimated_cost_usd": 10000,
      "scheduled_downtime_hours": 6,
      "savings_vs_failure": 510000
    }
  },
  "next_check_in_minutes": 15
}"""


# ─── Data Models ─────────────────────────────────────────────────────────────
class SensorSnapshot(BaseModel):
    timestamp: str
    machine_status: str
    health_score: float
    overall_status: str
    sensors: dict


class AnalyzeRequest(BaseModel):
    snapshot: SensorSnapshot
    raw_payload: Optional[dict] = None


# ─── WebSocket Manager ────────────────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []
        # Per-connection write lock — prevents concurrent ws.send_json() on the
        # same socket when /simulate/inject fires faster than the send completes.
        # WebSocket frames must be serialized per-connection; concurrent writes
        # corrupt the stream and can deadlock starlette's internal send machinery.
        self._locks: dict[int, asyncio.Lock] = {}

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        self._locks[id(ws)] = asyncio.Lock()

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)
        self._locks.pop(id(ws), None)

    async def broadcast(self, message: dict):
        """Send to all clients in parallel, serialized per-connection.

        asyncio.gather → all connections send at the same time (parallel).
        Per-connection lock → concurrent broadcast() calls queue up per socket
          instead of firing simultaneous writes (which corrupt WebSocket frames).
        wait_for(5s) → dead/slow clients are evicted quickly.
        """
        if not self.active:
            return

        async def _send(ws: WebSocket) -> WebSocket | None:
            lock = self._locks.get(id(ws))
            try:
                if lock:
                    async with lock:
                        await asyncio.wait_for(ws.send_json(message), timeout=5.0)
                else:
                    await asyncio.wait_for(ws.send_json(message), timeout=5.0)
                return None
            except Exception:
                return ws   # dead — caller will remove

        results = await asyncio.gather(
            *[_send(ws) for ws in list(self.active)],
            return_exceptions=True,   # never let one client abort the whole gather
        )
        for r in results:
            if isinstance(r, WebSocket):
                self.disconnect(r)


manager = ConnectionManager()

# Cache latest sensor payload for /latest endpoint
_last_sensor_payload: dict = {}

# ─── MQTT Bridge ─────────────────────────────────────────────────────────────
class MQTTBridge:
    """Subscribes to MQTT and pushes messages to WebSocket clients."""

    def __init__(self):
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="backend-bridge",
            protocol=mqtt.MQTTv311,
        )
        self.client.on_connect    = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message    = self._on_message
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._stopping           = False

    def _on_connect(self, client, userdata, connect_flags, reason_code, properties):
        if not reason_code.is_failure:
            print(f"[MQTT Bridge] ✅ Connected to {MQTT_HOST}:{MQTT_PORT}")
            client.subscribe(TOPIC_SENSORS, qos=1)
        else:
            print(f"[MQTT Bridge] ❌ Connection failed: {reason_code}")

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        if self._stopping:
            return
        print(f"[MQTT Bridge] Disconnected (rc={reason_code}), will retry...")
        threading.Thread(target=self._reconnect_loop, daemon=True).start()

    def _reconnect_loop(self):
        for delay in [2, 4, 8, 16, 30]:
            if self._stopping:
                return
            time.sleep(delay)
            try:
                self.client.reconnect()
                print(f"[MQTT Bridge] ✅ Reconnected to {MQTT_HOST}:{MQTT_PORT}")
                return
            except Exception as e:
                print(f"[MQTT Bridge] Reconnect failed ({delay}s delay): {e}")
        print(f"[MQTT Bridge] ❌ Could not reconnect after retries")

    def _on_message(self, client, userdata, msg):
        if self.loop is None:
            return
        try:
            payload = json.loads(msg.payload.decode())
            payload["type"] = "sensor_update"
            # Cache for /latest endpoint
            global _last_sensor_payload, _forced_state
            _last_sensor_payload = payload

            # If Node-RED injected data recently (within 5s), stay silent —
            # Node-RED will broadcast enriched data via /simulate/inject.
            # This prevents double-broadcast / status flickering on the dashboard.
            nodered_active = (time.time() - _nodered_last_inject) < 5.0
            if nodered_active:
                return

            # Node-RED is offline or warming up — broadcast raw MQTT as fallback.
            # Apply forced-state override so the control panel still works correctly.
            if _forced_state is not None:
                payload.update(_FORCED_STATUS_MAP[_forced_state])
            asyncio.run_coroutine_threadsafe(
                manager.broadcast(payload),
                self.loop
            )
        except Exception as e:
            print(f"[MQTT Bridge] Message error: {e}")

    def start(self, loop: asyncio.AbstractEventLoop):
        self.loop     = loop
        self._stopping = False
        if MQTT_USERNAME:
            self.client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        use_tls = MQTT_TLS or MQTT_PORT == 8883
        if use_tls:
            self.client.tls_set(tls_version=ssl.PROTOCOL_TLS_CLIENT)
        self.client.loop_start()
        # Retry initial connection (Mosquitto may not be ready yet)
        for attempt in range(8):
            try:
                self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
                print(f"[MQTT Bridge] Connecting to {MQTT_HOST}:{MQTT_PORT} (TLS={use_tls})")
                return
            except Exception as e:
                wait = 2 ** min(attempt, 4)
                print(f"[MQTT Bridge] Connect attempt {attempt+1} failed: {e} — retry in {wait}s")
                time.sleep(wait)
        print(f"[MQTT Bridge] ❌ Could not connect to {MQTT_HOST}:{MQTT_PORT} after 8 attempts")

    def stop(self):
        self._stopping = True
        self.client.loop_stop()
        self.client.disconnect()


mqtt_bridge = MQTTBridge()


# ─── Lifespan ─────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_event_loop()
    mqtt_bridge.start(loop)
    yield
    mqtt_bridge.stop()


# ─── App ─────────────────────────────────────────────────────────────────────
app = FastAPI(title="Pump IoT Demo Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Slow-request logger ──────────────────────────────────────────────────────
# Logs any request that takes longer than SLOW_THRESHOLD seconds.
# Helps identify which endpoint is blocking the event loop.
SLOW_THRESHOLD = 1.0   # seconds

@app.middleware("http")
async def log_slow_requests(request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - t0
    if elapsed > SLOW_THRESHOLD:
        print(f"[SLOW] {request.method} {request.url.path} → {elapsed:.2f}s")
    return response

# Serve dashboard static files
DASHBOARD_DIR = os.path.join(os.path.dirname(__file__), "..", "dashboard")

# Shortcut route: /control and /control/ → dashboard/control.html
@app.get("/control")
@app.get("/control/")
async def presenter_control():
    control_path = os.path.join(DASHBOARD_DIR, "control.html")
    return FileResponse(control_path)


if os.path.isdir(DASHBOARD_DIR):
    app.mount("/dashboard", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard")


# ─── AI Integration ──────────────────────────────────────────────────────────
async def call_ai(snapshot: SensorSnapshot) -> dict:
    """Call Gemini with sensor snapshot, return structured analysis."""

    user_msg = f"""Current pump sensor snapshot:

Machine Status: {snapshot.machine_status}
Health Score: {snapshot.health_score}/100
Overall Assessment: {snapshot.overall_status}
Timestamp: {snapshot.timestamp}

Sensor Readings:
{json.dumps(snapshot.sensors, indent=2)}

Analyze this data and provide your predictive maintenance assessment."""

    return await _call_groq(user_msg)


async def _call_groq(user_msg: str) -> dict:
    if not _groq_client:
        print("[AI] No GROQ_API_KEY, using mock response")
        return _mock_ai_response_generic()

    async with _ai_semaphore:   # max 2 concurrent calls
        try:
            response = await asyncio.wait_for(
                _groq_client.chat.completions.create(
                    model=AI_MODEL,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user",   "content": user_msg},
                    ],
                    response_format={"type": "json_object"},
                    max_tokens=2048,
                    temperature=0.3,
                ),
                timeout=45.0,   # give up after 45s — never block forever
            )
            text = response.choices[0].message.content
            print(f"[Groq] OK — model: {AI_MODEL}  tokens: {response.usage.total_tokens}")
            return json.loads(text)

        except asyncio.TimeoutError:
            print("[Groq] Timeout after 45s — returning mock")
            return _mock_ai_response_generic(error="Groq API timeout")
        except Exception as e:
            print(f"[Groq API] Error: {e}")
            return _mock_ai_response_generic(error=str(e))


def _mock_ai_response_generic(error: str = None) -> dict:
    """Fallback mock AI response when no API key is configured."""
    note = f" [MOCK - configure API key: {error}]" if error else " [MOCK - set API key in .env]"
    return {
        "risk_level": "HIGH",
        "confidence": 0.82,
        "summary": f"Vibration and temperature sensors indicate bearing degradation.{note}",
        "anomalous_sensors": [
            {
                "sensor": "vibration",
                "current_value": 6.2,
                "unit": "mm/s",
                "normal_range": "0–4.5",
                "deviation": "+38%",
                "interpretation": "Elevated vibration indicates mechanical imbalance or bearing wear"
            },
            {
                "sensor": "temperature",
                "current_value": 91.5,
                "unit": "°C",
                "normal_range": "60–85",
                "deviation": "+7.6%",
                "interpretation": "Overheating consistent with bearing friction increase"
            }
        ],
        "root_cause_hypothesis": "Bearing wear due to insufficient lubrication or contamination",
        "estimated_hours_to_failure": 18,
        "predicted_failure_mode": "Complete bearing seizure leading to shaft damage",
        "recommended_actions": [
            {
                "priority": 1,
                "action": "Schedule immediate bearing inspection within next 4-hour shift",
                "timeline": "Within 4 hours",
                "responsible": "Maintenance Team A"
            },
            {
                "priority": 2,
                "action": "Prepare replacement bearing kit (SKF 6310-2RS) and seal set",
                "timeline": "Within 2 hours",
                "responsible": "Procurement / Stores"
            },
            {
                "priority": 3,
                "action": "Notify operations to reduce pump load to 80% as precautionary measure",
                "timeline": "Immediately",
                "responsible": "Control Room Operator"
            }
        ],
        "cost_impact": {
            "if_no_action": {
                "estimated_downtime_hours": 48,
                "estimated_cost_usd": 524000,
                "breakdown": "$432K lost production (48h × $9K/h) + $42K emergency repair + $50K seal damage"
            },
            "if_planned_maintenance": {
                "estimated_cost_usd": 10500,
                "scheduled_downtime_hours": 6,
                "savings_vs_failure": 513500
            }
        },
        "next_check_in_minutes": 15
    }


def _mock_ai_response(snapshot: SensorSnapshot) -> dict:
    return _mock_ai_response_generic()


# ─── Routes ──────────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {
        "service": "Pump IoT Demo Backend",
        "status": "running",
        "ai_provider": "groq",
        "mqtt": f"{MQTT_HOST}:{MQTT_PORT}",
        "endpoints": {
            "analyze": "POST /analyze",
            "websocket": "ws://localhost:8000/ws",
            "health": "GET /health",
            "dashboard": "/dashboard/",
        }
    }


@app.get("/health")
async def health():
    mqtt_ok = (
        mqtt_bridge.client is not None
        and mqtt_bridge.client.is_connected()
    )
    last_status = _last_sensor_payload.get("machine_status") if _last_sensor_payload else None
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ai_provider": "groq",
        "api_key_configured": bool(GROQ_API_KEY),
        "ws_clients": len(manager.active),
        "mqtt_connected": mqtt_ok,
        "mqtt_broker": f"{MQTT_HOST}:{MQTT_PORT}",
        "has_data": bool(_last_sensor_payload),
        "last_status": last_status,
    }


@app.get("/latest")
async def latest():
    """Return the most recent sensor payload received via MQTT."""
    if not _last_sensor_payload:
        return {"status": "no_data", "message": "No sensor data received yet. Start mqtt_replay.py first."}
    return {"status": "ok", **_last_sensor_payload}


@app.get("/debug")
async def debug():
    """Snapshot of server internals — useful for diagnosing hangs."""
    all_tasks = asyncio.all_tasks()
    pending   = [str(t.get_coro()) for t in all_tasks if not t.done()]
    return {
        "ws_clients":          len(manager.active),
        "forced_state":        _forced_state,
        "nodered_last_inject": round(time.time() - _nodered_last_inject, 1),
        "ai_semaphore_value":  _ai_semaphore._value,
        "ai_semaphore_waiters": len(_ai_semaphore._waiters) if hasattr(_ai_semaphore, '_waiters') else "?",
        "pending_tasks":       len(pending),
        "pending_task_names":  pending[:20],   # first 20 only
        "last_alert_ago_s":    round(time.time() - _last_alert_time, 1),
        "nodered_active":      (time.time() - _nodered_last_inject) < 5.0,
    }


@app.post("/analyze")
async def analyze(req: AnalyzeRequest):
    """Receive sensor snapshot from Node-RED (or dashboard), call AI, return analysis."""
    result = await call_ai(req.snapshot)
    result["analyzed_at"] = datetime.now(timezone.utc).isoformat()
    # Broadcast to all WebSocket clients
    await manager.broadcast({"type": "ai_recommendation", **result})
    return result


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    print(f"[WS] Client connected. Total: {len(manager.active)}")
    try:
        while True:
            # Keep connection alive, receive control messages from dashboard
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "request_analysis":
                    # Dashboard manually requested AI analysis
                    snapshot = SensorSnapshot(**msg["snapshot"])
                    result = await call_ai(snapshot)
                    result["type"] = "ai_recommendation"
                    result["triggered_by"] = "manual"
                    await manager.broadcast(result)
            except Exception:
                pass
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        print(f"[WS] Client disconnected. Total: {len(manager.active)}")


@app.post("/simulate/inject")
async def inject_sensor_data(payload: dict):
    """Inject sensor data directly (for simulation without MQTT).

    If a presenter forced-state is active (set via /control/{state}),
    overall_status and machine_status are overridden so the dashboard
    cannot flicker back to a different state while the demo is locked.
    Sensor gauge values (vibration, temperature, …) are left untouched
    so the live readings still animate normally.
    """
    global _forced_state, _nodered_last_inject
    _nodered_last_inject = time.time()   # tell MQTT bridge Node-RED is alive
    payload["type"] = "sensor_update"
    if _forced_state is not None:
        payload.update(_FORCED_STATUS_MAP[_forced_state])
    await manager.broadcast(payload)
    return {"status": "injected", "clients": len(manager.active)}


# ─── Presenter Control ────────────────────────────────────────────────────────
VALID_STATES = {"normal", "warning", "critical"}

# Forced-state lock: set by /control/{state} so that Node-RED's /simulate/inject
# cannot override overall_status while a presenter state is active.
_forced_state: str | None = None   # None = free-running, else "normal"/"warning"/"critical"

# Tracks when Node-RED last called /simulate/inject.
# Used by the MQTT bridge to suppress its own raw broadcast while Node-RED is active
# (prevents double-publish and status flickering on the dashboard).
_nodered_last_inject: float = 0.0

_FORCED_STATUS_MAP = {
    "normal":   {"overall_status": "NORMAL",   "machine_status": "NORMAL",  "anomaly_detected": False},
    "warning":  {"overall_status": "WARNING",  "machine_status": "BROKEN",  "anomaly_detected": True},
    "critical": {"overall_status": "CRITICAL", "machine_status": "BROKEN",  "anomaly_detected": True},
}

@app.post("/control/{state}")
async def control_state(state: str):
    """
    Trigger a state change on all connected dashboards.
    Called by presenter from phone or terminal — not visible on main screen.

    Usage:
        curl -X POST http://localhost:8000/control/warning
        curl -X POST http://localhost:8000/control/critical
        curl -X POST http://localhost:8000/control/normal
    """
    global _forced_state
    state = state.lower()
    if state not in VALID_STATES:
        return {"status": "error", "reason": f"Invalid state. Use: {VALID_STATES}"}

    # Lock / unlock the forced-state so Node-RED inject cannot flicker the status
    _forced_state = None if state == "normal" else state
    print(f"[Control] Forced state → {_forced_state or 'FREE (normal)'}")

    await manager.broadcast({
        "type":    "state_command",
        "state":   state,
        "sent_at": datetime.now(timezone.utc).isoformat(),
    })
    print(f"[Control] → {state.upper()} broadcast to {len(manager.active)} client(s)")

    # Fire email alert when presenter manually triggers warning or critical
    if state in ("warning", "critical"):
        level = "CRITICAL" if state == "critical" else "WARNING"
        # Synthetic sensor readings that match the simulated state
        if state == "critical":
            sensor_vals = {"vibration": 7.8, "temperature": 96.5, "pressure": 10.2, "flow_rate": 88.0}
            health_score = 18.0
            hours = 6
            savings = 513500
        else:
            sensor_vals = {"vibration": 5.2, "temperature": 87.0, "pressure": 8.6, "flow_rate": 112.0}
            health_score = 48.0
            hours = 18
            savings = 513500

        sensor_summary, sensor_statuses = _build_sensor_summary(sensor_vals)
        alert_req = AlertRequest(
            level=level,
            sensor_summary=sensor_summary,
            sensor_statuses=sensor_statuses,
            health_score=health_score,
            message=f"Operator manually triggered {level} state via Control Panel. Immediate inspection recommended.",
            ai_risk_level=level,
            estimated_hours_to_failure=hours,
            estimated_savings=savings,
        )
        print(f"[Control] Firing email alert for {level} state...")

        async def _fire_control_alert(req: AlertRequest, lvl: str):
            try:
                result = await _do_send_alert(req)
                print(f"[Control] Email result for {lvl}: {result}")
            except Exception as exc:
                print(f"[Control] Email error for {lvl}: {exc}")

        asyncio.create_task(_fire_control_alert(alert_req, level))

    return {"status": "ok", "state": state, "clients": len(manager.active)}


@app.post("/control/ai/trigger")
async def control_trigger_ai():
    """Trigger AI analysis on all connected dashboards."""
    await manager.broadcast({
        "type":    "state_command",
        "state":   "trigger_ai",
        "sent_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"status": "ok", "action": "trigger_ai", "clients": len(manager.active)}


@app.get("/alert/test")
async def test_alert():
    """
    Quick diagnostic — check email config and send a test alert.
    Open: http://localhost:8000/alert/test
    """
    diag = {
        "RESEND_API_KEY": "✅ set" if RESEND_API_KEY else "❌ NOT SET",
        "ALERT_TO":       ALERT_TO if ALERT_TO else "❌ NOT SET",
        "ALERT_FROM":     ALERT_FROM,
        "cooldown_remaining": max(0, int(_ALERT_COOLDOWN_SEC - (time.time() - _last_alert_time))),
    }

    if not RESEND_API_KEY or not ALERT_TO:
        return {"status": "cannot_send", "config": diag}

    sensor_vals = {"vibration": 7.8, "temperature": 96.5, "pressure": 10.2, "flow_rate": 88.0}
    sensor_summary, sensor_statuses = _build_sensor_summary(sensor_vals)
    req = AlertRequest(
        level="CRITICAL",
        sensor_summary=sensor_summary,
        sensor_statuses=sensor_statuses,
        health_score=18.0,
        message="Test alert from /alert/test endpoint.",
        ai_risk_level="CRITICAL",
        estimated_hours_to_failure=6,
        estimated_savings=513500,
    )
    result = await _do_send_alert(req)
    return {"config": diag, "send_result": result}


# ─── Email Alert ─────────────────────────────────────────────────────────────

# Sensor thresholds (must match SENSOR_GAUGE_CFG in the dashboard)
_SENSOR_THRESHOLDS = {
    "vibration":   {"warn": 4.5,  "crit": 7.0,  "unit": "mm/s", "invert": False},
    "temperature": {"warn": 85.0, "crit": 95.0,  "unit": "°C",   "invert": False},
    "pressure":    {"warn": 8.5,  "crit": 10.0,  "unit": "bar",  "invert": False},
    "flow_rate":   {"warn": 110.0,"crit": 90.0,  "unit": "m³/h", "invert": True},
}

def _sensor_status(name: str, val: float) -> str:
    t = _SENSOR_THRESHOLDS.get(name)
    if not t:
        return "NORMAL"
    if t["invert"]:
        if val <= t["crit"]: return "CRITICAL"
        if val <= t["warn"]: return "WARNING"
    else:
        if val >= t["crit"]: return "CRITICAL"
        if val >= t["warn"]: return "WARNING"
    return "NORMAL"

def _build_sensor_summary(sensors: dict):
    """Return (sensor_summary, sensor_statuses) from raw sensor values."""
    summary  = {}
    statuses = {}
    for name, val in sensors.items():
        t = _SENSOR_THRESHOLDS.get(name, {})
        unit = t.get("unit", "")
        decimals = 1 if name == "temperature" else 2 if name in ("vibration", "pressure") else 1
        summary[name.replace("_", " ").title()] = f"{val:.{decimals}f} {unit}".strip()
        statuses[name.replace("_", " ").title()] = _sensor_status(name, val)
    return summary, statuses


class AlertRequest(BaseModel):
    level: str                          # "WARNING" | "CRITICAL"
    sensor_summary: dict                # {"Vibration": "7.2 mm/s", ...}
    sensor_statuses: Optional[dict] = None  # {"Vibration": "CRITICAL", ...}
    health_score: float
    message: str
    ai_risk_level: Optional[str] = None
    estimated_hours_to_failure: Optional[int] = None
    estimated_savings: Optional[int] = None


_EMAIL_TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "email_alert.html")
# Cache template in memory — avoids synchronous file I/O on every alert.
# Loaded lazily on first use; reload by restarting the server.
_EMAIL_TEMPLATE_CACHE: str | None = None

def _build_email_html(req: AlertRequest) -> str:
    # Re-read .env so PUBLIC_URL is always fresh (no restart needed after tunnel is set)
    try:
        from dotenv import dotenv_values
        _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        _pub = dotenv_values(_env_path).get("PUBLIC_URL", "")
    except Exception:
        _pub = ""
    dashboard_url = (_pub or os.getenv("PUBLIC_URL", "http://localhost:8000")).rstrip("/") + "/dashboard/"

    is_critical  = req.level == "CRITICAL"
    level_color  = "#ef4444" if is_critical else "#f59e0b"
    level_bg     = "#1a0a0a" if is_critical else "#1a1400"
    icon         = "🔴"      if is_critical else "⚠️"

    # Build sensor rows
    _badge_html = {
        "CRITICAL": '<span style="background:#ef4444;color:#fff;font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;margin-left:8px">CRITICAL</span>',
        "WARNING":  '<span style="background:#f59e0b;color:#000;font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;margin-left:8px">WARNING</span>',
        "NORMAL":   '<span style="background:#10b981;color:#fff;font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;margin-left:8px">NORMAL</span>',
    }
    def _row_bg(k):
        st = (req.sensor_statuses or {}).get(k, "NORMAL")
        return "#1a0a0a" if st == "CRITICAL" else "#1a1000" if st == "WARNING" else "transparent"

    sensor_rows = "".join(
        f'<tr style="background:{_row_bg(k)}">'
        f'<td style="padding:8px 12px;color:#9ca3af;font-size:13px">{k}</td>'
        f'<td style="padding:8px 12px;color:#f9fafb;font-size:13px;font-weight:600">{v}'
        f'{_badge_html.get((req.sensor_statuses or {}).get(k, "NORMAL"), "")}</td></tr>'
        for k, v in req.sensor_summary.items()
    )

    # Build optional AI block
    if req.ai_risk_level:
        savings_str = f"${req.estimated_savings:,}" if req.estimated_savings else "–"
        hours_str   = f"{req.estimated_hours_to_failure}h" if req.estimated_hours_to_failure else "–"
        ai_block = (
            '<div style="background:#111827;border:1px solid #374151;border-radius:8px;padding:16px;margin-top:16px">'
            '<div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:.08em;margin-bottom:10px">AI Analysis</div>'
            '<div style="display:flex;gap:24px;flex-wrap:wrap">'
            f'<div><div style="font-size:11px;color:#6b7280">Risk Level</div><div style="font-size:20px;font-weight:700;color:{level_color}">{req.ai_risk_level}</div></div>'
            f'<div><div style="font-size:11px;color:#6b7280">Est. Time to Failure</div><div style="font-size:20px;font-weight:700;color:#f9fafb">{hours_str}</div></div>'
            f'<div><div style="font-size:11px;color:#6b7280">Potential Savings</div><div style="font-size:20px;font-weight:700;color:#10b981">{savings_str}</div></div>'
            '</div></div>'
        )
    else:
        ai_block = ""

    # Load template from cache (avoid blocking file I/O in async path)
    global _EMAIL_TEMPLATE_CACHE
    if _EMAIL_TEMPLATE_CACHE is None:
        _EMAIL_TEMPLATE_CACHE = open(_EMAIL_TEMPLATE_PATH, encoding="utf-8").read()
    template = _EMAIL_TEMPLATE_CACHE
    placeholders = {
        "{{level_bg}}":     level_bg,
        "{{level_color}}":  level_color,
        "{{icon}}":         icon,
        "{{level}}":        req.level,
        "{{message}}":      req.message,
        "{{health_score}}": str(int(req.health_score)),
        "{{sensor_rows}}":  sensor_rows,
        "{{ai_block}}":     ai_block,
        "{{dashboard_url}}": dashboard_url,
        "{{timestamp}}":    datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }
    for key, val in placeholders.items():
        template = template.replace(key, val)
    return template


# Cooldown: prevent sending too many emails in a short period
_last_alert_time: float = 0.0
_ALERT_COOLDOWN_SEC = 60.0


async def _do_send_alert(req: AlertRequest) -> dict:
    """Core email-sending logic — callable from both the HTTP route and internal code."""
    global _last_alert_time

    if not RESEND_API_KEY:
        return {"status": "skipped", "reason": "RESEND_API_KEY not configured"}
    if not ALERT_TO:
        return {"status": "skipped", "reason": "ALERT_TO not configured"}

    now = time.time()
    if now - _last_alert_time < _ALERT_COOLDOWN_SEC:
        remaining = int(_ALERT_COOLDOWN_SEC - (now - _last_alert_time))
        return {"status": "skipped", "reason": f"cooldown active ({remaining}s remaining)"}

    try:
        import resend
        resend.api_key = RESEND_API_KEY

        subject = (
            "🔴 CRITICAL — Pump Failure Risk Detected"
            if req.level == "CRITICAL"
            else "⚠️ WARNING — Pump Anomaly Detected"
        )

        # resend.Emails.send is synchronous (blocking HTTP) — run in a thread
        # so it never blocks the asyncio event loop / WebSocket broadcasts.
        # Hard 15 s timeout: if Resend API is unreachable the thread would
        # otherwise hang indefinitely, filling the thread pool over time.
        html_body = _build_email_html(req)
        await asyncio.wait_for(
            asyncio.to_thread(
                resend.Emails.send,
                {
                    "from":    ALERT_FROM,
                    "to":      ALERT_TO,
                    "subject": subject,
                    "html":    html_body,
                },
            ),
            timeout=15.0,
        )

        _last_alert_time = now
        print(f"[Alert] Email sent → {ALERT_TO} ({req.level})")
        return {"status": "sent", "to": ALERT_TO, "level": req.level}

    except Exception as e:
        print(f"[Alert] Email error: {e}")
        return {"status": "error", "reason": str(e)}


@app.post("/alert")
async def send_alert(req: AlertRequest):
    return await _do_send_alert(req)
