"""Tạo PumpGuard_Colab.ipynb từ WORKSHOP.md (các code block được extract thành cells)"""
import json, os, re

def md(src): return {"cell_type":"markdown","metadata":{},"source":[src]}
def code(src): return {"cell_type":"code","metadata":{},"source":[src],"execution_count":None,"outputs":[]}

cells = []

cells.append(md("""# 🏭 PumpGuard AI — Setup Notebook

> Notebook này giúp bạn chạy từng bước theo **docs/WORKSHOP.md**.  
> **Yêu cầu:** Đã upload code files lên Colab theo hướng dẫn trong WORKSHOP.md trước khi chạy.

| Bước | Nội dung |
|------|----------|
| 1 | Tạo thư mục |
| 2 | Kiểm tra file |
| 3 | Cài dependencies |
| 4 | Cấu hình API Key |
| 5 | Khởi động MQTT |
| 6 | Khởi động Backend |
| 7 | Public URL (Cloudflare) |
| 8 | Stream data (tuỳ chọn) |
"""))

cells.append(md("## Bước 1 — Tạo cấu trúc thư mục"))
cells.append(code("""\
import os
for folder in ['/content/pumpguard/backend', '/content/pumpguard/dashboard',
               '/content/pumpguard/data', '/content/pumpguard/scripts']:
    os.makedirs(folder, exist_ok=True)
print("✅ Đã tạo thư mục. Bây giờ upload file theo WORKSHOP.md Bước 3.")
print()
print("   /content/pumpguard/")
print("     ├── backend/    ← server.py, requirements.txt")
print("     ├── dashboard/  ← index.html")
print("     ├── data/       ← sensor_groups.json (+ sensor.csv nếu có)")
print("     └── scripts/    ← mqtt_replay.py")
"""))

cells.append(md("## Bước 2 — Kiểm tra file đã upload đúng chưa"))
cells.append(code("""\
import os
files = [
    '/content/pumpguard/backend/server.py',
    '/content/pumpguard/backend/requirements.txt',
    '/content/pumpguard/dashboard/index.html',
    '/content/pumpguard/data/sensor_groups.json',
    '/content/pumpguard/scripts/mqtt_replay.py',
]
all_ok = True
for f in files:
    ok = os.path.exists(f)
    print(f"{'✅' if ok else '❌ THIẾU'}  {f.replace('/content/pumpguard/', '')}")
    if not ok: all_ok = False
print()
print("✅ Tất cả OK!" if all_ok else "❌ Upload file còn thiếu theo WORKSHOP.md trước khi tiếp tục!")
"""))

cells.append(md("## Bước 3 — Cài dependencies"))
cells.append(code("""\
import subprocess, sys
print("📦 Cài Python packages...")
subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                "-r", "/content/pumpguard/backend/requirements.txt"], check=True)
print("🦟 Cài Mosquitto...")
subprocess.run(["apt-get", "install", "-y", "-q", "mosquitto"], check=True, capture_output=True)
print("✅ Tất cả dependencies đã cài xong!")
"""))

cells.append(md("""## Bước 4 — Cấu hình Gemini API Key

Lấy key miễn phí tại: https://aistudio.google.com/apikey  
Điền vào ô `GEMINI_API_KEY` bên dưới rồi chạy cell.
"""))
cells.append(code("""\
GEMINI_API_KEY = "AIzaSy-xxxx"   # ← THAY BẰNG KEY THẬT CỦA BẠN

with open("/content/pumpguard/backend/.env", "w") as f:
    f.write(f"AI_PROVIDER=gemini\\nGEMINI_API_KEY={GEMINI_API_KEY}\\nMQTT_HOST=localhost\\nMQTT_PORT=1883\\n")

if GEMINI_API_KEY.startswith("AIzaSy") and len(GEMINI_API_KEY) > 20:
    print(f"✅ API key: {GEMINI_API_KEY[:14]}...")
else:
    print("⚠️  Điền API key thật vào ô trên!")
"""))

cells.append(md("## Bước 5 — Khởi động MQTT Broker"))
cells.append(code("""\
import subprocess, time
with open("/tmp/mosquitto.conf", "w") as f:
    f.write("listener 1883\\nallow_anonymous true\\n")
subprocess.run(["pkill", "-f", "mosquitto"], capture_output=True)
time.sleep(0.5)
proc = subprocess.Popen(["mosquitto", "-c", "/tmp/mosquitto.conf"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
print(f"{'✅ Mosquitto running (PID ' + str(proc.pid) + ') — port 1883' if proc.poll() is None else '❌ Lỗi — thử chạy lại'}")
"""))

