# 🏭 PumpGuard AI — Workshop Guide

> **Thời lượng:** 3–4 giờ | **Cấp độ:** Beginner–Intermediate  
> **Mục tiêu:** Xây dựng hệ thống IoT giám sát máy bơm công nghiệp với AI từ đầu trên Google Colab.

---

## Tổng quan kiến trúc

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
                              (browser của học viên)
```

### Tại sao chọn kiến trúc này?

| Thành phần | Lý do chọn |
|-----------|-----------|
| **MQTT** | Giao thức chuẩn IoT — nhẹ, pub/sub, phù hợp sensor data |
| **Mosquitto** | MQTT broker phổ biến nhất, miễn phí, cài được mọi nơi |
| **Node-RED** | Visual programming — dễ thấy luồng data, không cần code nhiều |
| **FastAPI** | Python, nhanh, có WebSocket built-in, tự sinh API docs |
| **WebSocket** | Push real-time từ server → browser, không cần refresh |
| **Gemini AI** | Miễn phí tier, JSON output, phù hợp phân tích kỹ thuật |
| **Cloudflare Tunnel** | Expose localhost ra internet, không cần tài khoản |

---

## Chuẩn bị trước buổi học

### Học viên cần chuẩn bị
- [ ] Tài khoản Google (để dùng Colab)
- [ ] **Gemini API Key** miễn phí → [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
- [ ] Bộ code được giảng viên phát (zip file)

### Bộ code gồm
```
pumpguard/
├── backend/
│   ├── server.py          ← FastAPI server (~700 dòng)
│   ├── requirements.txt   ← Danh sách thư viện Python
│   └── .env.example       ← Template biến môi trường
├── dashboard/
│   └── index.html         ← Giao diện web real-time
├── data/
│   ├── sensor_groups.json ← Cấu hình ngưỡng sensor
│   └── sensor.csv         ← Dữ liệu sensor thật (124MB)
├── scripts/
│   └── mqtt_replay.py     ← Script phát lại dữ liệu CSV
└── nodered/
    └── flows.json         ← Node-RED flow (import sẵn)
```

---

## Phần 0 — Giới thiệu & Mở Colab (15 phút)

### Mục tiêu
Hiểu bức tranh tổng thể và chuẩn bị môi trường làm việc.

### Bước 0.1 — Mở Google Colab
1. Vào [colab.research.google.com](https://colab.research.google.com)
2. Click **"New notebook"**
3. Đổi tên: click "Untitled0.ipynb" → gõ **PumpGuard_Workshop**
4. Đảm bảo Runtime type là **Python 3** (Runtime menu → Change runtime type)

### Bước 0.2 — Tạo cấu trúc thư mục

**Tại sao cần làm bước này?**  
Google Colab làm việc trong `/content/`. Mỗi file Python, HTML, config đều cần đúng chỗ để server có thể tìm thấy.

Tạo cell mới và chạy:

```python
import os

# Tạo cấu trúc thư mục project
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

print("✅ Cấu trúc thư mục đã sẵn sàng:")
for f in folders:
    print(f"   {f.replace('/content/pumpguard', '.')}/")
```

**Giải thích:**
- `os.makedirs(..., exist_ok=True)` — tạo thư mục, không báo lỗi nếu đã tồn tại
- `os.chdir(...)` — chuyển working directory, để các lệnh sau không cần gõ đường dẫn đầy đủ

---

## Phần 1 — MQTT: Xương sống IoT (30 phút)

### Mục tiêu
Hiểu giao thức MQTT và khởi động MQTT broker.

### Lý thuyết: MQTT là gì?

**MQTT** (Message Queuing Telemetry Transport) là giao thức nhắn tin được thiết kế riêng cho IoT:

```
Publisher                Broker              Subscriber
(sensor/script)        (Mosquitto)          (backend)

