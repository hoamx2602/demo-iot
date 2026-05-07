"""
Chạy script này để tạo PumpGuard_Workshop.ipynb
    python notebooks/generate_workshop.py
"""
import json, os

def md(source): return {"cell_type":"markdown","metadata":{},"source":[source]}
def code(source): return {"cell_type":"code","metadata":{},"source":[source],"execution_count":None,"outputs":[]}

cells = []

# ─── TITLE ───────────────────────────────────────────────────────────────────
cells.append(md("""# 🏭 PumpGuard AI — Workshop: Build từng bước trên Google Colab

> Bạn sẽ **tự xây dựng** hệ thống IoT giám sát máy bơm công nghiệp từ đầu.  
> Không cần cài gì trên máy — tất cả chạy trên cloud.

## Kiến trúc hệ thống

```
[CSV Data] → [MQTT Replay Script]
                    ↓  publish  (topic: pump/sensors)
              [Mosquitto Broker]  :1883
                    ↓  subscribe
              [FastAPI Backend]   :8000
                    ↓  WebSocket
              [Dashboard HTML]  (browser)
                    ↓  POST /analyze
              [Gemini AI API]  → Kết quả phân tích
```

## Lộ trình Workshop

| Module | Nội dung | Thời gian |
|--------|----------|-----------|
| 0 | Cài đặt môi trường | 10 phút |
| 1 | MQTT Broker | 20 phút |
| 2 | FastAPI Backend | 40 phút |
| 3 | AI Integration | 15 phút |
| 4 | Dashboard | 10 phút |
| 5 | Kết nối & Demo | 15 phút |
| 6 | Public URL (Cloudflare) | 10 phút |
"""))

# ─── MODULE 0: SETUP ─────────────────────────────────────────────────────────
cells.append(md("## Module 0 — Cài đặt môi trường\n\nTạo cấu trúc thư mục và cài các package cần thiết."))

cells.append(code("""\
import os, subprocess, sys

# Tạo cấu trúc thư mục
for d in ['/content/pumpguard/backend', '/content/pumpguard/dashboard']:
    os.makedirs(d, exist_ok=True)

os.chdir('/content/pumpguard')
print("📁 Cấu trúc thư mục:")
print("  /content/pumpguard/")
print("    ├── backend/   ← Python server")
print("    └── dashboard/ ← HTML frontend")
"""))

cells.append(code("""\
# Cài tất cả Python packages cần dùng
packages = [
    "fastapi>=0.111.0",
    "uvicorn[standard]>=0.29.0",
    "paho-mqtt>=1.6.1",
    "google-generativeai>=0.7.0",
    "python-dotenv>=1.0.0",
    "httpx>=0.27.0",
]
print("📦 Cài packages...")
subprocess.run([sys.executable, "-m", "pip", "install", "-q"] + packages, check=True)
print("✅ Xong!")
"""))

# ─── MODULE 1: MQTT ───────────────────────────────────────────────────────────
cells.append(md("""## Module 1 — MQTT Broker

### MQTT là gì?
**MQTT** (Message Queuing Telemetry Transport) là giao thức nhắn tin nhẹ dành cho IoT.

```
Publisher (sensor) → [Broker] → Subscriber (backend)
      publish("pump/sensors", data)    subscribe("pump/sensors")
```

- **Broker**: server trung gian (Mosquitto)
- **Topic**: "kênh" để phân loại message. VD: `pump/sensors`, `pump/alerts`
- **QoS 1**: đảm bảo message được giao đúng 1 lần
"""))

cells.append(code("""\
# Bước 1.1: Cài và cấu hình Mosquitto broker
import subprocess
subprocess.run(["apt-get", "install", "-y", "-q", "mosquitto"], check=True, capture_output=True)

# Tạo config: allow anonymous (không cần auth cho demo)
with open("/tmp/mosquitto.conf", "w") as f:
    f.write("listener 1883\\nallow_anonymous true\\n")

print("✅ Mosquitto đã cài xong")
"""))

cells.append(code("""\
# Bước 1.2: Khởi động broker
import subprocess, time

subprocess.run(["pkill", "-f", "mosquitto"], capture_output=True)
time.sleep(0.5)

proc = subprocess.Popen(
    ["mosquitto", "-c", "/tmp/mosquitto.conf"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)
time.sleep(1)

if proc.poll() is None:
    print(f"✅ Mosquitto running (PID {proc.pid}) — port 1883")
else:
    print("❌ Lỗi khởi động Mosquitto")
"""))

