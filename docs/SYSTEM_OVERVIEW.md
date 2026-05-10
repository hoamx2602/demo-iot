# PumpGuard AI — Mô tả hệ thống & Câu hỏi thảo luận

> Tài liệu tham khảo cho giảng viên và học viên workshop.

---

## Phần A — Vai trò của từng thành phần trong kiến trúc IoT

### 1. Sensor Simulator (`mqtt_replay.py`)
**Vai trò: Tầng Perception — Nguồn dữ liệu**

Trong triển khai thực tế, tầng này là các sensor vật lý gắn trên máy bơm (đầu dò rung động, cặp nhiệt điện, cảm biến áp suất, đồng hồ đo lưu lượng). Trong workshop này, simulator phát lại 220.000 dòng dữ liệu sensor công nghiệp thực từ file CSV ở tốc độ nén 360×.

**Tại sao quan trọng trong IoT:**
Tầng perception là nơi thế giới vật lý trở thành dữ liệu số. Mọi hệ thống IoT đều bắt đầu từ đây — không có dữ liệu sensor đáng tin cậy và tần suất cao, mọi xử lý phía sau đều vô nghĩa. Simulator tái hiện trung thực quá trình xuống cấp của một lần hỏng máy bơm thực tế.

**Các lựa chọn thiết kế chính:**
- Publish mỗi ~167 ms → tần suất lấy mẫu 6 Hz
- Chuyển đổi giữa chế độ `NORMAL` và `BROKEN` để mô phỏng sự xuống cấp
- Dùng MQTT để tách biệt hoàn toàn nguồn phát khỏi các bên tiêu thụ

---

### 2. Mosquitto MQTT Broker
**Vai trò: Tầng Network & Transport — Message Bus**

Mosquitto là trung gian nhận message từ publisher và định tuyến đến tất cả subscriber. Publisher không biết ai đang nhận dữ liệu của mình, và subscriber không biết dữ liệu đến từ đâu — họ chỉ cần biết **tên topic** (`pump/sensors`).

**Tại sao quan trọng trong IoT:**
MQTT được thiết kế cho mạng có hạn chế (băng thông thấp, độ trễ cao, kết nối không ổn định). Mô hình publish/subscribe tự nhiên hỗ trợ phân phối **một-đến-nhiều** — một lần publish từ sensor đến đồng thời Node-RED, FastAPI backend và mọi subscriber khác mà không cần làm thêm gì.

**Tính năng chính trong hệ thống:**
- QoS 1: đảm bảo giao hàng ít nhất một lần — không mất dữ liệu kể cả khi mất kết nối ngắn
- Kết nối liên tục: broker duy trì session, giảm chi phí kết nối lại
- Phân cấp topic: `pump/sensors` có thể mở rộng (ví dụ: `pump/alerts`, `pump/control`)

---

### 3. Node-RED
**Vai trò: Tầng Processing — Edge Intelligence**

Node-RED nằm giữa luồng sensor thô và backend. Nó subscribe MQTT, tích lũy **rolling buffer** 60 readings (~30 giây lịch sử), tính xu hướng thống kê, và quyết định có trigger cảnh báo AI hay không.

**Tại sao quan trọng trong IoT:**
Trong IoT công nghiệp, "edge processing" có nghĩa là tính toán anomaly score gần nguồn dữ liệu — trước khi gửi bất cứ thứ gì lên cloud. Điều này giảm băng thông, độ trễ và chi phí cloud. Node-RED biến logic này thành **trực quan và có thể kiểm tra**: mỗi bước xử lý là một node bạn có thể quan sát và debug riêng lẻ.

**Những gì Node-RED tính toán:**
| Chỉ số | Mục đích |
|--------|---------|
| Rolling average | Làm mịn nhiễu sensor |
| Linear slope | Phát hiện xu hướng tăng/giảm |
| Standard deviation | Đo độ ổn định tín hiệu |
| `anomaly_score` (0–1) | Chỉ số sức khỏe tổng hợp |

**Quy tắc quyết định:** Nếu `anomaly_detected = true` (bất kỳ sensor nào ở trạng thái WARNING hoặc CRITICAL dựa trên xu hướng), Node-RED throttle xuống tối đa 1 lần mỗi 60 giây và gửi `AlertRequest` đến endpoint `/alert` của FastAPI.