publish("pump/sensors", data) ──▶ [phân phối] ──▶ on_message(data)
```

**Tại sao không dùng REST API thay vì MQTT?**

| | REST API | MQTT |
|---|---|---|
| Kết nối | Pull (client hỏi server) | Push (server gửi client) |
| Overhead | Header HTTP lớn | Rất nhỏ (2 byte header) |
| Nhiều subscriber | Không tự nhiên | Built-in (1 publish → nhiều subscriber) |
| Phù hợp IoT | ❌ | ✅ |

**Khái niệm quan trọng:**
- **Topic**: "kênh" của message, dạng path. VD: `pump/sensors`, `pump/alerts`
- **QoS 1**: đảm bảo message đến ít nhất 1 lần (không mất)
- **Broker**: server trung gian — nhận publish, phân phối đến subscribers

### Bước 1.1 — Cài Mosquitto

```python
import subprocess, sys

print("🦟 Cài Mosquitto MQTT Broker...")
result = subprocess.run(
    ["apt-get", "install", "-y", "-q", "mosquitto"],
    capture_output=True, check=True
)
print("✅ Mosquitto đã cài xong!")

# Kiểm tra version
v = subprocess.run(["mosquitto", "--version"], capture_output=True, text=True)
print(f"   Version: {v.stdout.split(chr(10))[0]}")
```

**Tại sao Mosquitto?**  
Mosquitto là MQTT broker phổ biến nhất thế giới — nhẹ (~1MB RAM), mã nguồn mở, chuẩn MQTT 3.1.1 và 5.0, được dùng trong cả sản xuất công nghiệp lẫn Raspberry Pi.

### Bước 1.2 — Tạo config và khởi động

```python
import subprocess, time

# Config: cho phép kết nối không cần username/password (phù hợp lab)
with open("/tmp/mosquitto.conf", "w") as f:
    f.write("listener 1883\n")
    f.write("allow_anonymous true\n")

# Dừng instance cũ nếu có
subprocess.run(["pkill", "-f", "mosquitto"], capture_output=True)
time.sleep(0.5)

