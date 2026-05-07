#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# setup_nodered.sh — Cài Node-RED và import flow tự động (macOS)
# Usage: bash nodered/setup_nodered.sh
# ─────────────────────────────────────────────────────────────────────────────
set -e

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
FLOWS_FILE="$SCRIPT_DIR/flows.json"
NR_DIR="$HOME/.node-red"

info "Project: $PROJECT_DIR"
info "Flows:   $FLOWS_FILE"

# ── 1. Kiểm tra Node.js ────────────────────────────────────────────────────
if ! command -v node &>/dev/null; then
  warn "Node.js chưa cài. Đang cài qua Homebrew..."
  if ! command -v brew &>/dev/null; then
    error "Homebrew chưa cài. Cài tại: https://brew.sh"
  fi
  brew install node
fi
info "Node.js: $(node --version)"

# ── 2. Cài Node-RED ────────────────────────────────────────────────────────
if ! command -v node-red &>/dev/null; then
  info "Đang cài Node-RED..."
  npm install -g --unsafe-perm node-red
fi
info "Node-RED: $(node-red --version 2>/dev/null | head -1)"

# ── 3. Tạo thư mục .node-red nếu chưa có ──────────────────────────────────
mkdir -p "$NR_DIR"

# ── 4. Backup flow cũ nếu có ───────────────────────────────────────────────
if [ -f "$NR_DIR/flows.json" ]; then
  BACKUP="$NR_DIR/flows.backup.$(date +%Y%m%d_%H%M%S).json"
  warn "Đang backup flow cũ → $BACKUP"
  cp "$NR_DIR/flows.json" "$BACKUP"
fi

# ── 5. Copy flow vào Node-RED ──────────────────────────────────────────────
info "Import flow: $FLOWS_FILE → $NR_DIR/flows.json"
cp "$FLOWS_FILE" "$NR_DIR/flows.json"

# ── 6. Tạo settings.js nếu chưa có (bật CORS cho dashboard) ──────────────
if [ ! -f "$NR_DIR/settings.js" ]; then
  info "Tạo settings.js với CORS enabled..."
  node-red --help > /dev/null 2>&1 || true
  # Copy default settings
  cat > "$NR_DIR/settings.js" << 'SETTINGS'
module.exports = {
  uiPort: process.env.PORT || 1880,
  mqttReconnectTime: 15000,
  serialReconnectTime: 15000,
  debugMaxLength: 1000,
  functionGlobalContext: {},
  exportGlobalContextKeys: false,
  logging: { console: { level: "info", metric: false, audit: false } },
  editorTheme: {
    tours: false,
    header: { title: "PumpGuard AI — Node-RED" }
  },
  // CORS: cho phép dashboard gọi API Node-RED
  httpAdminCors: {
    origin: "*",
    methods: "GET,PUT,POST,DELETE"
  },
  httpNodeCors: {
    origin: "*",
    methods: "GET,PUT,POST,DELETE"
  },
}
SETTINGS
fi

# ── 7. Start Node-RED ──────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════"
echo " ✅ Node-RED đã sẵn sàng!"
echo ""
echo "  Mở trình duyệt: http://localhost:1880"
echo "  Flow 'Pump IoT Demo' đã được import sẵn"
echo ""
echo "  Lưu ý: Đảm bảo Mosquitto đang chạy trước"
echo "  → mosquitto -p 1883 -v"
echo "════════════════════════════════════════════════════════════"
echo ""

read -p "Start Node-RED ngay bây giờ? [Y/n] " answer
answer=${answer:-Y}
if [[ "$answer" =~ ^[Yy]$ ]]; then
  info "Starting Node-RED tại http://localhost:1880 ..."
  node-red
else
  info "Khi muốn start: chạy 'node-red' trong terminal"
fi