cells.append(md("## Bước 6 — Khởi động FastAPI Backend"))
cells.append(code("""\
import subprocess, sys, os, time, requests

os.chdir("/content/pumpguard")
env_vars = {k.strip(): v.strip() for line in open("backend/.env").read().splitlines()
            if "=" in line and not line.startswith("#") for k, v in [line.split("=", 1)]}

subprocess.run(["pkill", "-f", "uvicorn"], capture_output=True)
time.sleep(1)

log = open("/tmp/backend.log", "w")
proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.server:app",
                         "--host", "0.0.0.0", "--port", "8000"],
                        stdout=log, stderr=log, env={**os.environ, **env_vars})

print("⏳ Khởi động backend", end="")
for _ in range(20):
    time.sleep(1)
    try:
        r = requests.get("http://localhost:8000/health", timeout=2)
        if r.status_code == 200:
            d = r.json()
            print(f"\\n✅ Backend running!")
            print(f"   AI: {d['ai_provider']} | Key: {'✅ OK' if d['api_key_configured'] else '❌ Chưa set'}")
            break
    except: print(".", end="", flush=True)
else:
    print("\\n❌ Lỗi. Xem log:")
    print(open("/tmp/backend.log").read()[-2000:])
"""))

cells.append(md("""## Bước 7 — Tạo Public URL

Dùng **Cloudflare Tunnel** — không cần tài khoản, không cần token.  
URL có dạng: `https://xxxx.trycloudflare.com`
"""))
cells.append(code("""\
import subprocess, time, re

subprocess.run(["wget", "-q", "-O", "/usr/local/bin/cloudflared",
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"], check=True)
subprocess.run(["chmod", "+x", "/usr/local/bin/cloudflared"])
subprocess.run(["pkill", "-f", "cloudflared"], capture_output=True)
time.sleep(1)

log = open("/tmp/cf.log", "w")
subprocess.Popen(["cloudflared", "tunnel", "--url", "http://localhost:8000", "--no-autoupdate"],
                 stdout=log, stderr=subprocess.STDOUT)

print("⏳ Đợi Cloudflare", end="")
url = None
for _ in range(30):
    time.sleep(2)
    try:
        m = re.search(r'https://[\\w-]+\\.trycloudflare\\.com', open("/tmp/cf.log").read())
        if m: url = m.group(0); break
    except: pass
    print(".", end="", flush=True)

print()
if url:
    print("\\n" + "="*60)
    print("🎉  PUMPGUARD AI ĐANG CHẠY!")
    print("="*60)
    print(f"\\n🌐  Dashboard  →  {url}/dashboard/")
    print(f"🔗  API Docs   →  {url}/docs")
    print("\\n📌  Copy URL Dashboard và mở trên browser!")
    print("="*60)
else:
    print("❌ Không lấy được URL. Chạy lại cell này.")
"""))

cells.append(md("""## Bước 8 — Stream dữ liệu sensor *(Tuỳ chọn)*

Nếu đã upload `data/sensor.csv`, chạy cell này để stream data lên dashboard.  
Nếu không, dùng **Operator Controls** (nút ⚙ góc phải dashboard) để mô phỏng.

> ⚠️ Cell này chạy liên tục — nhấn ⏹ để dừng.
"""))
cells.append(code("""\
import os, sys
os.chdir("/content/pumpguard")

if os.path.exists("data/sensor.csv"):
    print("▶ Streaming data... (nhấn ⏹ để dừng)")
    os.system(f"{sys.executable} scripts/mqtt_replay.py "
              "--csv data/sensor.csv --config data/sensor_groups.json "
              "--start-at-anomaly --compression 360")
else:
    print("ℹ️  Không có sensor.csv. Dùng Operator Controls trên dashboard:")
    print("   Nút ⚙ góc phải → ⚠ Simulate Anomaly hoặc 🔴 Simulate Critical")
"""))

cells.append(md("""---
## 🛠 Troubleshooting

```python
# Xem log backend
print(open("/tmp/backend.log").read()[-3000:])

# Restart sau khi Colab timeout → chạy lại Bước 5, 6, 7
```

| Vấn đề | Giải pháp |
|--------|----------|
| AI chỉ hiện `[MOCK]` | Chạy lại Bước 4 với key thật |
| Backend không start | Kiểm tra file đã upload đúng chưa (Bước 2) |
| Cloudflare không lên | Chạy lại Bước 7 |
| Colab timeout | Chạy lại Bước 5 → 6 → 7 |
"""))

nb = {
    "nbformat": 4, "nbformat_minor": 0,
    "metadata": {
        "colab": {"provenance": [], "name": "PumpGuard_Colab.ipynb"},
        "kernelspec": {"name": "python3", "display_name": "Python 3"},
    },
    "cells": cells,
}

out = "/Users/hoamai/Documents/Claude/Projects/IOT/pump-iot-demo/notebooks/PumpGuard_Colab.ipynb"
with open(out, "w", encoding="utf-8") as f:
    json.dump(nb, f, ensure_ascii=False, indent=1)
print(f"✅ Notebook tạo xong: {len(cells)} cells")
