"""
PumpGuard AI — Backend Server
- FastAPI + WebSocket
- Subscribe MQTT → broadcast đến dashboard qua WebSocket
- POST /analyze  → Claude API → trả JSON business-friendly
- POST /alert    → Resend email alert khi phát hiện anomaly

Run:
    uvicorn backend.server:app --host 0.0.0.0 --port 8000 --reload

Environment variables (đặt trong .env):
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

# ─── MQTT Bridge ─────────────────────────────────────────────────────────────
class MQTTBridge:
    """Subscribes to MQTT and pushes messages to WebSocket clients."""

    def __init__(self):
        self.client = mqtt.Client(client_id="backend-bridge", protocol=mqtt.MQTTv311)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._last_analysis_time = 0.0
        self._analysis_cooldown = 10.0  # seconds between AI calls

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print(f"[MQTT Bridge] Connected to {MQTT_HOST}:{MQTT_PORT}")
            client.subscribe(TOPIC_SENSORS, qos=1)
        else:
            print(f"[MQTT Bridge] Connection failed: rc={rc}")

    def _on_message(self, client, userdata, msg):
        if self.loop is None:
            return
        try:
            payload = json.loads(msg.payload.decode())
            payload["type"] = "sensor_update"
            asyncio.run_coroutine_threadsafe(
                manager.broadcast(payload),
                self.loop
            )

            # Auto-trigger AI analysis on anomaly (with cooldown)
            if payload.get("anomaly_detected") or payload.get("overall_status") in ("WARNING", "CRITICAL"):
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
            snapshot = SensorSnapshot(
                timestamp=payload.get("ts", datetime.now(timezone.utc).isoformat()),
                machine_status=payload.get("machine_status", "UNKNOWN"),
                health_score=payload.get("health_score", 50.0),
                overall_status=payload.get("overall_status", "NORMAL"),
                sensors=payload.get("trends", payload.get("sensors", {})),
            )
            result = await call_ai(snapshot)
            result["type"] = "ai_recommendation"
            result["triggered_by"] = "auto"
            await manager.broadcast(result)
        except Exception as e:
            print(f"[Auto-Analysis] Error: {e}")

    def start(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        try:
            self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            self.client.loop_start()
        except Exception as e:
            print(f"[MQTT Bridge] Warning: Could not connect: {e}")
            print("  Dashboard will work, but won't receive live MQTT data.")

    def stop(self):
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
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ai_provider": AI_PROVIDER,
        "api_key_configured": bool(ANTHROPIC_API_KEY or OPENAI_API_KEY),
        "ws_clients": len(manager.active),
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


# ─── Email Alert ─────────────────────────────────────────────────────────────
class AlertRequest(BaseModel):
    level: str                  # "WARNING" | "CRITICAL"
    sensor_summary: dict        # {vibration: 5.8, temperature: 88, ...}
    health_score: float
    message: str
    ai_risk_level: Optional[str] = None
    estimated_hours_to_failure: Optional[int] = None
    estimated_savings: Optional[int] = None


def _build_email_html(req: AlertRequest) -> str:
    # Đọc PUBLIC_URL mỗi lần gọi (có thể được set sau khi server khởi động)
    _public_url = os.getenv("PUBLIC_URL", "http://localhost:8000").rstrip("/")
    _dashboard_url = _public_url + "/dashboard/"

    level_color = "#ef4444" if req.level == "CRITICAL" else "#f59e0b"
    level_bg    = "#1a0a0a" if req.level == "CRITICAL" else "#1a1400"
    icon        = "🔴" if req.level == "CRITICAL" else "⚠️"

    sensor_rows = "".join(
        f'<tr><td style="padding:6px 12px;color:#9ca3af;font-size:13px">{k.replace("_"," ").title()}</td>'
        f'<td style="padding:6px 12px;color:#f9fafb;font-size:13px;font-weight:600">{v}</td></tr>'
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


# Cooldown: tránh gửi quá nhiều mail
_last_alert_time: float = 0.0
_ALERT_COOLDOWN_SEC = 60.0


@app.post("/alert")
async def send_alert(req: AlertRequest):
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
            f"🔴 CRITICAL — Pump Failure Risk Detected"
            if req.level == "CRITICAL"
            else f"⚠️ WARNING — Pump Anomaly Detected"
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