---

### 4. FastAPI Backend (`server.py`)
**Vai trò: Tầng Processing — Data Hub & API Gateway**

Backend là hệ thần kinh trung ương của ứng dụng. Nó:
- Cầu nối MQTT với WebSocket (relay sensor thô)
- Expose REST endpoint cho Node-RED (`/alert`, `/analyze`) và dashboard (`/control`, `/simulate/inject`)
- Quản lý tất cả kết nối WebSocket client với per-connection write lock
- Rate-limit broadcast ở 2 Hz để tránh backlog trên kết nối chậm

**Tại sao quan trọng trong IoT:**
Backend trong IoT đóng vai trò **điểm tổng hợp và chuẩn hóa** — chuyển đổi dữ liệu không đồng nhất (MQTT binary, REST JSON) thành một luồng duy nhất mà dashboard và AI service có thể tiêu thụ. WebSocket hub cho phép push real-time mà không cần polling.

**Chi tiết kỹ thuật quan trọng:**
- `_broadcast_loop` ở 2 Hz: tách biệt tốc độ ingest khỏi tốc độ gửi — tránh freeze trên ngrok/mobile
- `_forced_state` lock: cho phép presenter override trạng thái dashboard trong demo mà không bị Node-RED ghi đè
- `_ai_semaphore(2)`: giới hạn 2 Groq call đồng thời — tránh cạn kiệt quota và rò rỉ file descriptor
- Middleware log request chậm: ghi log endpoint nào mất > 1 giây cho mục đích chẩn đoán

---

### 5. Groq LLM — `llama-3.3-70b-versatile`
**Vai trò: Tầng AI Analytics & Decision**

Khi Node-RED phát hiện anomaly, backend gửi một snapshot sensor có cấu trúc đến Groq API. Model trả về **JSON có thể hiểu được về mặt kinh doanh**: mức độ rủi ro, giả thuyết nguyên nhân gốc, danh sách hành động bảo trì được xếp hạng, ước tính giờ đến khi hỏng, và tiết kiệm chi phí nếu can thiệp sớm.

**Tại sao quan trọng trong IoT:**
Ngưỡng sensor thô cho bạn biết *rằng* có gì đó sai. AI cho bạn biết *tại sao*, *mức độ khẩn cấp*, và *cần làm gì*. Điều này chuyển hệ thống từ cảnh báo phản ứng sang **bảo trì dự đoán** — ứng dụng có giá trị cao nhất trong IoT công nghiệp.

**Tại sao chọn Groq:**
- LPU (Language Processing Unit): suy luận nhanh hơn ~10× so với provider dùng GPU
- Free tier: 30 RPM, 14.400 RPD — đủ cho workshop nhiều người dùng
- `response_format: json_object`: đảm bảo JSON hợp lệ, không bao giờ lỗi parse
- `llama-3.3-70b-versatile`: lý luận kỹ thuật tốt, hiểu ngữ cảnh kỹ thuật

---

### 6. Real-time Dashboard (`index.html`)
**Vai trò: Tầng Application — Human-Machine Interface**

Single-page application kết nối với backend qua WebSocket và hiển thị dữ liệu sensor trực tiếp, health score, khuyến nghị AI và timeline dự đoán hỏng hóc. Không dùng framework — HTML/CSS/JS thuần túy để có thể deploy mà không cần phụ thuộc.

**Tại sao quan trọng trong IoT:**
Dashboard là cửa sổ của operator nhìn vào máy vật lý. Trong bảo trì dự đoán, giá trị nằm ở **thời gian để hành động**: panel khuyến nghị AI, đếm ngược đến thời điểm hỏng dự kiến, và ước tính tiết kiệm chi phí bảo trì đều được thiết kế để operator đưa ra quyết định trong vài giây.

**Các thành phần UI chính:**
| Thành phần | Mục đích |
|-----------|---------|
| Health Ring | Sức khỏe máy tổng thể (0–100%) nhìn nhanh |
| Sensor Gauges | Readings real-time theo nhóm với dải ngưỡng |
| Failure Prediction Timeline | Hành trình 4 mốc: Start → Anomaly → Now → Est. Failure |
| AI Recommendation Panel | Mức rủi ro, hành động, tiết kiệm ước tính |
| Operator Controls | Override trạng thái của presenter (Normal / Warning / Critical) |

