# 🚀 PumpGuard AI — Deployment Guide

> The project consists of 3 services: **FastAPI backend** (Python + WebSocket) · **Mosquitto MQTT broker** · **Node-RED**
> Docker Compose is pre-configured at `docker/docker-compose.yml`

---

## ⚡ Option 1: Railway (Recommended)

**Pros:** $5/month free credit, solid WebSocket support, 1-click deploy from GitHub, supports multiple services.

### Step 1 — Update docker-compose for your AI provider

Add your API key to the `environment` section of the `backend` service in `docker/docker-compose.yml`:

```yaml
environment:
  - AI_PROVIDER=${AI_PROVIDER:-gemini}
  - GEMINI_API_KEY=${GEMINI_API_KEY}
  - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
  - OPENAI_API_KEY=${OPENAI_API_KEY:-}
  - MQTT_HOST=mosquitto
  - MQTT_PORT=1883
  - RESEND_API_KEY=${RESEND_API_KEY:-}
  - ALERT_TO=${ALERT_TO:-}
```

### Step 2 — Create `railway.toml` at the repo root

```toml
[build]
dockerfilePath = "docker/Dockerfile.backend"

[deploy]
startCommand = "uvicorn backend.server:app --host 0.0.0.0 --port 8000"
healthcheckPath = "/health"
restartPolicyType = "ON_FAILURE"
```

### Step 3 — Deploy

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login and initialise
railway login
railway init

# Set environment variables
railway variables set AI_PROVIDER=gemini
railway variables set GEMINI_API_KEY=AIzaSy-xxxxx
railway variables set RESEND_API_KEY=re_xxxxx
railway variables set ALERT_TO=your@email.com
railway variables set MQTT_HOST=localhost

# Deploy
railway up
```

Dashboard: `https://<project-name>.up.railway.app/dashboard/`

> **MQTT note:** If you only need the AI demo — use the **Operator Controls** (▶ Normal / ⚠ Simulate Anomaly) on the dashboard; no real MQTT connection required.

---

## 🟢 Option 2: Render (Free tier)

**Pros:** Completely free, easy to set up.
**Cons:** Sleeps after 15 minutes idle → ~30 s cold start on the next request.

### Step 1 — Create a `Dockerfile` at the repo root

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./backend/
COPY dashboard/ ./dashboard/
EXPOSE 8000
CMD ["uvicorn", "backend.server:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Step 2 — Create a Web Service on Render

1. Go to [render.com](https://render.com) → **New** → **Web Service**
2. Connect your GitHub repo
3. Runtime: **Docker**
4. Fill in Environment Variables:

| Key | Value |
|-----|-------|
| `AI_PROVIDER` | `gemini` |
| `GEMINI_API_KEY` | `AIzaSy-xxxxx` |
| `MQTT_HOST` | `localhost` |
| `MQTT_PORT` | `1883` |
| `RESEND_API_KEY` | `re_xxxxx` |
| `ALERT_TO` | `your@email.com` |

5. Click **Deploy**

Dashboard: `https://pump-iot-demo.onrender.com/dashboard/`

---

## 🖥 Option 3: VPS / AWS EC2 (Full control — real MQTT)

**Best option if you need real MQTT + Node-RED + full demo.**

### Step 1 — Provision a server

- **AWS EC2 free tier:** t2.micro, Ubuntu 22.04, open ports 22/8000/1883/1880
- **DigitalOcean:** $6/month Droplet, Ubuntu 22.04

### Step 2 — Install Docker and clone the repo

```bash
ssh ubuntu@<server-ip>

# Install Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker

# Clone repo
git clone https://github.com/hoamx2602/demo-iot.git
cd demo-iot
```

### Step 3 — Create `.env` on the server

```bash
cat > backend/.env << 'EOF'
AI_PROVIDER=gemini
GEMINI_API_KEY=AIzaSy-xxxxx
MQTT_HOST=mosquitto
MQTT_PORT=1883
RESEND_API_KEY=re_xxxxx
ALERT_TO=your@email.com
EOF
```

### Step 4 — Run Docker Compose

```bash
cd docker
docker compose --env-file ../backend/.env up -d

# Verify
docker compose ps
docker compose logs backend -f
```

### Step 5 — Access the services

| Service | URL |
|---------|-----|
| Dashboard | `http://<server-ip>:8000/dashboard/` |
| Node-RED | `http://<server-ip>:1880` |
| Health check | `http://<server-ip>:8000/health` |

### Step 6 (Optional) — Custom domain + HTTPS

```bash
sudo apt install -y nginx certbot python3-certbot-nginx

sudo tee /etc/nginx/sites-available/pumpguard << 'EOF'
server {
    listen 80;
    server_name yourdomain.com;
    location / {
        proxy_pass http://localhost:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
}
EOF

sudo ln -s /etc/nginx/sites-available/pumpguard /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d yourdomain.com
```

---

## 📋 Quick Comparison

| | Railway | Render | VPS/EC2 |
|---|---|---|---|
| **Cost** | $5 credit/month | Free | $0–6/month |
| **Real MQTT** | ✅ internal | ❌ | ✅ |
| **WebSocket** | ✅ | ✅ | ✅ |
| **Node-RED** | ✅ | ❌ | ✅ |
| **Difficulty** | ⭐ Easy | ⭐ Easy | ⭐⭐⭐ |
| **Best for** | Quick demo | Lightweight demo | Production |

---

## ⚠️ Important Notes

1. **WebSocket over HTTPS:** The dashboard uses `ws://` — if deploying with HTTPS you must switch to `wss://` in `index.html`:
   ```js
   const WS_URL = `wss://${location.hostname}/ws`;  // remove :8000 if using nginx
   ```
2. **API key security:** Always set your API key via the platform's environment variables — never commit it to Git.
3. **AI-only demo:** If you don't have a real MQTT source, **Operator Controls** on the dashboard are fully functional without any MQTT connection.
