"""Generate PumpGuard_Colab.ipynb from code blocks (legacy helper script)."""
import json, os, re

def md(src): return {"cell_type":"markdown","metadata":{},"source":[src]}
def code(src): return {"cell_type":"code","metadata":{},"source":[src],"execution_count":None,"outputs":[]}

cells = []

cells.append(md("""# 🏭 PumpGuard AI — Setup Notebook

> This notebook walks you through the setup steps from **docs/WORKSHOP.md**.
> **Requirement:** Upload all code files to Colab as described in WORKSHOP.md before running.

| Step | Description |
|------|-------------|
| 1 | Create directory structure |
| 2 | Verify uploaded files |
| 3 | Install dependencies |
| 4 | Configure API Key |
| 5 | Start MQTT broker |
| 6 | Start Backend |
| 7 | Create Public URL (Cloudflare) |
| 8 | Stream sensor data (optional) |
"""))

cells.append(md("## Step 1 — Create directory structure"))
cells.append(code("""\
import os
for folder in ['/content/pumpguard/backend', '/content/pumpguard/dashboard',
               '/content/pumpguard/data', '/content/pumpguard/scripts']:
    os.makedirs(folder, exist_ok=True)
print("✅ Directories created. Now upload files as described in WORKSHOP.md Step 3.")
print()
print("   /content/pumpguard/")
print("     ├── backend/    ← server.py, requirements.txt")
print("     ├── dashboard/  ← index.html")
print("     ├── data/       ← sensor_groups.json (+ sensor.csv if available)")
print("     └── scripts/    ← mqtt_replay.py")
"""))

cells.append(md("## Step 2 — Verify uploaded files"))
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
    print(f"{'✅' if ok else '❌ MISSING'}  {f.replace('/content/pumpguard/', '')}")
    if not ok: all_ok = False
print()
print("✅ All files present!" if all_ok else "❌ Missing files — upload them before continuing.")
"""))

cells.append(md("## Step 3 — Install dependencies"))
cells.append(code("""\
import subprocess, sys
print("📦 Installing Python packages...")
subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                "-r", "/content/pumpguard/backend/requirements.txt"], check=True)
print("🦟 Installing Mosquitto...")
subprocess.run(["apt-get", "install", "-y", "-q", "mosquitto"], check=True, capture_output=True)
print("✅ All dependencies installed!")
"""))

cells.append(md("""## Step 4 — Configure Groq API Key

Get a **free** key at: https://console.groq.com → API Keys
Fill in `GROQ_API_KEY` below and run the cell.
"""))
cells.append(code("""\
GROQ_API_KEY = "gsk_xxxx"   # ← REPLACE WITH YOUR REAL KEY

with open("/content/pumpguard/backend/.env", "w") as f:
    f.write(f"GROQ_API_KEY={GROQ_API_KEY}\\nMQTT_HOST=localhost\\nMQTT_PORT=1883\\n")

if GROQ_API_KEY.startswith("gsk_") and len(GROQ_API_KEY) > 10:
    print(f"✅ API key set: {GROQ_API_KEY[:12]}...")
else:
    print("⚠️  Enter your real Groq API key above!")
"""))

cells.append(md("## Step 5 — Start MQTT Broker"))
cells.append(code("""\
import subprocess, time
with open("/tmp/mosquitto.conf", "w") as f:
    f.write("listener 1883\\nallow_anonymous true\\n")
subprocess.run(["pkill", "-f", "mosquitto"], capture_output=True)
time.sleep(0.5)
proc = subprocess.Popen(["mosquitto", "-c", "/tmp/mosquitto.conf"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
print(f"{'✅ Mosquitto running (PID ' + str(proc.pid) + ') — port 1883' if proc.poll() is None else '❌ Failed — try re-running this cell'}")
"""))

cells.append(md("## Step 6 — Start FastAPI Backend"))
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

print("⏳ Starting backend", end="")
for _ in range(20):
    time.sleep(1)
    try:
        r = requests.get("http://localhost:8000/health", timeout=2)
        if r.status_code == 200:
            d = r.json()
            print(f"\\n✅ Backend running!")
            print(f"   AI: {d['ai_provider']} | Key: {'✅ OK' if d['api_key_configured'] else '❌ Not set'}")
            break
    except: print(".", end="", flush=True)
else:
    print("\\n❌ Failed to start. Check log:")
    print(open("/tmp/backend.log").read()[-2000:])
"""))

cells.append(md("""## Step 7 — Create Public URL

Using **Cloudflare Tunnel** — no account or token required.
URL format: `https://xxxx.trycloudflare.com`
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

print("⏳ Waiting for Cloudflare", end="")
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
    print("🎉  PUMPGUARD AI IS LIVE!")
    print("="*60)
    print(f"\\n🌐  Dashboard  →  {url}/dashboard/")
    print(f"🔗  API Docs   →  {url}/docs")
    print("\\n📌  Copy the Dashboard URL and open it in your browser!")
    print("="*60)
else:
    print("❌ Could not get URL. Re-run this cell.")
"""))

cells.append(md("""## Step 8 — Stream sensor data *(Optional)*

If you have uploaded `data/sensor.csv`, run this cell to stream data to the dashboard.
Otherwise use the **Operator Controls** (⚙ button, top-right of the dashboard) to simulate.

> ⚠️ This cell runs continuously — press ⏹ to stop.
"""))
cells.append(code("""\
import os, sys
os.chdir("/content/pumpguard")

if os.path.exists("data/sensor.csv"):
    print("▶ Streaming data... (press ⏹ to stop)")
    os.system(f"{sys.executable} scripts/mqtt_replay.py "
              "--csv data/sensor.csv --config data/sensor_groups.json "
              "--start-at-anomaly --compression 360")
else:
    print("ℹ️  No sensor.csv found. Use Operator Controls on the dashboard:")
    print("   ⚙ button → ⚠ Simulate Anomaly  or  🔴 Simulate Critical")
"""))

cells.append(md("""---
## 🛠 Troubleshooting

```python
# View backend log
print(open("/tmp/backend.log").read()[-3000:])

# After a Colab timeout → re-run Steps 5, 6, 7
```

| Problem | Solution |
|---------|----------|
| AI shows `[MOCK]` | Re-run Step 4 with a real API key |
| Backend fails to start | Verify files are uploaded correctly (Step 2) |
| Cloudflare URL not available | Re-run Step 7 |
| Colab session expired | Re-run Steps 5 → 6 → 7 |
"""))

nb = {
    "nbformat": 4, "nbformat_minor": 0,
    "metadata": {
        "colab": {"provenance": [], "name": "PumpGuard_Colab.ipynb"},
        "kernelspec": {"name": "python3", "display_name": "Python 3"},
    },
    "cells": cells,
}

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "PumpGuard_Colab.ipynb")
with open(out, "w", encoding="utf-8") as f:
    json.dump(nb, f, ensure_ascii=False, indent=1)
print(f"✅ Notebook generated: {len(cells)} cells → {out}")
