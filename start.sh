#!/bin/bash
# ── start.sh — Khởi động toàn bộ PumpGuard AI ────────────────────────────────
# Dùng cho: GitHub Codespaces, local Mac/Linux
# Usage: bash start.sh [--no-replay]
# ─────────────────────────────────────────────────────────────────────────────

set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

NO_REPLAY=0
for arg in "$@"; do [[ "$arg" == "--no-replay" ]] && NO_REPLAY=1; done

# ── Màu sắc output ────────────────────────────────────────────────────────────
GRN='\033[0;32m'; YLW='\033[1;33m'; CYN='\033[0;36m'; RST='\033[0m'
info()    { echo -e "${CYN}[INFO]${RST}  $*"; }
success() { echo -e "${GRN}[OK]${RST}    $*"; }
warn()    { echo -e "${YLW}[WARN]${RST}  $*"; }

echo ""
echo -e "${CYN}╔══════════════════════════════════════════╗${RST}"
echo -e "${CYN}║   PumpGuard AI — Startup Script          ║${RST}"
echo -e "${CYN}╚══════════════════════════════════════════╝${RST}"
echo ""

# ── Kiểm tra .env ─────────────────────────────────────────────────────────────
if [ ! -f backend/.env ]; then
  warn ".env chưa tồn tại — tạo từ template..."
  cp backend/.env.example backend/.env
  echo ""
  echo -e "${YLW}⚠️  Hãy điền API key vào backend/.env trước khi dùng AI:${RST}"
  echo "   GEMINI_API_KEY=AIzaSy-xxxxxx"
  echo "   (Lấy miễn phí tại: https://aistudio.google.com/apikey)"
  echo ""
fi

# ── Start Mosquitto MQTT ──────────────────────────────────────────────────────
info "Khởi động Mosquitto MQTT broker (port 1883)..."
pkill -f "mosquitto" 2>/dev/null || true
sleep 0.5

if command -v mosquitto &>/dev/null; then
  mosquitto -p 1883 -d --log-type none 2>/dev/null || mosquitto -p 1883 &
  sleep 1
  success "MQTT broker running on port 1883"
else
  warn "Mosquitto chưa được cài. Thử cài: sudo apt-get install mosquitto"
  warn "Backend vẫn chạy nhưng MQTT replay sẽ không hoạt động."
fi

# ── Start FastAPI Backend ─────────────────────────────────────────────────────
info "Khởi động FastAPI backend (port 8000)..."
pkill -f "uvicorn" 2>/dev/null || true
sleep 0.5

# Load env vars
set -a; source backend/.env; set +a

nohup uvicorn backend.server:app --host 0.0.0.0 --port 8000 \
  > /tmp/pumpguard-backend.log 2>&1 &
BACKEND_PID=$!
sleep 2

# Kiểm tra backend đã lên chưa
if curl -s http://localhost:8000/health > /dev/null 2>&1; then
  success "Backend running (PID $BACKEND_PID)"
else
  warn "Backend chưa sẵn sàng, xem log: tail -f /tmp/pumpguard-backend.log"
fi

# ── In URL Dashboard ──────────────────────────────────────────────────────────
echo ""
echo -e "${GRN}╔══════════════════════════════════════════╗${RST}"
echo -e "${GRN}║  🚀 PumpGuard AI đang chạy!              ║${RST}"
echo -e "${GRN}╠══════════════════════════════════════════╣${RST}"
echo -e "${GRN}║  Dashboard: http://localhost:8000/dashboard/ ║${RST}"
echo -e "${GRN}║  API Docs:  http://localhost:8000/docs       ║${RST}"
echo -e "${GRN}╚══════════════════════════════════════════╝${RST}"

# Nếu đang chạy trên Codespaces → gợi ý dùng port forwarding URL
if [ -n "$CODESPACE_NAME" ]; then
  echo ""
  echo -e "${CYN}📡 Codespaces URL:${RST}"
  echo "   https://${CODESPACE_NAME}-8000.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}/dashboard/"
fi

echo ""

# ── Start MQTT Replay (tuỳ chọn) ─────────────────────────────────────────────
if [ "$NO_REPLAY" -eq 0 ] && [ -f data/sensor.csv ] && [ -f data/sensor_groups.json ]; then
  info "Khởi động MQTT data replay (data/sensor.csv)..."
  echo "   Nhấn Ctrl+C để dừng replay"
  echo ""
  python scripts/mqtt_replay.py \
    --csv data/sensor.csv \
    --config data/sensor_groups.json \
    --start-at-anomaly \
    --compression 360
else
  if [ "$NO_REPLAY" -eq 1 ]; then
    info "Replay tắt (--no-replay). Dashboard dùng Operator Controls."
  else
    warn "Không tìm thấy data/sensor.csv — dùng Operator Controls trên dashboard."
  fi
  echo ""
  echo "Để chạy replay thủ công:"
  echo "  python scripts/mqtt_replay.py --csv data/sensor.csv --config data/sensor_groups.json"
  echo ""
  echo "Nhấn Ctrl+C để thoát."
  wait
fi
