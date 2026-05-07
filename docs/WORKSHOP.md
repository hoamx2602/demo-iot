# 🏭 Workshop: Build PumpGuard AI trên Google Colab

> **Dành cho học viên** — Bạn đã được cung cấp bộ code đầy đủ.  
> Tài liệu này hướng dẫn bạn setup và chạy hệ thống từng bước trên Google Colab.

---

## Trước khi bắt đầu

### Bạn cần có
- [ ] Tài khoản Google (để dùng Colab)
- [ ] **Gemini API Key** miễn phí → [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
- [ ] Bộ code được giảng viên cung cấp (zip file)

### Bộ code gồm những gì?

```
pumpguard/
├── backend/
│   ├── server.py          ← FastAPI backend (Python)
│   ├── requirements.txt   ← Danh sách thư viện Python
│   └── .env.example       ← Template cấu hình
├── dashboard/
│   └── index.html         ← Giao diện web (HTML/JS)
├── data/
│   ├── sensor_groups.json ← Cấu hình nhóm sensor
│   └── sensor.csv         ← Dữ liệu sensor thật (124MB)
└── scripts/
    └── mqtt_replay.py     ← Script phát lại dữ liệu
```

### Kiến trúc hệ thống

```
[sensor.csv] → [mqtt_replay.py]
                     │  publish
                     ▼
              [Mosquitto Broker]  ← port 1883
                     │  subscribe
                     ▼
              [FastAPI Backend]   ← port 8000
                 │           │
                 │ WebSocket  │ POST /analyze
                 ▼           ▼
           [Dashboard]   [Gemini AI]
           (browser)     (cloud API)
```

---

## Bước 1 — Mở Google Colab

1. Vào [colab.research.google.com](https://colab.research.google.com)
2. Click **"New notebook"**
3. Đổi tên notebook: click vào "Untitled0.ipynb" → gõ `PumpGuard_Demo`

---

## Bước 2 — Tạo cấu trúc thư mục

Copy đoạn code sau vào **cell đầu tiên** và chạy (Shift+Enter):

```python
import os

# Tạo cấu trúc thư mục
folders = [
    '/content/pumpguard/backend',
    '/content/pumpguard/dashboard',
    '/content/pumpguard/data',
    '/content/pumpguard/scripts',
]
for folder in folders:
    os.makedirs(folder, exist_ok=True)

print("✅ Đã tạo cấu trúc thư mục:")
print("   /content/pumpguard/")
print("     ├── backend/")
print("     ├── dashboard/")
print("     ├── data/")
print("     └── scripts/")
```

---

## Bước 3 — Upload file code lên Colab

### Mở thanh Files
Ở bên trái Colab, click biểu tượng 📁 (Files) để mở file browser.

### Upload từng file

Với mỗi file dưới đây, làm theo thứ tự:

#### 3.1 — Upload `backend/server.py`
1. Điều hướng đến thư mục `/content/pumpguard/backend/`  
   *(Click chuột phải vào folder `backend` → không cần bước này nếu dùng drag & drop)*
2. Click nút **Upload** (biểu tượng ↑) trong file browser
3. Chọn file `server.py` từ máy tính

#### 3.2 — Upload `backend/requirements.txt`
Tương tự, upload vào `/content/pumpguard/backend/`

#### 3.3 — Upload `dashboard/index.html`
Upload vào `/content/pumpguard/dashboard/`

#### 3.4 — Upload `data/sensor_groups.json`
Upload vào `/content/pumpguard/data/`

#### 3.5 — Upload `scripts/mqtt_replay.py`
Upload vào `/content/pumpguard/scripts/`

#### 3.6 — Upload `data/sensor.csv` *(tuỳ chọn — file lớn 124MB)*
Upload vào `/content/pumpguard/data/`  
> ⚠️ File này lớn, có thể bỏ qua nếu chỉ muốn demo AI với dữ liệu mô phỏng.

### Kiểm tra sau khi upload

Chạy cell sau để xác nhận file đã đúng chỗ:

```python
import os

required_files = [
    '/content/pumpguard/backend/server.py',
    '/content/pumpguard/backend/requirements.txt',
    '/content/pumpguard/dashboard/index.html',
    '/content/pumpguard/data/sensor_groups.json',
    '/content/pumpguard/scripts/mqtt_replay.py',
]

all_ok = True
for f in required_files:
    exists = os.path.exists(f)
    status = "✅" if exists else "❌ THIẾU"
    print(f"{status}  {f.replace('/content/pumpguard/', '')}")
    if not exists:
        all_ok = False

print()
print("✅ Tất cả file đã sẵn sàng!" if all_ok else "❌ Upload các file còn thiếu trước khi tiếp tục.")
```

---

## Bước 4 — Cài đặt dependencies

```python
import subprocess, sys

print("📦 Cài Python packages...")
subprocess.run(
    [sys.executable, "-m", "pip", "install", "-q",
     "-r", "/content/pumpguard/backend/requirements.txt"],
    check=True
)

print("🦟 Cài Mosquitto MQTT broker...")
subprocess.run(
    ["apt-get", "install", "-y", "-q", "mosquitto"],
    check=True, capture_output=True
)

print("✅ Tất cả dependencies đã cài xong!")
```

---

## Bước 5 — Cấu hình API Key

**Lấy Gemini API Key tại:** [aistudio.google.com/apikey](https://aistudio.google.com/apikey)  
*(Đăng nhập Google → "Get API Key" → "Create API key")*

```python
import os

# ════════════════════════════════════════════════
# ĐIỀN API KEY CỦA BẠN VÀO ĐÂY
GEMINI_API_KEY = "AIzaSy-xxxx"   # ← thay bằng key thật
# ════════════════════════════════════════════════

# Tạo file .env
env_content = f"""AI_PROVIDER=gemini
GEMINI_API_KEY={GEMINI_API_KEY}
MQTT_HOST=localhost
MQTT_PORT=1883
"""

with open("/content/pumpguard/backend/.env", "w") as f:
    f.write(env_content)

# Kiểm tra
if GEMINI_API_KEY.startswith("AIzaSy") and len(GEMINI_API_KEY) > 20:
    print(f"✅ API key hợp lệ: {GEMINI_API_KEY[:14]}...")
else:
    print("⚠️  Hãy điền API key thật vào ô trên!")
```

---

## Bước 6 — Khởi động MQTT Broker

```python
import subprocess, time

# Tạo config cho Mosquitto
with open("/tmp/mosquitto.conf", "w") as f:
    f.write("listener 1883\nallow_anonymous true\n")

# Dừng instance cũ nếu có
subprocess.run(["pkill", "-f", "mosquitto"], capture_output=True)
time.sleep(0.5)

# Khởi động
proc = subprocess.Popen(
    ["mosquitto", "-c", "/tmp/mosquitto.conf"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)
time.sleep(1)

if proc.poll() is None:
    print(f"✅ Mosquitto đang chạy (PID {proc.pid}) — port 1883")
else:
    print("❌ Lỗi khởi động Mosquitto. Thử chạy lại cell.")
```

---

## Bước 7 — Khởi động FastAPI Backend

```python
import subprocess, sys, os, time, requests

os.chdir("/content/pumpguard")

# Đọc .env vào environment
env_vars = {}
with open("backend/.env") as f:
    for line in f.read().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env_vars[k.strip()] = v.strip()

# Dừng instance cũ
subprocess.run(["pkill", "-f", "uvicorn"], capture_output=True)
time.sleep(1)

# Khởi động backend
log = open("/tmp/backend.log", "w")
proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "backend.server:app",
     "--host", "0.0.0.0", "--port", "8000"],
    stdout=log, stderr=log,
    env={**os.environ, **env_vars}
)

# Đợi backend sẵn sàng
print("⏳ Khởi động backend", end="")
for _ in range(20):
    time.sleep(1)
    try:
        r = requests.get("http://localhost:8000/health", timeout=2)
        if r.status_code == 200:
            data = r.json()
            print(f"\n✅ Backend đang chạy!")
            print(f"   AI Provider : {data.get('ai_provider')}")
            print(f"   API Key     : {'✅ OK' if data.get('api_key_configured') else '❌ Chưa set'}")
            print(f"   WS Clients  : {data.get('ws_clients', 0)}")
            break
    except:
        print(".", end="", flush=True)
else:
    print("\n❌ Backend không khởi động được. Xem log:")
    print(open("/tmp/backend.log").read()[-2000:])
```

---

## Bước 8 — Tạo Public URL (Cloudflare Tunnel)

> Không cần tài khoản, không cần token. Cloudflare tự tạo URL miễn phí.

```python
import subprocess, time, re

# Tải cloudflared
print("📥 Tải Cloudflare tunnel...")
subprocess.run([
    "wget", "-q", "-O", "/usr/local/bin/cloudflared",
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
], check=True)
subprocess.run(["chmod", "+x", "/usr/local/bin/cloudflared"])

# Dừng tunnel cũ
subprocess.run(["pkill", "-f", "cloudflared"], capture_output=True)
time.sleep(1)

# Tạo tunnel
log = open("/tmp/cf.log", "w")
subprocess.Popen(
    ["cloudflared", "tunnel", "--url", "http://localhost:8000", "--no-autoupdate"],
    stdout=log, stderr=subprocess.STDOUT
)

# Đợi URL
print("⏳ Đợi Cloudflare kết nối", end="")
public_url = None
for _ in range(30):
    time.sleep(2)
    try:
        content = open("/tmp/cf.log").read()
        match = re.search(r'https://[\w-]+\.trycloudflare\.com', content)
        if match:
            public_url = match.group(0)
            break
    except:
        pass
    print(".", end="", flush=True)

print()
if public_url:
    print()
    print("=" * 60)
    print("🎉  PUMPGUARD AI ĐANG CHẠY!")
    print("=" * 60)
    print(f"\n🌐  Dashboard  →  {public_url}/dashboard/")
    print(f"🔗  API Docs   →  {public_url}/docs")
    print(f"❤️   Health    →  {public_url}/health")
    print()
    print("📌  Mở URL Dashboard trên browser để xem hệ thống!")
    print("=" * 60)
else:
    print("❌ Không lấy được URL. Chạy lại cell này.")
    print(open("/tmp/cf.log").read()[-500:])
```

---

## Bước 9 — Chạy Data Replay *(Tuỳ chọn)*

> Chỉ thực hiện nếu bạn đã upload `data/sensor.csv` ở Bước 3.

```python
import os, sys

os.chdir("/content/pumpguard")

if os.path.exists("data/sensor.csv") and os.path.exists("data/sensor_groups.json"):
    print("▶ Bắt đầu stream dữ liệu sensor lên dashboard...")
    print("  (Nhấn ⏹ trên Colab để dừng)")
    print("-" * 50)
    # compression=360: 1 phút data thật = 1/6 giây demo
    os.system(
        f"{sys.executable} scripts/mqtt_replay.py "
        "--csv data/sensor.csv "
        "--config data/sensor_groups.json "
        "--start-at-anomaly "
        "--compression 360"
    )
else:
    print("⚠️  Không có data/sensor.csv")
    print()
    print("→ Thay thế: dùng Operator Controls trên dashboard")
    print("  Nút ⚙ ở góc phải màn hình → chọn:")
    print("  • ▶ Normal Operation")
    print("  • ⚠ Simulate Anomaly")
    print("  • 🔴 Simulate Critical Failure")
```

---

## Troubleshooting

| Vấn đề | Nguyên nhân | Giải pháp |
|--------|-------------|-----------|
| Backend lỗi ngay khi start | Import error / thiếu file | Kiểm tra file đã upload đúng chưa (Bước 3) |
| AI chỉ hiện `[MOCK]` | Chưa set API key | Chạy lại Bước 5 với key thật |
| Dashboard không kết nối WS | URL sai hoặc tunnel reset | Chạy lại Bước 8 |
| Colab bị ngắt | Runtime timeout (~1.5h free) | Chạy lại từ Bước 6 |
| Cloudflare không kết nối | Mạng Colab không ổn | Đợi 1 phút rồi chạy lại Bước 8 |

### Xem log backend khi có lỗi

```python
print(open("/tmp/backend.log").read()[-3000:])
```

### Restart nhanh sau khi Colab timeout

```
Chạy lại: Bước 6 → Bước 7 → Bước 8
(Bỏ qua: Bước 1, 2, 3, 4, 5 — đã được lưu trong session)
```

---

## Kết quả mong đợi

Sau khi hoàn thành, bạn có một hệ thống IoT đầy đủ chạy trên cloud:

- **Dashboard real-time** hiển thị: health score, trạng thái sensor, heatmap
- **AI phân tích** khi có anomaly: risk level, khuyến nghị bảo trì, ước tính thời gian hỏng
- **WebSocket** cập nhật dữ liệu mỗi giây không cần refresh trang
- **Public URL** có thể share cho bất kỳ ai xem

> 💡 **Tip cho demo:** Vào dashboard → click **⚙ Operator Controls** → chọn **"⚠ Simulate Anomaly"**  
> Sau ~30 giây AI sẽ tự động phân tích và hiển thị kết quả ở tab **AI Recommendations**.