cells.append(code("""\
# Bước 1.3: Test MQTT publish/subscribe
# Chạy cell này để xem MQTT hoạt động như thế nào
import paho.mqtt.client as mqtt, time, json

received = []

def on_message(client, userdata, msg):
    data = json.loads(msg.payload.decode())
    received.append(data)
    print(f"📨 Nhận được: {data}")

# Subscriber
sub = mqtt.Client(client_id="test-sub", protocol=mqtt.MQTTv311)
sub.on_message = on_message
sub.connect("localhost", 1883)
sub.subscribe("pump/test", qos=1)
sub.loop_start()
time.sleep(0.5)

# Publisher
pub = mqtt.Client(client_id="test-pub", protocol=mqtt.MQTTv311)
pub.connect("localhost", 1883)
test_data = {"sensor": "vibration", "value": 3.2, "unit": "mm/s"}
pub.publish("pump/test", json.dumps(test_data), qos=1)
time.sleep(0.5)
pub.disconnect()
sub.loop_stop()
sub.disconnect()

print(f"\\n✅ MQTT hoạt động! Đã nhận {len(received)} message")
"""))

# ─── MODULE 2: FASTAPI BACKEND ────────────────────────────────────────────────
cells.append(md("""## Module 2 — FastAPI Backend

### Kiến trúc Backend

```
FastAPI App
├── GET  /health     → kiểm tra server
├── GET  /dashboard/ → serve HTML  
├── WS   /ws         → WebSocket (real-time data → browser)
└── POST /analyze    → gửi data lên AI, trả kết quả
```

Server sẽ:
1. **Subscribe MQTT** để nhận data từ sensor  
2. **Broadcast qua WebSocket** đến tất cả browser đang mở  
3. **Gọi AI** khi có anomaly hoặc user yêu cầu
"""))

cells.append(code("""\
%%writefile /content/pumpguard/backend/requirements.txt
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
paho-mqtt>=1.6.1
google-generativeai>=0.7.0
python-dotenv>=1.0.0
httpx>=0.27.0
"""))

cells.append(md("### 2A — Config & WebSocket Manager\n\nPhần này định nghĩa:\n- **Config**: đọc biến môi trường từ `.env`\n- **ConnectionManager**: quản lý danh sách browser đang kết nối WebSocket"))

cells.append(code("""\
%%writefile /content/pumpguard/backend/server.py
# ═══════════════════════════════════════════════════════════════════════
# PumpGuard AI — Backend Server (Workshop Version)
# ═══════════════════════════════════════════════════════════════════════
import asyncio, json, os, time
from contextlib import asynccontextmanager
from typing import Optional

import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

load_dotenv("backend/.env")

# ── Config ──────────────────────────────────────────────────────────────
AI_PROVIDER    = os.getenv("AI_PROVIDER", "gemini").lower()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MQTT_HOST      = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT      = int(os.getenv("MQTT_PORT", "1883"))
TOPIC_SENSORS  = "pump/sensors"

print(f"[Config] AI={AI_PROVIDER} | MQTT={MQTT_HOST}:{MQTT_PORT}")
print(f"[Config] Gemini key: {'✅ OK' if GEMINI_API_KEY else '❌ Chưa set'}")

# ── WebSocket Manager ────────────────────────────────────────────────────
class ConnectionManager:
    \"\"\"Quản lý tất cả browser đang mở dashboard.\"\"\"

    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        print(f"[WS] Browser kết nối. Tổng: {len(self.active)}")

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict):
        \"\"\"Gửi JSON đến TẤT CẢ browser đang mở.\"\"\"
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

manager = ConnectionManager()
"""))

cells.append(md("### 2B — MQTT Bridge\n\nMQTT chạy trên **thread riêng**, FastAPI chạy trên **asyncio event loop**.\n\nDùng `run_coroutine_threadsafe()` để giao tiếp an toàn giữa 2 thread."))