# Khởi động broker trong background
proc = subprocess.Popen(
    ["mosquitto", "-c", "/tmp/mosquitto.conf"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL
)
time.sleep(1)

if proc.poll() is None:
    print(f"✅ Mosquitto đang chạy (PID {proc.pid})")
    print(f"   Listening on: localhost:1883")
else:
    print("❌ Lỗi khởi động Mosquitto")
```

**Giải thích config:**
- `listener 1883` — lắng nghe trên port 1883 (chuẩn MQTT)
- `allow_anonymous true` — cho phép kết nối không cần auth (chỉ dùng trong lab/dev)

### Bước 1.3 — Test MQTT (Publish & Subscribe)

Đây là bước quan trọng để **hiểu cách MQTT hoạt động** trước khi tích hợp vào hệ thống.

```python
import paho.mqtt.client as mqtt
import json, time, threading

received_messages = []

# ── Subscriber ────────────────────────────────────────────
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        client.subscribe("pump/test", qos=1)
        print("📡 Subscriber đã kết nối và lắng nghe topic 'pump/test'")

def on_message(client, userdata, msg):
    data = json.loads(msg.payload.decode())
    received_messages.append(data)
    print(f"📨 Nhận được: {json.dumps(data, ensure_ascii=False)}")

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

print(f"\n📤 Publisher gửi: {json.dumps(test_data, ensure_ascii=False)}")
pub.publish("pump/test", json.dumps(test_data), qos=1)

time.sleep(0.5)
pub.disconnect()
sub.loop_stop()
sub.disconnect()

print(f"\n✅ MQTT hoạt động! Đã gửi 1 message, nhận {len(received_messages)} message")
```

**Quan sát:** Publisher và Subscriber không biết nhau — họ chỉ biết broker và topic. Đây là **decoupled architecture** — cốt lõi của IoT.


---

## Phần 2 — FastAPI Backend: Từng file một (45 phút)

### Mục tiêu
Hiểu cấu trúc backend và đưa từng file vào đúng chỗ.

### Lý thuyết: FastAPI + WebSocket

**FastAPI** là Python web framework hiện đại:
- Nhanh (tương đương NodeJS/Go)
- Tự sinh API docs tại `/docs`
- Built-in WebSocket support
- Dùng type hints → ít bug

**WebSocket** khác REST API:
```
REST:    Browser ──request──▶ Server ──response──▶ Browser  (1 chiều mỗi lần)
WebSocket: Browser ◀──────────────── Server  (2 chiều, kết nối liên tục)
```

Dashboard cần WebSocket vì: sensor gửi data mỗi giây, không thể cứ 1 giây browser lại gửi 1 HTTP request.

---

### Bước 2.1 — Copy `requirements.txt`

**Mục đích:** Liệt kê tất cả thư viện Python mà server.py cần dùng.

**Cách upload lên Colab:**
1. Ở bên trái Colab, click biểu tượng 📁 (Files)
2. Điều hướng đến `/content/pumpguard/backend/`
3. Click nút **↑ Upload** → chọn file `requirements.txt`

Sau khi upload, kiểm tra nội dung:

```python
print(open('/content/pumpguard/backend/requirements.txt').read())
```

**Giải thích từng package:**
```
fastapi          ← Web framework chính
uvicorn          ← ASGI server chạy FastAPI (như Gunicorn cho WSGI)
paho-mqtt        ← MQTT client library cho Python
google-generativeai ← Gemini AI SDK
python-dotenv    ← Đọc file .env vào os.environ
httpx            ← HTTP client async (dùng cho gọi AI API)
```

Cài tất cả:

```python
import subprocess, sys

print("📦 Cài Python packages từ requirements.txt...")
subprocess.run(
    [sys.executable, "-m", "pip", "install", "-q",
     "-r", "/content/pumpguard/backend/requirements.txt"],
    check=True
)
print("✅ Tất cả packages đã cài!")
```

---

### Bước 2.2 — Copy `data/sensor_groups.json`

**Mục đích:** File cấu hình định nghĩa ngưỡng bình thường/cảnh báo cho từng sensor.  
Node-RED và Backend đều đọc file này.

**Upload:** Vào `/content/pumpguard/data/` → Upload `sensor_groups.json`

Kiểm tra:

```python
import json
cfg = json.load(open('/content/pumpguard/data/sensor_groups.json'))
print("Sensors được cấu hình:")
for name, info in cfg.items():
    print(f"  {name}: warning={info.get('warning_threshold')}, critical={info.get('critical_threshold')}")
```

---

### Bước 2.3 — Tạo file `.env` (cấu hình API key)

**Mục đích:** Lưu thông tin nhạy cảm (API key) ra ngoài code.  
**Tại sao không hardcode trong code?** Vì code được commit lên Git — nếu hardcode key sẽ bị lộ.

```python
# Điền Gemini API key của bạn vào đây
# Lấy miễn phí tại: https://aistudio.google.com/apikey
GEMINI_API_KEY = "AIzaSy-xxxx"   # ← THAY BẰNG KEY THẬT

env_content = f"""# AI Provider
AI_PROVIDER=gemini
GEMINI_API_KEY={GEMINI_API_KEY}

# MQTT
MQTT_HOST=localhost
MQTT_PORT=1883
"""

with open('/content/pumpguard/backend/.env', 'w') as f:
    f.write(env_content)

# Kiểm tra (không in key ra màn hình — bảo mật)
if GEMINI_API_KEY.startswith("AIzaSy") and len(GEMINI_API_KEY) > 20:
    print(f"✅ .env đã tạo với key hợp lệ: {GEMINI_API_KEY[:14]}...")
else:
    print("⚠️  Hãy điền API key thật — hệ thống sẽ chạy nhưng AI dùng mock data")
```

---

### Bước 2.4 — Copy `backend/server.py`

**Mục đích:** Đây là "bộ não" của hệ thống — FastAPI server với WebSocket, MQTT bridge, và AI integration.

**Upload:** Vào `/content/pumpguard/backend/` → Upload `server.py`

Sau khi upload, xem qua cấu trúc:

```python
# Xem cấu trúc file
lines = open('/content/pumpguard/backend/server.py').readlines()
print(f"Tổng số dòng: {len(lines)}")

# In ra phần config và các endpoint chính
import re
for i, line in enumerate(lines, 1):
    if any(x in line for x in ['def ', 'class ', '@app.', '# ──']):
        print(f"  Line {i:4d}: {line.rstrip()}")
```

**Kiến trúc server.py:**
```
server.py
├── Config        ← đọc .env (AI key, MQTT host/port)
├── ConnectionManager ← quản lý WebSocket clients
├── MQTTBridge    ← subscribe MQTT → broadcast WebSocket
├── FastAPI app
│   ├── GET /health     ← kiểm tra server
│   ├── WS  /ws         ← WebSocket endpoint
│   ├── POST /analyze   ← gọi AI phân tích
│   └── /dashboard/     ← serve HTML tĩnh
└── AI functions  ← gọi Gemini/Claude/OpenAI
```

---

### Bước 2.5 — Copy `dashboard/index.html`

**Mục đích:** Giao diện web hiển thị data real-time từ WebSocket.

**Upload:** Vào `/content/pumpguard/dashboard/` → Upload `index.html`

```python
size = os.path.getsize('/content/pumpguard/dashboard/index.html')
print(f"✅ Dashboard: {size:,} bytes ({size//1024} KB)")
print("   Gồm: HTML structure + CSS styling + JavaScript WebSocket client")
```

---

### Bước 2.6 — Kiểm tra tất cả file

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

print("📋 Kiểm tra file:")
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
print("✅ Sẵn sàng khởi động backend!" if all_ok else "❌ Upload các file còn thiếu.")
```

---

### Bước 2.7 — Khởi động Backend

```python
import subprocess, sys, os, time, requests

os.chdir('/content/pumpguard')

# Đọc .env vào environment variables
env_vars = {}
for line in open('backend/.env').read().splitlines():
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env_vars[k.strip()] = v.strip()

# Dừng instance cũ
subprocess.run(['pkill', '-f', 'uvicorn'], capture_output=True)
time.sleep(1)

# Start backend
log = open('/tmp/backend.log', 'w')
proc = subprocess.Popen(
    [sys.executable, '-m', 'uvicorn', 'backend.server:app',
     '--host', '0.0.0.0', '--port', '8000'],
    stdout=log, stderr=log,
    env={**os.environ, **env_vars}
)

print("⏳ Khởi động backend", end='')
for _ in range(20):
    time.sleep(1)
    try:
        r = requests.get('http://localhost:8000/health', timeout=2)
        if r.status_code == 200:
            d = r.json()
            print(f"\n✅ Backend đang chạy! (PID {proc.pid})")
            print(f"   AI Provider : {d.get('ai_provider')}")
            print(f"   API Key     : {'✅ OK' if d.get('api_key_configured') else '❌ Chưa set'}")
            break
    except:
        print('.', end='', flush=True)
else:
    print("\n❌ Lỗi. Xem log:")
    print(open('/tmp/backend.log').read()[-2000:])
```

### Bước 2.8 — Test API bằng curl

FastAPI tự sinh API docs tại `/docs`. Test thử endpoint `/analyze`:

```python
import requests, json

# Gửi snapshot sensor giả lập để test AI
test_payload = {
    "snapshot": {
        "machine_status": "WARNING",
        "health_score": 42.5,
        "overall_status": "WARNING",
        "sensors": {
            "vibration": {"current": 6.8, "status": "WARNING", "trending": "DEGRADING"},
            "temperature": {"current": 91.2, "status": "WARNING", "trending": "STABLE"},
            "pressure": {"current": 5.1, "status": "NORMAL", "trending": "STABLE"},
            "flow_rate": {"current": 118.0, "status": "NORMAL", "trending": "STABLE"},
        }
    }
}

print("🤖 Gọi AI phân tích sensor...")
r = requests.post('http://localhost:8000/analyze', json=test_payload, timeout=30)

if r.status_code == 200:
    result = r.json()
    print(f"\n📊 Kết quả AI:")
    print(f"  Risk Level   : {result.get('risk_level')}")
    print(f"  Confidence   : {result.get('confidence', 0)*100:.0f}%")
    print(f"  Summary      : {result.get('summary')}")
    print(f"  ETF (hours)  : {result.get('estimated_hours_to_failure')}")
    if result.get('recommended_actions'):
        print(f"  Action #1    : {result['recommended_actions'][0].get('action')}")
else:
    print(f"❌ Lỗi HTTP {r.status_code}: {r.text[:300]}")
```

---

## Phần 3 — Node-RED: Visual Data Pipeline (45 phút)

### Mục tiêu
Cài Node-RED trên Colab, import flow có sẵn, hiểu từng node xử lý data.

### Lý thuyết: Node-RED là gì?

**Node-RED** là công cụ visual programming cho IoT do IBM phát triển:
- Kéo thả các "node" để tạo luồng xử lý data
- Mỗi node làm 1 việc cụ thể: nhận MQTT, xử lý, gọi API, gửi WebSocket
- Không cần code nhiều — phù hợp cho prototyping và giảng dạy

**Tại sao dùng Node-RED trong hệ thống này?**

Thay vì viết Python code để:
1. Subscribe MQTT
2. Validate data
3. Tính rolling average
4. Detect anomaly
5. Gọi AI API

→ Node-RED làm tất cả bằng giao diện kéo thả, dễ debug từng bước.

**Flow trong hệ thống:**
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

### Bước 3.1 — Cài Node.js và Node-RED

```python
import subprocess, time, sys

print("📦 Cài Node.js...")
subprocess.run("curl -fsSL https://deb.nodesource.com/setup_20.x | bash -",
               shell=True, capture_output=True, check=True)
subprocess.run(["apt-get", "install", "-y", "-q", "nodejs"],
               capture_output=True, check=True)

v = subprocess.run(["node", "--version"], capture_output=True, text=True)
print(f"✅ Node.js: {v.stdout.strip()}")

print("📦 Cài Node-RED (có thể mất 1-2 phút)...")
subprocess.run(
    ["npm", "install", "-g", "--unsafe-perm", "--silent", "node-red"],
    capture_output=True, check=True
)
print("✅ Node-RED đã cài xong!")
```

### Bước 3.2 — Copy `nodered/flows.json`

**Upload:** Vào `/content/pumpguard/nodered/` → Upload `flows.json`

```python
import json
flows = json.load(open('/content/pumpguard/nodered/flows.json'))
print(f"✅ flows.json hợp lệ — {len(flows)} nodes")
node_types = set(n.get('type') for n in flows if 'type' in n)
print(f"   Loại nodes: {', '.join(sorted(node_types))}")
```

### Bước 3.3 — Khởi động Node-RED

```python
import subprocess, time, os

# Tạo thư mục config Node-RED
nr_home = '/root/.node-red'
os.makedirs(nr_home, exist_ok=True)

# Copy flows.json vào thư mục Node-RED
import shutil
shutil.copy('/content/pumpguard/nodered/flows.json', f'{nr_home}/flows.json')

# Dừng instance cũ
subprocess.run(['pkill', '-f', 'node-red'], capture_output=True)
time.sleep(1)

# Khởi động Node-RED
log = open('/tmp/nodered.log', 'w')
proc = subprocess.Popen(
    ['node-red', '--port', '1880', '--userDir', nr_home],
    stdout=log, stderr=subprocess.STDOUT
)

print("⏳ Khởi động Node-RED", end='')
for _ in range(20):
    time.sleep(2)
    try:
        r = requests.get('http://localhost:1880', timeout=3)
        if r.status_code == 200:
            print(f"\n✅ Node-RED đang chạy! (PID {proc.pid})")
            print(f"   UI: http://localhost:1880")
            break
    except:
        print('.', end='', flush=True)
else:
    print("\n❌ Lỗi. Xem log:")
    print(open('/tmp/nodered.log').read()[-1500:])
```

### Bước 3.4 — Expose Node-RED ra public URL (port 1880)

```python
import subprocess, time, re

# Dừng tunnel cũ
subprocess.run(['pkill', '-f', 'cloudflared'], capture_output=True)
time.sleep(1)

# Tunnel cho backend (port 8000)
log_be = open('/tmp/cf_backend.log', 'w')
subprocess.Popen(
    ['cloudflared', 'tunnel', '--url', 'http://localhost:8000', '--no-autoupdate'],
    stdout=log_be, stderr=subprocess.STDOUT
)

# Tunnel cho Node-RED (port 1880)
log_nr = open('/tmp/cf_nodered.log', 'w')
subprocess.Popen(
    ['cloudflared', 'tunnel', '--url', 'http://localhost:1880', '--no-autoupdate'],
    stdout=log_nr, stderr=subprocess.STDOUT
)

print("⏳ Đợi Cloudflare tạo URLs", end='')
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
print("🎉  HỆ THỐNG ĐANG CHẠY!")
print("=" * 62)
if be_url:
    print(f"\n🌐  Dashboard  →  {be_url}/dashboard/")
    print(f"🔗  API Docs   →  {be_url}/docs")
if nr_url:
    print(f"🔧  Node-RED   →  {nr_url}")
print("=" * 62)
```

### Bước 3.5 — Khám phá Node-RED UI

Mở URL Node-RED trên browser. Bạn sẽ thấy flow đã được import sẵn.

**Giải thích từng node trong flow:**

| Node | Loại | Mục đích |
|------|------|---------|
| **Subscribe pump/sensors** | MQTT in | Lắng nghe topic, nhận raw data từ sensor |
| **Parse & Validate** | Function | Kiểm tra cấu trúc JSON hợp lệ, thêm timestamp |
| **Rolling Buffer (60)** | Function | Giữ 60 readings gần nhất trong context |
| **Compute Trends & Stats** | Function | Tính avg, slope, std dev, phát hiện trend |
| **If NORMAL / If ANOMALY** | Switch | Phân luồng xử lý theo trạng thái |
| **Throttle AI (1/10s)** | Delay | Giới hạn tần suất gọi AI — tránh spam API |
| **Build AI Payload** | Function | Chuẩn bị dữ liệu compact để gửi AI |
| **POST → /analyze** | HTTP Request | Gọi FastAPI backend để AI phân tích |
| **WS → Dashboard** | WebSocket out | Push kết quả đến browser real-time |

**Tại sao cần Rolling Buffer?**  
Một điểm data đơn lẻ không đủ để phát hiện trend. 60 readings × 1s = 60 giây gần nhất cho phép tính được:
- Slope (xu hướng tăng/giảm)
- Standard deviation (độ ổn định)
- Rate of change (tốc độ thay đổi)

**Tại sao Throttle AI?**  
Gemini free tier có rate limit. Nếu anomaly kéo dài 60 giây, không cần gọi AI 60 lần — 1 lần mỗi 10 giây là đủ.

---

## Phần 4 — Chạy Data Replay & Xem Demo (20 phút)

### Bước 4.1 — Copy `scripts/mqtt_replay.py`

**Upload:** Vào `/content/pumpguard/scripts/` → Upload `mqtt_replay.py`

**Mục đích:** Script đọc `sensor.csv` (dữ liệu thật) và publish lên MQTT từng dòng một, mô phỏng sensor thật đang gửi data.

### Bước 4.2 — Copy `data/sensor.csv` *(nếu có)*

> ⚠️ File lớn (~124MB). Upload mất 1-2 phút.

**Nếu không có CSV:** Dùng **Operator Controls** trên dashboard (nút ⚙ góc phải).

### Bước 4.3 — Chạy data replay

> ⚠️ Cell này chạy **liên tục** — nhấn ⏹ để dừng.

```python
import os, sys
os.chdir('/content/pumpguard')

if os.path.exists('data/sensor.csv') and os.path.exists('data/sensor_groups.json'):
    print("▶ Stream dữ liệu sensor lên dashboard...")
    print("  compression=360: 1 phút data = 1/6 giây demo")
    print("  start-at-anomaly: bắt đầu gần điểm bất thường để demo nhanh")
    print("-" * 50)
    os.system(
        f"{sys.executable} scripts/mqtt_replay.py "
        "--csv data/sensor.csv "
        "--config data/sensor_groups.json "
        "--start-at-anomaly "
        "--compression 360"
    )
else:
    print("ℹ️  Không có sensor.csv")
    print("   → Mở Dashboard → click ⚙ → chọn '⚠ Simulate Anomaly'")
    print("   AI sẽ tự động phân tích sau ~30 giây")
```

### Bước 4.4 — Quan sát trên Dashboard

Mở URL Dashboard trên browser. Các tab cần chú ý:

| Tab | Hiển thị |
|-----|---------|
| **Overview** | Health score tổng thể, trạng thái máy, 4 sensor chính |
| **Sensor Status** | Heatmap tất cả sensor, chọn từng sensor để xem chi tiết |
| **AI Recommendations** | Kết quả phân tích từ Gemini: risk, actions, cost impact |
| **Alerts** | Lịch sử cảnh báo |

**Để trigger AI phân tích nhanh:**
1. Click **⚙ Operator Controls** (góc phải)
2. Chọn **"⚠ Simulate Anomaly"** hoặc **"🔴 Simulate Critical"**
3. Chuyển sang tab **AI Recommendations** — kết quả xuất hiện sau ~10-30 giây

---

## Troubleshooting

| Vấn đề | Nguyên nhân | Giải pháp |
|--------|-------------|-----------|
| Backend lỗi khi start | File thiếu hoặc import error | Kiểm tra lại file đã upload đúng chưa |
| AI chỉ hiện `[MOCK]` | Chưa điền API key | Chạy lại Bước 2.3 với key thật |
| Node-RED không import flow | flows.json đặt sai chỗ | Đảm bảo copy vào `~/.node-red/flows.json` |
| Dashboard không kết nối WS | URL tunnel đã reset | Chạy lại bước Cloudflare |
| Colab bị ngắt sau ~1.5h | Runtime timeout | Chạy lại từ Phần 1 Bước 1.2 |
| MQTT không nhận data | Replay chưa chạy | Chạy Phần 4 hoặc dùng Operator Controls |

### Lệnh debug nhanh

```python
# Xem log backend
print(open('/tmp/backend.log').read()[-3000:])

# Xem log Node-RED
print(open('/tmp/nodered.log').read()[-2000:])

# Kiểm tra services đang chạy
import subprocess
out = subprocess.run(['ps', 'aux'], capture_output=True, text=True).stdout
for svc in ['mosquitto', 'uvicorn', 'node-red', 'cloudflared']:
    running = svc in out
    print(f"  {'✅' if running else '❌'} {svc}")
```

### Restart nhanh sau timeout

Khi Colab Runtime bị ngắt, chạy lại các bước theo thứ tự:
1. ✅ Không cần: Bước 0 (tạo thư mục) và Bước 2.1-2.5 (upload file)
2. 🔄 Chạy lại: Phần 1 Bước 1.2 (MQTT) → Phần 2 Bước 2.7 (Backend) → Phần 3 Bước 3.3 (Node-RED) → Cloudflare

---

## Tổng kết Workshop

Sau buổi học, bạn đã xây dựng:

```
✅ MQTT Broker (Mosquitto)   ← nhận data từ sensor
✅ Data Pipeline (Node-RED)  ← xử lý, tính toán, phát hiện anomaly
✅ AI Backend (FastAPI)      ← kết nối AI, WebSocket
✅ Dashboard (HTML/JS)       ← hiển thị real-time
✅ AI Integration (Gemini)   ← phân tích, đề xuất bảo trì
✅ Public URL (Cloudflare)   ← share cho bất kỳ ai
```

**Câu hỏi để ôn tập:**
1. Tại sao IoT dùng MQTT thay vì REST API?
2. Rolling buffer 60 readings có ý nghĩa gì?
3. Tại sao cần throttle AI calls?
4. WebSocket khác gì so với HTTP polling?
5. Tại sao lưu API key trong `.env` thay vì hardcode trong code?