---

### 7. Resend Email Alert
**Vai trò: Tầng Application — Out-of-band Notification**

Khi endpoint `/alert` được gọi, sau khi phân tích AI hoàn thành, backend gửi email HTML qua Resend API. Email bao gồm snapshot sensor, mức rủi ro AI, hành động được khuyến nghị và link đến dashboard.

**Tại sao quan trọng trong IoT:**
Dashboard chỉ hữu ích khi có người đang nhìn vào nó. Email (và trong production: SMS, push notification, tích hợp SCADA) đảm bảo anomaly kích hoạt phản hồi ngay cả khi không có operator nào đang đăng nhập. Điều này hoàn thiện vòng lặp giữa **phát hiện** và **hành động của con người**.

---

## Phần B — Câu hỏi Kỹ thuật & Thảo luận

### 🔧 Kiến trúc IoT & Giao thức

1. **Tại sao hệ thống dùng MQTT thay vì HTTP REST để truyền dữ liệu sensor?**
   *(Gợi ý: nghĩ về mô hình kết nối, overhead header, và khả năng fan-out)*

2. **Điều gì xảy ra với hệ thống nếu MQTT broker bị down trong 10 giây? Những thành phần nào bị ảnh hưởng và theo cách nào?**

3. **QoS 1 đảm bảo giao hàng "ít nhất một lần". Trong tình huống nào điều này có thể gây ra vấn đề cho hệ thống này? Bạn xử lý message trùng lặp như thế nào?**

4. **Simulator publish ở 6 Hz (167 ms/lần). Nếu một máy bơm thực có 52 sensor mỗi sensor lấy mẫu ở 100 Hz, kiến trúc cần thay đổi gì?**

5. **Hiện tại cả MQTT bridge và Node-RED đều subscribe vào `pump/sensors`. Đánh đổi của thiết kế này là gì so với chỉ có một subscriber duy nhất rồi fan-out nội bộ?**

6. **Hệ thống dùng rolling buffer 60 readings. Làm thế nào bạn quyết định kích thước buffer phù hợp cho một use case IoT khác (ví dụ: sensor nhiệt độ trong kho lạnh)?**

---

### ⚙️ Thiết kế hệ thống & Kỹ thuật

7. **Broadcast loop chạy ở 2 Hz bất kể Node-RED gửi data nhanh thế nào. Tại sao rate-limiting này quan trọng? Điều gì có thể xảy ra nếu không có nó trên kết nối chậm như ngrok?**

8. **Endpoint `/control/{state}` khóa `_forced_state` mà Node-RED không thể ghi đè. Tại sao lock này cần thiết cho một demo trực tiếp? Rủi ro gì nếu bỏ nó?**

9. **Groq client được tạo một lần lúc startup dưới dạng singleton (`_groq_client`). Điều này giải quyết vấn đề gì so với tạo client mới cho mỗi request?**

10. **Hệ thống dùng `asyncio.Semaphore(2)` để giới hạn AI call đồng thời. Nếu 30 học viên cùng trigger anomaly một lúc, điều gì xảy ra? Bạn scale hệ thống này như thế nào?**

11. **Tại sao Resend email call được wrap trong `asyncio.to_thread()`? Điều gì xảy ra với WebSocket broadcast nếu nó chạy đồng bộ trong event loop?**

12. **Timestamp `_nodered_last_inject` ngăn MQTT bridge broadcast trong 5 giây sau khi Node-RED post. Mục đích là gì, và edge case nào có thể khiến dashboard không hiển thị dữ liệu?**

---

### 🤖 AI & Machine Learning

13. **LLM nhận JSON snapshot sensor có cấu trúc, không phải raw time-series. Thông tin gì bị mất trong abstraction này? Rolling buffer trong Node-RED bù đắp điều này như thế nào?**

14. **System prompt hướng dẫn LLM trả về JSON với `risk_level`, `estimated_hours_to_failure`, và `recommended_actions`. Rủi ro gì khi tin vào ước tính số của LLM (như số giờ đến khi hỏng) trong bối cảnh an toàn quan trọng?**