cells.append(code("""\
%%writefile -a /content/pumpguard/backend/server.py

# ── MQTT Bridge ─────────────────────────────────────────────────────────
class MQTTBridge:
    \"\"\"Subscribe MQTT → forward real-time đến WebSocket clients.\"\"\"

    def __init__(self):
        self.client = mqtt.Client(client_id="pumpguard-backend", protocol=mqtt.MQTTv311)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print(f"[MQTT] ✅ Kết nối tới {MQTT_HOST}:{MQTT_PORT}")
            client.subscribe(TOPIC_SENSORS, qos=1)
        else:
            print(f"[MQTT] ❌ Lỗi kết nối rc={rc}")

    def _on_message(self, client, userdata, msg):
        \"\"\"Sensor gửi data → broadcast ngay đến tất cả browser.\"\"\"
        if not self.loop:
            return
        try:
            payload = json.loads(msg.payload.decode())
            payload["type"] = "sensor_update"
            asyncio.run_coroutine_threadsafe(
                manager.broadcast(payload), self.loop
            )
        except Exception as e:
            print(f"[MQTT] Lỗi xử lý message: {e}")

    def start(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        try:
            self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            self.client.loop_start()
        except Exception as e:
            print(f"[MQTT] ⚠️  Không kết nối được: {e}")

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()

mqtt_bridge = MQTTBridge()
"""))

cells.append(md("### 2C — FastAPI App & Endpoints\n\nKhai báo app, middleware CORS, và các API endpoint."))

cells.append(code("""\
%%writefile -a /content/pumpguard/backend/server.py

# ── FastAPI App ──────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    \"\"\"Khởi động MQTT bridge khi server start, dừng khi tắt.\"\"\"
    loop = asyncio.get_event_loop()
    mqtt_bridge.start(loop)
    yield
    mqtt_bridge.stop()

app = FastAPI(title="PumpGuard AI Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# Serve dashboard HTML
if os.path.isdir("dashboard"):
    app.mount("/dashboard", StaticFiles(directory="dashboard", html=True), name="dashboard")

@app.get("/")
async def root():
    return {"message": "PumpGuard AI is running", "dashboard": "/dashboard/"}

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "ai_provider": AI_PROVIDER,
        "api_key_configured": bool(GEMINI_API_KEY),
        "ws_clients": len(manager.active),
    }

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    \"\"\"Dashboard kết nối vào đây để nhận data real-time.\"\"\"
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()   # keep-alive
    except WebSocketDisconnect:
        manager.disconnect(ws)
"""))

cells.append(md("### 2D — AI Integration\n\nEndpoint `/analyze` nhận sensor snapshot → gọi Gemini → trả JSON phân tích."))

cells.append(code("""\
%%writefile -a /content/pumpguard/backend/server.py

# ── AI Integration ───────────────────────────────────────────────────────
SYSTEM_PROMPT = \"\"\"You are a predictive maintenance AI for industrial pumps.
Analyze sensor data and return ONLY valid JSON with these fields:
risk_level (LOW/MEDIUM/HIGH/CRITICAL), confidence (0-1), summary,
anomalous_sensors (list), recommended_actions (list), estimated_hours_to_failure.\"\"\"

@app.post("/analyze")
async def analyze(req: dict):
    \"\"\"Gọi AI phân tích dữ liệu sensor và broadcast kết quả đến dashboard.\"\"\"
    snapshot = req.get("snapshot", req)
    result = await _call_gemini(json.dumps(snapshot, indent=2))
    result["type"] = "ai_recommendation"
    await manager.broadcast(result)
    return result

async def _call_gemini(sensor_json: str) -> dict:
    \"\"\"Gọi Google Gemini API để phân tích dữ liệu.\"\"\"
    if not GEMINI_API_KEY:
        return _mock_response("[MOCK] Set GEMINI_API_KEY in .env")
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(
            "gemini-1.5-flash",
            system_instruction=SYSTEM_PROMPT,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                max_output_tokens=1200,
            ),
        )
        response = await asyncio.to_thread(
            model.generate_content,
            f"Sensor data:\\n{sensor_json}\\n\\nProvide maintenance assessment."
        )
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception as e:
        print(f"[Gemini] Error: {e}")
        return _mock_response(f"Error: {e}")

def _mock_response(note="") -> dict:
    return {
        "risk_level": "HIGH", "confidence": 0.85,
        "summary": f"Bearing degradation detected. {note}",
        "anomalous_sensors": [
            {"sensor": "vibration", "current_value": 6.2, "unit": "mm/s",
             "normal_range": "0-4.5", "deviation": "+38%",
             "interpretation": "Elevated vibration suggests bearing wear"}
        ],
        "recommended_actions": [
            {"priority": 1, "action": "Inspect bearings immediately",
             "timeline": "Within 4 hours", "responsible": "Maintenance team"}
        ],
        "estimated_hours_to_failure": 18,
    }
"""))

