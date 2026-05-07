# 🚀 Hướng dẫn Deploy PumpGuard AI

> Project gồm 3 service: **FastAPI backend** (Python + WebSocket) · **Mosquitto MQTT broker** · **Node-RED**  
> Docker Compose đã có sẵn tại `docker/docker-compose.yml`

---

## ⚡ Option 1: Railway (Khuyến nghị)

**Ưu điểm:** Free tier $5/tháng credit, hỗ trợ WebSocket tốt, deploy từ GitHub 1 click, hỗ trợ nhiều service.

### Bước 1 — Cập nhật docker-compose cho Gemini

Thêm `GEMINI_API_KEY` vào `environment` của service `backend` trong `docker/docker-compose.yml`:

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

### Bước 2 — Tạo `railway.toml` ở root

```toml
[build]
dockerfilePath = "docker/Dockerfile.backend"

[deploy]
startCommand = "uvicorn backend.server:app --host 0.0.0.0 --port 8000"
healthcheckPath = "/health"
restartPolicyType = "ON_FAILURE"
```

### Bước 3 — Deploy

```bash
# Cài Railway CLI
npm install -g @railway/cli

# Login và init
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

Dashboard: `https://<tên-project>.up.railway.app/dashboard/`

> **Lưu ý MQTT:** Nếu chỉ demo AI — dùng **Operator Controls** (▶ Normal / ⚠ Simulate Anomaly) trên dashboard, không cần MQTT thật.

---

## 🟢 Option 2: Render (Free tier)

**Ưu điểm:** Free hoàn toàn, dễ dùng.  
**Nhược:** Ngủ sau 15 phút idle → cold start ~30s khi có request mới.

### Bước 1 — Tạo `Dockerfile` ở root repo

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

### Bước 2 — Tạo Web Service trên Render

1. Vào [render.com](https://render.com) → **New** → **Web Service**
2. Connect GitHub repo `hoamx2602/demo-iot`
3. Runtime: **Docker**
4. Điền Environment Variables:

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

## 🖥 Option 3: VPS / AWS EC2 (Full control — MQTT thật)

**Tốt nhất nếu cần MQTT thật + Node-RED + demo đầy đủ.**

### Bước 1 — Tạo server

- **AWS EC2 free tier:** t2.micro, Ubuntu 22.04, mở port 22/8000/1883/1880
- **DigitalOcean:** Droplet $6/tháng, Ubuntu 22.04

### Bước 2 — Cài Docker và clone repo

```bash
ssh ubuntu@<server-ip>

# Cài Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker

# Clone repo
git clone https://github.com/hoamx2602/demo-iot.git
cd demo-iot
```

### Bước 3 — Tạo `.env` trên server

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

### Bước 4 — Chạy Docker Compose

```bash
cd docker
docker compose --env-file ../backend/.env up -d

# Kiểm tra
docker compose ps
docker compose logs backend -f
```

### Bước 5 — Truy cập

| Service | URL |
|---------|-----|
| Dashboard | `http://<server-ip>:8000/dashboard/` |
| Node-RED | `http://<server-ip>:1880` |
| Health check | `http://<server-ip>:8000/health` |

### Bước 6 (Tuỳ chọn) — Domain + HTTPS

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

## 📋 So sánh nhanh

| | Railway | Render | VPS/EC2 |
|---|---|---|---|
| **Giá** | $5 credit/tháng | Free | $0–6/tháng |
| **MQTT thật** | ✅ internal | ❌ | ✅ |
| **WebSocket** | ✅ | ✅ | ✅ |
| **Node-RED** | ✅ | ❌ | ✅ |
| **Độ khó** | ⭐ Dễ | ⭐ Dễ | ⭐⭐⭐ |
| **Tốt cho** | Demo nhanh | Demo nhẹ | Production |

---

## ⚠️ Lưu ý quan trọng

1. **WebSocket HTTPS:** Dashboard dùng `ws://` — nếu deploy HTTPS phải đổi thành `wss://` trong `index.html` dòng 725:
   ```js
   const WS_URL = `wss://${location.hostname}/ws`;  // bỏ :8000 nếu dùng nginx
   ```
2. **Gemini API key** phải điền vào env vars của platform, không commit vào git
3. Nếu chỉ demo AI mà không có MQTT: **Operator Controls** vẫn hoạt động đầy đủ