15. **AI response bao gồm `confidence` (0–1). Operator nên hiểu thế nào khi thấy risk level `HIGH` với `confidence: 0.45`? Thay đổi UI nào sẽ truyền đạt sự không chắc chắn này tốt hơn?**

16. **Hệ thống throttle AI call xuống 1 lần mỗi 60 giây. Trong một sự cố kéo dài, readings sensor có thể thay đổi đáng kể. Bạn thiết kế chiến lược throttle thông minh hơn như thế nào để kích hoạt re-analysis khi tình huống thực sự thay đổi?**

17. **Node-RED tính `anomaly_score` dùng ngưỡng thủ công và linear slope. Giới hạn của approach dựa trên quy tắc này là gì so với trained ML model (ví dụ: Isolation Forest, LSTM)? Bạn cần gì để train model cho máy bơm này?**

18. **LLM được cho biết snapshot sensor hiện tại nhưng không có ngữ cảnh lịch sử ngoài những gì Node-RED tóm tắt. Việc truy cập toàn bộ lịch sử 60 readings sẽ thay đổi chất lượng khuyến nghị AI như thế nào?**

---

### 📊 Kinh doanh & Use Case IoT

19. **AI ước tính "tiết kiệm chi phí từ bảo trì có kế hoạch vs sửa chữa khẩn cấp". Bạn cần dữ liệu gì để ước tính này chính xác với một nhà máy thực? Chi phí ẩn nào LLM có thể bỏ sót?**

20. **Hệ thống hiện tại gửi một email cảnh báo mỗi anomaly (với cooldown 60 giây). Trong nhà máy thực với 200 máy bơm, bạn quản lý "alert fatigue" như thế nào? Thêm cơ chế lọc hoặc ưu tiên nào?**

21. **Hệ thống hiện không lưu trữ dữ liệu — sensor readings và phân tích AI bị mất khi server restart. Bạn thêm gì để hỗ trợ phân tích xu hướng theo tuần/tháng? Chọn database nào và tại sao?**

22. **MQTT topic là chuỗi phẳng (`pump/sensors`). Nếu mở rộng cho nhà máy có 50 máy, bạn thiết kế phân cấp topic như thế nào? Node-RED cần thay đổi gì?**

23. **Hệ thống chạy trên một instance Google Colab duy nhất. Hãy xác định 3 single point of failure và đề xuất cách giải quyết từng cái trong deployment production.**

24. **Khách hàng hỏi: "Tôi có thể dùng hệ thống này không có internet không?" (nhà máy air-gapped). Những thành phần nào hoạt động offline? Thành phần nào cần internet? Cần thay đổi gì để vận hành hoàn toàn offline?**

---

### 🔬 Nâng cao / Mở rộng

25. **Dashboard bỏ qua WebSocket sensor update khi local simulator đang chạy (`if (!_simInterval)`). Tại sao? Visual artifact nào xuất hiện nếu không có guard này?**

26. **Thiết kế extension "digital twin" cho hệ thống này: một bản sao mô phỏng của máy bơm chạy song song và dự đoán điều gì sẽ xảy ra trong một giờ tới dựa trên xu hướng hiện tại. Bạn thêm những thành phần nào?**

27. **Phát hiện anomaly hiện tại dựa trên ngưỡng cố định (vibration > 7.0 mm/s = CRITICAL). Máy bơm chạy ở 50% tải có dải giá trị bình thường khác với máy chạy ở 100% tải. Bạn làm cho ngưỡng trở nên context-aware như thế nào?**

28. **Nếu thay Groq/llama-3.3-70b bằng model nhỏ on-device (ví dụ: Phi-3 mini chạy trên Raspberry Pi), bạn chấp nhận đánh đổi gì? Scenarios IoT nào làm cho đánh đổi này xứng đáng?**

---

*Câu hỏi 🔧 phù hợp cho tất cả học viên. Câu hỏi ⚙️ dành cho track phần mềm/hệ thống. Câu hỏi 🤖 dành cho track AI/ML. Câu hỏi 📊 dành cho track kinh doanh/sản phẩm.*