# ─── MODULE 3: CONFIG ────────────────────────────────────────────────────────
cells.append(md("## Module 3 — Cấu hình .env\n\nĐiền Gemini API key của bạn. Lấy miễn phí tại: https://aistudio.google.com/apikey"))

cells.append(code("""\
GEMINI_API_KEY = "AIzaSy-xxxx-thay-bang-key-cua-ban"  # ← đổi thành key thật

with open("/content/pumpguard/backend/.env", "w") as f:
    f.write(f\"\"\"AI_PROVIDER=gemini
GEMINI_API_KEY={GEMINI_API_KEY}
MQTT_HOST=localhost
MQTT_PORT=1883
\"\"\")

print("✅ .env đã tạo")
print(f"   Gemini key: {GEMINI_API_KEY[:12]}..." if len(GEMINI_API_KEY) > 15 else "   ⚠️ Chưa điền key thật!")
"""))

# ─── MODULE 4: START SERVER ───────────────────────────────────────────────────
cells.append(md("## Module 4 — Khởi động Backend\n\nKiểm tra `/health` để xác nhận server đang chạy."))

cells.append(code("""\
import subprocess, time, sys, os, requests

os.chdir('/content/pumpguard')
subprocess.run(["pkill", "-f", "uvicorn"], capture_output=True)
time.sleep(1)

env_vars = {k: v for line in open("backend/.env").read().splitlines()
            if "=" in line and not line.startswith("#")
            for k, v in [line.split("=", 1)]}

log = open("/tmp/backend.log", "w")
proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "backend.server:app",
     "--host", "0.0.0.0", "--port", "8000"],
    stdout=log, stderr=log, env={**os.environ, **env_vars}
)

print("⏳ Khởi động backend", end="")
for _ in range(20):
    time.sleep(1)
    try:
        r = requests.get("http://localhost:8000/health", timeout=2)
        if r.status_code == 200:
            data = r.json()
            print(f"\\n✅ Backend running!")
            print(f"   AI: {data['ai_provider']} | Key: {'✅' if data['api_key_configured'] else '❌'}")
            print(f"   WebSocket clients: {data['ws_clients']}")
            break
    except:
        print(".", end="", flush=True)
else:
    print("\\n❌ Backend lỗi. Xem log:")
    print(open("/tmp/backend.log").read()[-2000:])
"""))

cells.append(code("""\
# Test /analyze endpoint thủ công
import requests, json

test_snapshot = {
    "snapshot": {
        "machine_status": "WARNING",
        "health_score": 42.0,
        "sensors": {
            "vibration": 6.8,
            "temperature": 94.2,
            "pressure": 5.1,
            "flow_rate": 280.0
        }
    }
}

print("🤖 Gọi AI phân tích...")
r = requests.post("http://localhost:8000/analyze", json=test_snapshot)
result = r.json()
print(f"Risk Level : {result.get('risk_level')}")
print(f"Confidence : {result.get('confidence')}")
print(f"Summary    : {result.get('summary')}")
print(f"ETF (hours): {result.get('estimated_hours_to_failure')}")
"""))

# ─── MODULE 5: DASHBOARD ─────────────────────────────────────────────────────
cells.append(md("""## Module 5 — Dashboard HTML

Dashboard là file HTML tĩnh (khoảng 1600 dòng HTML/CSS/JS).  
Nó kết nối WebSocket tới backend và hiển thị data real-time.

Chúng ta sẽ **tải từ repo GitHub** thay vì viết tay.
"""))

cells.append(code("""\
import subprocess

print("📥 Tải dashboard HTML...")
subprocess.run([
    "wget", "-q", "-O", "/content/pumpguard/dashboard/index.html",
    "https://raw.githubusercontent.com/hoamx2602/demo-iot/cloud-classroom/dashboard/index.html"
], check=True)

size = os.path.getsize("/content/pumpguard/dashboard/index.html")
print(f"✅ Dashboard đã tải! ({size:,} bytes)")
print(f"   URL: http://localhost:8000/dashboard/")
"""))

