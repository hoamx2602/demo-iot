# PumpGuard AI — Predictive Maintenance Platform
### Industrial Pump Monitoring · Oil & Gas · Powered by Claude AI

---

## Kiến trúc hệ thống

```
┌─────────────────────────────────────────────────────────────────┐
│                        DATA FLOW                                │
│                                                                 │
│  sensor.csv  ──►  mqtt_replay.py  ──►  Mosquitto  ──►  Node-RED│
│  (dataset)        (replay script)     (MQTT broker)   (xử lý)  │
│                                                                 │
│  Node-RED  ──►  FastAPI Backend  ──►  Claude API               │
│             ◄──  (AI analysis)    ◄──  (kết quả)               │
│                       │                                         │
│                   WebSocket                                     │
│                       │                                         │
│               Web Dashboard  ◄──── Operator                    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Vai trò từng thành phần

### 1. `sensor.csv` — Dataset
Dữ liệu thật từ máy bơm công nghiệp (Kaggle Pump Sensor Data):
- 220.320 readings, tần suất 1 phút/lần (~5 tháng vận hành)
- 52 sensor thật: rung động, nhiệt độ, áp suất, lưu lượng
- Nhãn `machine_status`: NORMAL / BROKEN / RECOVERING

### 2. `scripts/analyze_sensors.py` — Phân tích ban đầu
Chạy một lần duy nhất khi setup:
- Đọc CSV, nhóm 52 sensor thành 4 nhóm có ý nghĩa vật lý
- Tính scale factor để ra đơn vị thực tế (mm/s, °C, bar, m³/h)
- Xác định row đầu tiên có dấu hiệu bất thường
- Xuất `data/sensor_groups.json` — config file dùng cho toàn hệ thống

### 3. `scripts/mqtt_replay.py` — Replay Script
Đóng vai thiết bị IoT thật ngoài hiện trường:
- Đọc CSV từng row, tính giá trị đại diện cho mỗi nhóm sensor
- Thêm noise ngẫu nhiên ±0.8% để data trông tự nhiên
- Gửi lên MQTT topic `pump/sensors` mỗi ~0.17 giây (tương đương 1 phút thật)
- Nhận lệnh điều khiển từ topic `pump/control`: PAUSE / RESUME / JUMP:<row>

**Time compression:** 1 giờ dữ liệu thật = 10 giây trong hệ thống.
Dùng `--start-at-anomaly` để bắt đầu ngay từ giai đoạn máy bắt đầu suy giảm.

### 4. Mosquitto — MQTT Broker
Trung tâm nhận/phát message giữa các thành phần:
- Giao thức MQTT (Message Queuing Telemetry Transport) — chuẩn công nghiệp IoT
- Chạy local tại `localhost:1883`
- Không lưu logic, chỉ route message
- Trên production: thay bằng AWS IoT Core hoặc HiveMQ Cloud

### 5. `nodered/flows.json` — Node-RED Flow
Lớp xử lý trung gian, làm 3 việc mà backend không làm:

**Rolling Buffer:** Giữ 60 readings gần nhất (~60 phút thật) trong bộ nhớ.

**Compute Trends:** Với mỗi nhóm sensor, tính:
- `avg_60`: trung bình 60 readings
- `slope`: xu hướng tăng/giảm (linear regression)
- `std_dev`: độ ổn định của sensor
- `rate_of_change`: thay đổi so với reading trước
- `status`: NORMAL / WARNING / CRITICAL

**Throttle & Route:** Chỉ gửi lên AI khi có anomaly, tối đa 1 lần/10 giây — tránh tốn API cost khi máy đang hoạt động bình thường.

### 6. `backend/server.py` — FastAPI Backend
Trung tâm điều phối:
- **MQTT Bridge:** Subscribe `pump/sensors`, đẩy thẳng lên WebSocket cho dashboard
- **POST /analyze:** Nhận snapshot từ Node-RED hoặc dashboard, gọi Claude API
- **WebSocket /ws:** Broadcast real-time đến tất cả client đang mở dashboard
- **Claude/OpenAI integration:** System prompt được tối ưu để output business-friendly

### 7. `dashboard/index.html` — Web Dashboard
Giao diện duy nhất người dùng nhìn vào:

**Tab 1 — Live Sensor Feed:**
Health score ring, 4 sensor cards với sparkline chart, badge NORMAL/WARNING/CRITICAL cập nhật real-time.

**Tab 2 — Machine Health Timeline:**
Trục thời gian từ NORMAL → DEGRADING → (dự báo) FAILURE. Điểm AI phát hiện đầu tiên được đánh dấu. Multi-sensor trend chart 60 readings. Event log.

**Tab 3 — AI Recommendation:**
Risk level + confidence score. Danh sách sensor bất thường + giải thích vật lý. 4 hành động cụ thể có timeline và người chịu trách nhiệm. Cost impact: thiệt hại nếu không làm gì vs chi phí bảo trì có kế hoạch.

---

## Cách start hệ thống

### Thứ tự khởi động (quan trọng)

```bash
# Terminal 1 — MQTT Broker (phải start trước tiên)
mosquitto -p 1883

