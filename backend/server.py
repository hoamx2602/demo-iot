"""
PumpGuard AI — Backend Server
- FastAPI + WebSocket
- Subscribe MQTT → broadcast sensor data to dashboard via WebSocket
- POST /analyze  → Claude API → returns business-friendly JSON
- POST /alert    → Resend email alert on anomaly detection

Run:
    uvicorn backend.server:app --host 0.0.0.0 --port 8000 --reload

Environment variables (set in .env):
    AI_PROVIDER=claude|openai
    ANTHROPIC_API_KEY=sk-ant-...
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
AI_PROVIDER       = os.getenv("AI_PROVIDER", "claude").lower()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "")
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY", "")
MQTT_HOST         = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT         = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME     = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD     = os.getenv("MQTT_PASSWORD", "")
MQTT_TLS          = os.getenv("MQTT_TLS", "false").lower() in ("true", "1", "yes")
TOPIC_SENSORS     = "pump/sensors"

RESEND_API_KEY    = os.getenv("RESEND_API_KEY", "")
ALERT_FROM        = os.getenv("ALERT_FROM", "PumpGuard AI <onboarding@resend.dev>")
ALERT_TO          = [e.strip() for e in os.getenv("ALERT_TO", "").split(",") if e.strip()]

# AI Model defaults per provider
_MODEL_DEFAULTS = {
    "claude": "claude-sonnet-4-6",
    "openai": "gpt-4o",
    "gemini": "gemini-1.5-flash",   # free-tier friendly
}
AI_MODEL = os.getenv("AI_MODEL", _MODEL_DEFAULTS.get(AI_PROVIDER, "gemini-1.5-flash"))

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

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


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
        self._last_analysis_time = 0.0
        self._analysis_cooldown  = 10.0
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
            global _last_sensor_payload
            _last_sensor_payload = payload
            asyncio.run_coroutine_threadsafe(
                manager.broadcast(payload),
                self.loop
            )

            # Auto-trigger AI analysis on anomaly (with cooldown)
            _status = payload.get("overall_status") or payload.get("machine_status", "NORMAL")
            if payload.get("anomaly_detected") or _status in ("WARNING", "CRITICAL", "BROKEN"):
                now = time.time()
                if now - self._last_analysis_time > self._analysis_cooldown:
                    self._last_analysis_time = now
                    asyncio.run_coroutine_threadsafe(
                        self._auto_analyze(payload),
                        self.loop
                    )
        except Exception as e:
            print(f"[MQTT Bridge] Message error: {e}")

    async def _auto_analyze(self, payload: dict):
        try:
            machine_status = payload.get("machine_status", "NORMAL")
            health_score   = payload.get("health_score", 50.0)
            sensors        = payload.get("trends", payload.get("sensors", {}))

            snapshot = SensorSnapshot(
                timestamp=payload.get("ts", datetime.now(timezone.utc).isoformat()),
                machine_status=machine_status,
                health_score=health_score,
                overall_status=payload.get("overall_status", machine_status),
                sensors=sensors,
            )
            result = await call_ai(snapshot)
            result["type"] = "ai_recommendation"
            result["triggered_by"] = "auto"
            await manager.broadcast(result)

            # Auto-send email alert (server-side, no need for dashboard to be open)
            alert_level = "CRITICAL" if machine_status in ("BROKEN", "CRITICAL") else "WARNING"
            sensor_summary, sensor_statuses = _build_sensor_summary(sensors)
            alert_req = AlertRequest(
                level=alert_level,
                sensor_summary=sensor_summary,
                sensor_statuses=sensor_statuses,
                health_score=health_score,
                message=(
                    "Pump failure detected — emergency maintenance required."
                    if machine_status == "BROKEN"
                    else "Sensor readings outside normal bounds."
                ),
                ai_risk_level=result.get("risk_level"),
                estimated_hours_to_failure=result.get("estimated_hours_to_failure"),
                estimated_savings=result.get("estimated_savings"),
            )
            await send_alert(alert_req)
            print(f"[Auto-Analysis] Alert dispatched: {alert_level}")
        except Exception as e:
            print(f"[Auto-Analysis] Error: {e}")

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
    """Call Claude or OpenAI with sensor snapshot, return structured analysis."""

    user_msg = f"""Current pump sensor snapshot:

Machine Status: {snapshot.machine_status}
Health Score: {snapshot.health_score}/100
Overall Assessment: {snapshot.overall_status}
Timestamp: {snapshot.timestamp}

Sensor Readings:
{json.dumps(snapshot.sensors, indent=2)}

Analyze this data and provide your predictive maintenance assessment."""

    if AI_PROVIDER == "claude":
        return await _call_claude(user_msg)
    elif AI_PROVIDER == "openai":
        return await _call_openai(user_msg)
    elif AI_PROVIDER == "gemini":
        return await _call_gemini(user_msg)
    else:
        return _mock_ai_response(snapshot)


async def _call_claude(user_msg: str) -> dict:
    if not ANTHROPIC_API_KEY:
        print("[AI] No ANTHROPIC_API_KEY, using mock response")
        return _mock_ai_response_generic()

    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model=AI_MODEL,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = response.content[0].text.strip()
        # Strip markdown if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception as e:
        print(f"[Claude API] Error: {e}")
        return _mock_ai_response_generic(error=str(e))


async def _call_openai(user_msg: str) -> dict:
    if not OPENAI_API_KEY:
        print("[AI] No OPENAI_API_KEY, using mock response")
        return _mock_ai_response_generic()

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        response = await client.chat.completions.create(
            model=AI_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=1500,
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"[OpenAI API] Error: {e}")
        return _mock_ai_response_generic(error=str(e))


async def _call_gemini(user_msg: str) -> dict:
    if not GEMINI_API_KEY:
        print("[AI] No GEMINI_API_KEY, using mock response")
        return _mock_ai_response_generic()

    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(
            model_name=AI_MODEL,
            system_instruction=SYSTEM_PROMPT,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                max_output_tokens=1500,
            ),
        )
        response = await asyncio.to_thread(
            model.generate_content, user_msg
        )
        text = response.text.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception as e:
        print(f"[Gemini API] Error: {e}")
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
        "ai_provider": AI_PROVIDER,
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
        "ai_provider": AI_PROVIDER,
        "api_key_configured": bool(ANTHROPIC_API_KEY or OPENAI_API_KEY or GEMINI_API_KEY),
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
    """Inject sensor data directly (for simulation without MQTT)."""
    payload["type"] = "sensor_update"
    await manager.broadcast(payload)
    return {"status": "injected", "clients": len(manager.active)}


# ─── Presenter Control ────────────────────────────────────────────────────────
VALID_STATES = {"normal", "warning", "critical"}

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
    state = state.lower()
    if state not in VALID_STATES:
        return {"status": "error", "reason": f"Invalid state. Use: {VALID_STATES}"}

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
    "vibration":   {"warn": 4.5,  "crit": 6.5,  "unit": "mm/s", "invert": False},
    "temperature": {"warn": 85.0, "crit": 95.0,  "unit": "°C",   "invert": False},
    "pressure":    {"warn": 8.0,  "crit": 10.0,  "unit": "bar",  "invert": False},
    "flow_rate":   {"warn": 120.0,"crit": 100.0, "unit": "m³/h", "invert": True},
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


def _build_email_html(req: AlertRequest) -> str:
    # Read PUBLIC_URL on every call (may be set after server has already started)
    _public_url = os.getenv("PUBLIC_URL", "http://localhost:8000").rstrip("/")
    _dashboard_url = _public_url + "/dashboard/"

    level_color = "#ef4444" if req.level == "CRITICAL" else "#f59e0b"
    level_bg    = "#1a0a0a" if req.level == "CRITICAL" else "#1a1400"
    icon        = "🔴" if req.level == "CRITICAL" else "⚠️"

    _status_badge = {
        "CRITICAL": '<span style="background:#ef4444;color:#fff;font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;margin-left:8px">CRITICAL</span>',
        "WARNING":  '<span style="background:#f59e0b;color:#000;font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;margin-left:8px">WARNING</span>',
        "NORMAL":   '<span style="background:#10b981;color:#fff;font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;margin-left:8px">NORMAL</span>',
    }

    def _row_color(k):
        st = (req.sensor_statuses or {}).get(k, "NORMAL")
        return "#1a0a0a" if st == "CRITICAL" else "#1a1000" if st == "WARNING" else "transparent"

    sensor_rows = "".join(
        f'<tr style="background:{_row_color(k)}">'
        f'<td style="padding:8px 12px;color:#9ca3af;font-size:13px">{k}</td>'
        f'<td style="padding:8px 12px;color:#f9fafb;font-size:13px;font-weight:600">{v}'
        f'{_status_badge.get((req.sensor_statuses or {}).get(k,"NORMAL"),"")}</td></tr>'
        for k, v in req.sensor_summary.items()
    )

    ai_block = ""
    if req.ai_risk_level:
        savings_str = f"${req.estimated_savings:,}" if req.estimated_savings else "–"
        hours_str   = f"{req.estimated_hours_to_failure}h" if req.estimated_hours_to_failure else "–"
        ai_block = f"""
        <div style="background:#111827;border:1px solid #374151;border-radius:8px;padding:16px;margin-top:16px">
          <div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:.08em;margin-bottom:10px">AI Analysis</div>
          <div style="display:flex;gap:24px;flex-wrap:wrap">
            <div><div style="font-size:11px;color:#6b7280">Risk Level</div>
              <div style="font-size:20px;font-weight:700;color:{level_color}">{req.ai_risk_level}</div></div>
            <div><div style="font-size:11px;color:#6b7280">Est. Time to Failure</div>
              <div style="font-size:20px;font-weight:700;color:#f9fafb">{hours_str}</div></div>
            <div><div style="font-size:11px;color:#6b7280">Potential Savings</div>
              <div style="font-size:20px;font-weight:700;color:#10b981">{savings_str}</div></div>
          </div>
        </div>"""

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    return f"""
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#0a0e1a;font-family:system-ui,sans-serif">
<div style="max-width:560px;margin:0 auto;padding:24px">

  <!-- Header -->
  <div style="background:{level_bg};border:1px solid {level_color};border-radius:12px;padding:20px 24px;margin-bottom:16px">
    <div style="font-size:11px;color:{level_color};text-transform:uppercase;letter-spacing:.1em;margin-bottom:6px">
      PumpGuard AI · Alert
    </div>
    <div style="font-size:22px;font-weight:700;color:#f9fafb">{icon} {req.level} — Pump Anomaly Detected</div>
    <div style="font-size:14px;color:#9ca3af;margin-top:6px">{req.message}</div>
  </div>

  <!-- Health Score -->
  <div style="background:#111827;border:1px solid #374151;border-radius:8px;padding:16px 24px;margin-bottom:16px;display:flex;align-items:center;gap:16px">
    <div style="font-size:36px;font-weight:800;color:{level_color}">{int(req.health_score)}%</div>
    <div>
      <div style="font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:.06em">Machine Health Score</div>
      <div style="height:6px;background:#1f2937;border-radius:3px;margin-top:8px;width:200px">
        <div style="height:100%;border-radius:3px;background:{level_color};width:{int(req.health_score)}%"></div>
      </div>
    </div>
  </div>

  <!-- Sensor readings -->
  <div style="background:#111827;border:1px solid #374151;border-radius:8px;overflow:hidden;margin-bottom:16px">
    <div style="padding:12px 16px;background:#1f2937;font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:.08em">
      Sensor Readings
    </div>
    <table style="width:100%;border-collapse:collapse">
      {sensor_rows}
    </table>
  </div>

  {ai_block}

  <!-- CTA -->
  <div style="text-align:center;margin-top:20px">
    <a href="{_dashboard_url}"
       style="display:inline-block;background:#06b6d4;color:#000;font-weight:700;font-size:13px;
              padding:10px 24px;border-radius:6px;text-decoration:none">
      Open Dashboard →
    </a>
  </div>

  <div style="text-align:center;font-size:11px;color:#374151;margin-top:20px">{ts} · PumpGuard AI</div>
</div>
</body>
</html>"""


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

        resend.Emails.send({
            "from":    ALERT_FROM,
            "to":      ALERT_TO,
            "subject": subject,
            "html":    _build_email_html(req),
        })

        _last_alert_time = now
        print(f"[Alert] Email sent → {ALERT_TO} ({req.level})")
        return {"status": "sent", "to": ALERT_TO, "level": req.level}

    except Exception as e:
        print(f"[Alert] Email error: {e}")
        return {"status": "error", "reason": str(e)}


@app.post("/alert")
async def send_alert(req: AlertRequest):
    return await _do_send_alert(req)
