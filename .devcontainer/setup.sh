#!/bin/bash
# ── Chạy 1 lần khi tạo Codespace (install deps) ──────────────────────────────
set -e
echo "🔧 Installing Python dependencies..."
pip install -r backend/requirements.txt -q

echo "🦟 Installing Mosquitto MQTT broker..."
sudo apt-get update -qq && sudo apt-get install -y -qq mosquitto

echo "✅ Setup complete!"
echo "👉 Tiếp theo: chạy 'bash start.sh' trong terminal để khởi động hệ thống"