# Terminal 2 — Backend + WebSocket
cd pump-iot-demo
source venv/bin/activate
uvicorn backend.server:app --host 0.0.0.0 --port 8000

# Terminal 3 — Replay data
cd pump-iot-demo
source venv/bin/activate
python scripts/mqtt_replay.py \
  --csv data/sensor.csv \
  --config data/sensor_groups.json \
  --start-at-anomaly

# Browser — Dashboard
open http://localhost:8000/dashboard/
```

### Kiểm tra nhanh từng thành phần

| Thành phần | Cách kiểm tra |
|---|---|
| Mosquitto | `mosquitto_pub -t test -m hello` — không báo lỗi |
| Backend | http://localhost:8000/health → `{"status":"ok"}` |
| WebSocket | Dashboard badge chuyển xanh "Live" |
| MQTT data | Terminal replay đang in rows |
| Node-RED | http://localhost:1880 — badge "connected" |

---

## Cấu hình API key

```bash
cp backend/.env.example backend/.env
```

Mở `backend/.env`, điền một trong hai:

```env
# Dùng Claude
AI_PROVIDER=claude
ANTHROPIC_API_KEY=sk-ant-...

# Hoặc dùng OpenAI
AI_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

Restart backend sau khi thay đổi.

---

## Script trình bày cho khách hàng

### Chuẩn bị trước (5 phút)

1. Start Mosquitto, backend, replay — **theo thứ tự trên**
2. Replay dùng flag `--start-at-anomaly` để bắt đầu từ 200 rows trước điểm suy giảm
3. Mở dashboard ở tab "Live Sensor Feed"
4. Để chạy 30-60 giây cho chart có data trước khi khách vào phòng

### Phần 1 — Tình trạng bình thường (1-2 phút)

**Mở tab "Live Sensor Feed"**

Chỉ ra:
- Health Score 88%, màu xanh
- Cả 4 sensor badges đều NORMAL
- Sparklines ổn định, không có biến động lớn

Câu nói: *"Đây là máy bơm đang vận hành bình thường. Hệ thống đang đọc 52 sensor thật, nhóm lại thành 4 chỉ số đại diện, cập nhật mỗi giây."*

### Phần 2 — Phát hiện sự cố (2-3 phút)

**Quan sát badges bắt đầu chuyển vàng** (xảy ra tự nhiên khi replay chạy đến giai đoạn suy giảm)

Hoặc chủ động nhấn **"⚠ Simulate Anomaly"** trong Operator Controls.

Chỉ ra:
- Health Score giảm xuống ~50%, màu vàng
- Vibration và Temperature chuyển WARNING
- Sparklines tăng rõ rệt
- Switch sang tab "Machine Health Timeline" — thấy điểm AI Detection được đánh dấu trên trục thời gian

Câu nói: *"Sensor bắt đầu vượt ngưỡng. Hệ thống phát hiện tức thì — không cần chờ đến ca kiểm tra định kỳ."*

### Phần 3 — AI phân tích (3-4 phút)

**Nhấn "🤖 Run AI Analysis"** — đợi 2-3 giây, dashboard tự chuyển sang tab AI Recommendation.

Đi qua từng phần:

**Risk Level + Confidence:** *"AI đánh giá HIGH risk với 87% confidence, dự báo failure trong vòng 18 giờ."*

**Anomalous Sensors:** *"Cụ thể, vibration đang ở 5.8 mm/s trong khi ngưỡng bình thường là 0–4.5. Temperature đồng thời tăng — hai thứ tăng cùng lúc là dấu hiệu điển hình của bearing wear."*

**Recommended Actions:** *"AI đề xuất 4 hành động theo thứ tự ưu tiên, có timeline cụ thể và chỉ định người phụ trách — operator biết ngay phải làm gì."*

**Cost Impact — phần quan trọng nhất:** *"Nếu không làm gì: $524,000 thiệt hại — 48 giờ dừng máy, sửa khẩn cấp, phạt môi trường. Nếu bảo trì có kế hoạch ngay hôm nay: $10,500. Hệ thống đã cứu được $513,500 chỉ từ một cảnh báo sớm."*

### Câu chốt

*"Trước đây, kỹ sư phải đợi máy hỏng mới biết — hoặc kiểm tra định kỳ tốn kém dù máy vẫn tốt. Với IoT + AI, hệ thống cảnh báo trước 18 giờ, đủ thời gian lên lịch bảo trì trong ca thấp điểm, đặt linh kiện, chuẩn bị nhân lực. Unplanned downtime giảm, chi phí bảo trì giảm, tuổi thọ máy tăng."*

---

## Deploy lên AWS

### Option A — EC2 (nhanh nhất)

```bash
# 1. Launch EC2 t3.medium, Ubuntu 22.04
# 2. Mở Security Group: port 22, 1883, 8000, 1880

# 3. Trên EC2:
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker ubuntu

# 4. Copy project lên EC2
scp -r pump-iot-demo ubuntu@<EC2-IP>:~/

# 5. Start
cd ~/pump-iot-demo/docker
cp ../backend/.env.example ../backend/.env
nano ../backend/.env   # điền API key
docker compose up -d

# 6. Replay từ máy local, trỏ đến EC2
MQTT_HOST=<EC2-IP> python scripts/mqtt_replay.py \
  --csv data/sensor.csv \
  --config data/sensor_groups.json \
  --start-at-anomaly
```

Dashboard: `http://<EC2-IP>:8000/dashboard/`

### Option B — Docker local (không cần cài tay)

```bash
cd pump-iot-demo/docker
cp mosquitto.conf.example mosquitto.conf 2>/dev/null || true
docker compose up -d
```

---

## Cấu trúc thư mục

```
pump-iot-demo/
├── data/
│   ├── sensor.csv              ← dataset Kaggle (đặt vào đây)
│   └── sensor_groups.json      ← tự tạo khi chạy analyze_sensors.py
├── scripts/
│   ├── analyze_sensors.py      ← chạy 1 lần lúc setup
│   └── mqtt_replay.py          ← chạy mỗi lần muốn stream data
├── nodered/
│   ├── flows.json              ← import vào Node-RED
│   └── setup_nodered.sh        ← cài + import tự động (macOS)
├── backend/
│   ├── server.py               ← FastAPI + WebSocket + AI
│   ├── requirements.txt
│   └── .env.example            ← copy thành .env, điền API key
├── dashboard/
│   └── index.html              ← mở thẳng trên browser
├── docker/
│   ├── docker-compose.yml      ← deploy tất cả services
│   ├── Dockerfile.backend
│   └── mosquitto.conf
└── README.md
```

---

## Troubleshooting

**Dashboard "Offline"**
→ Backend chưa chạy. Chạy: `uvicorn backend.server:app --port 8000`

**Node-RED "connecting"**
→ Mosquitto chưa chạy. Chạy: `mosquitto -p 1883`

**AI trả về pre-loaded analysis**
→ API key chưa điền. Mở `backend/.env`, điền key, restart backend.

**Replay không thấy data trên dashboard**
→ Kiểm tra thứ tự: Mosquitto → Backend → Replay. Backend phải start trước replay.

**Port 1883 already in use**
→ `lsof -i :1883 | grep LISTEN` rồi kill process đó, hoặc `brew services restart mosquitto`