# ─── MODULE 6: PUBLIC URL ─────────────────────────────────────────────────────
cells.append(md("""## Module 6 — Tạo Public URL

Dùng **Cloudflare Tunnel** để expose server ra internet.  
**Không cần tài khoản, không cần token.**
"""))

cells.append(code("""\
import subprocess, time, re

print("📥 Tải cloudflared...")
subprocess.run([
    "wget", "-q", "-O", "/usr/local/bin/cloudflared",
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
], check=True)
subprocess.run(["chmod", "+x", "/usr/local/bin/cloudflared"])
print("✅ Cloudflared ready")

subprocess.run(["pkill", "-f", "cloudflared"], capture_output=True)
time.sleep(1)

log = open("/tmp/cf.log", "w")
subprocess.Popen(
    ["cloudflared", "tunnel", "--url", "http://localhost:8000", "--no-autoupdate"],
    stdout=log, stderr=subprocess.STDOUT
)

print("⏳ Đợi Cloudflare kết nối", end="")
public_url = None
for _ in range(30):
    time.sleep(2)
    try:
        match = re.search(r"https://[\w-]+\.trycloudflare\.com", open("/tmp/cf.log").read())
        if match:
            public_url = match.group(0)
            break
    except: pass
    print(".", end="", flush=True)

print()
if public_url:
    print("=" * 58)
    print("🎉  PUMPGUARD AI ĐANG CHẠY!")
    print("=" * 58)
    print(f"\\n🌐  Dashboard: {public_url}/dashboard/")
    print(f"🔗  API Docs:  {public_url}/docs")
    print(f"❤️   Health:   {public_url}/health")
    print("\\n📌  Share URL này cho học viên mở trên browser!")
    print("=" * 58)
else:
    print("❌ Không lấy được URL")
    print(open("/tmp/cf.log").read()[-1000:])
"""))

# ─── MODULE 7: LIVE DATA ──────────────────────────────────────────────────────
cells.append(md("""## Module 7 — Stream dữ liệu sensor thật (Tuỳ chọn)

Nếu có `data/sensor.csv` — chạy cell này để stream data lên dashboard.  
Nếu không có — dùng **Operator Controls** (nút ⚙ góc phải dashboard).
"""))

cells.append(code("""\
# Download data replay script từ repo
os.makedirs("scripts", exist_ok=True)
subprocess.run([
    "wget", "-q", "-O", "scripts/mqtt_replay.py",
    "https://raw.githubusercontent.com/hoamx2602/demo-iot/main/scripts/mqtt_replay.py"
], check=True)
print("✅ Script đã tải. Chạy cell tiếp theo để stream data.")
"""))

cells.append(code("""\
# ⚠️ Cell này chạy liên tục — nhấn ⏹ để dừng
import subprocess
subprocess.run([
    sys.executable, "scripts/mqtt_replay.py",
    "--mqtt-host", "localhost",
    "--mode", "simulate",   # hoặc --csv data/sensor.csv nếu có file
    "--compression", "360",
])
"""))

# ─── TROUBLESHOOTING ─────────────────────────────────────────────────────────
cells.append(md("""## 🛠 Troubleshooting

| Vấn đề | Giải pháp |
|--------|----------|
| Backend không khởi động | `!cat /tmp/backend.log` |
| AI chỉ hiện MOCK | Kiểm tra `GEMINI_API_KEY` ở Module 3 |
| Dashboard không load | Chạy lại Module 5 |
| URL Cloudflare không lên | Chạy lại Module 6 |
| Colab Runtime timeout | Chạy lại từ Module 4 (bỏ qua 0-2) |

```python
# Xem log backend
!tail -30 /tmp/backend.log

# Xem log Cloudflare
!cat /tmp/cf.log

# Restart backend
!pkill -f uvicorn
```
"""))

# ─── WRITE FILE ───────────────────────────────────────────────────────────────
nb = {
    "nbformat": 4,
    "nbformat_minor": 0,
    "metadata": {
        "colab": {"provenance": [], "name": "PumpGuard_Workshop.ipynb"},
        "kernelspec": {"name": "python3", "display_name": "Python 3"},
    },
    "cells": cells,
}

out_path = os.path.join(os.path.dirname(__file__), "PumpGuard_Workshop.ipynb")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(nb, f, ensure_ascii=False, indent=1)

print(f"✅ Notebook đã tạo: {out_path}")
print(f"   Số cell: {len(cells)}")
