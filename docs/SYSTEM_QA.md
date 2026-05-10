# PumpGuard AI — Câu hỏi & Đáp án

> Tài liệu dành cho giảng viên. Mỗi đáp án gắn với code/thiết kế thực tế của hệ thống.

---

## 🔧 Kiến trúc IoT & Giao thức

**Q1. Tại sao dùng MQTT thay vì HTTP REST cho sensor data?**

> **Đáp án:** REST hoạt động theo mô hình pull — client phải liên tục gửi request để hỏi "có data mới không?" (polling). Với sensor phát 6 Hz, browser phải gửi 6 HTTP request/giây, mỗi request mang header ~500 byte → lãng phí. MQTT hoạt động theo mô hình push — sensor publish một lần, broker phân phối đến *tất cả* subscriber đồng thời với header chỉ 2 byte. Trong hệ thống này, một lần publish từ `mqtt_replay.py` đến đồng thời `MQTTBridge` (FastAPI) và Node-RED mà không cần thêm bất kỳ code nào.

---

**Q2. Điều gì xảy ra nếu MQTT broker down 10 giây?**

> **Đáp án:** `mqtt_replay.py` sẽ gặp publish error nhưng tiếp tục chạy (paho-mqtt có internal retry). `MQTTBridge` trong `server.py` sẽ kích hoạt `_on_disconnect` → chạy `_reconnect_loop` với backoff 2→4→8→16→30 giây — không crash server. Node-RED sẽ dừng nhận data và flow đứng yên. Dashboard tiếp tục kết nối WebSocket nhưng không nhận sensor_update mới — hiển thị giá trị cuối cùng đã nhận. Sau khi broker phục hồi, hệ thống tự kết nối lại mà không cần restart.

---

**Q3. QoS 1 đảm bảo "at-least-once" — khi nào điều này gây vấn đề?**

> **Đáp án:** Nếu broker xác nhận message nhưng acknowledgement bị mất trên đường về, publisher sẽ gửi lại message đó. Node-RED sẽ xử lý cùng một reading hai lần — rolling buffer sẽ có một dòng lặp, làm slope và std_dev tính sai nhẹ. Trong hệ thống này ít ảnh hưởng vì chúng ta quan tâm xu hướng 60 readings, không phải từng điểm. Giải pháp production: thêm `msg_id` vào payload và Node-RED dedup theo ID trước khi push vào buffer.

---

**Q4. Nếu có 52 sensor × 100 Hz, kiến trúc thay đổi gì?**

> **Đáp án:** 52 × 100 = 5.200 data points/giây. Không thể gửi từng điểm qua MQTT — overhead quá lớn. Cần: (1) **Micro-aggregation at source**: thiết bị edge tổng hợp 100 readings thành 1 window (avg, min, max, std) mỗi giây trước khi publish. (2) **Binary protocol**: dùng MQTT với payload MessagePack/Protobuf thay vì JSON để giảm 60–80% kích thước. (3) **Topic sharding**: tách `pump/sensors/vibration`, `pump/sensors/temperature`... để Node-RED filter chỉ nhận group cần thiết.

---

**Q5. Cả MQTTBridge và Node-RED đều subscribe `pump/sensors` — đánh đổi gì?**

> **Đáp án:** **Ưu điểm:** Tách biệt trách nhiệm rõ ràng — MQTTBridge lo relay data thô lên WebSocket, Node-RED lo edge processing. Nếu Node-RED down, dashboard vẫn nhận data qua MQTTBridge. **Nhược điểm:** Broker phải gửi mỗi message hai lần (double fanout), tốn băng thông gấp đôi trên loopback. Trong hệ thống này, `_nodered_last_inject` timestamp giải quyết vấn đề double-broadcast: khi Node-RED active, MQTTBridge tự im lặng và chỉ update cache `_last_sensor_payload`.

---

**Q6. Làm sao quyết định kích thước rolling buffer phù hợp?**

> **Đáp án:** Câu hỏi thực chất là: "Cần bao nhiêu lịch sử để phát hiện xu hướng có ý nghĩa?" Với máy bơm publish 6 Hz, buffer 60 readings = 10 giây — đủ thấy slope. Công thức: `buffer_size = sampling_rate × observation_window_seconds`. Với kho lạnh sensor nhiệt độ 1 Hz cần thấy drift trong 5 phút → buffer 300. Nguyên tắc: quá nhỏ → quá nhạy với nhiễu; quá lớn → phát hiện chậm. Nên test với dữ liệu thực và đo **time-to-detection** vs **false positive rate**.

---

## ⚙️ Thiết kế hệ thống & Kỹ thuật

**Q7. Tại sao broadcast loop chạy ở 2 Hz là quan trọng?**

> **Đáp án:** Node-RED gọi `/simulate/inject` ~6 lần/giây. Nếu mỗi call ngay lập tức `await manager.broadcast()`, với kết nối ngrok latency 200ms, mỗi client nhận 6 message nhưng chỉ xử lý xong 5/giây → build backlog không giới hạn → RAM tăng dần → server freeze sau vài phút. `_broadcast_loop` dùng pattern "last-value wins": 6 payload ghi đè nhau vào `_pending_sensor_payload`, loop drain mỗi 0.5s → client luôn nhận reading mới nhất, không bao giờ bị backlog.

---

**Q8. Tại sao cần `_forced_state` lock trong demo trực tiếp?**

> **Đáp án:** Kịch bản: Presenter nhấn "🔴 Critical" trên control panel → `/control/critical` set `_forced_state = "critical"`. Ngay sau đó Node-RED tiếp tục gọi `/simulate/inject` với data từ MQTT (có thể là NORMAL). Nếu không có lock, dashboard sẽ nhận `state_command: critical` rồi ngay lập tức nhận `sensor_update: overall_status=NORMAL` → badge nhấp nháy liên tục. Với lock, mọi payload đi qua `/simulate/inject` đều bị override: `payload.update({"overall_status": "CRITICAL", "anomaly_detected": True})`.

---

**Q9. Tại sao dùng singleton Groq client thay vì tạo mới mỗi request?**

> **Đáp án:** `AsyncGroq()` khởi tạo một `httpx.AsyncClient` với connection pool bên trong. Tạo mới mỗi request = tạo mới connection pool = mỗi Groq call tạo một TCP connection mới (TLS handshake ~200ms overhead) và không bao giờ close pool cũ → rò rỉ file descriptor. Sau ~1000 request, server hết fd → `OSError: [Errno 24] Too many open files`. Singleton giữ connection pool sống, tái sử dụng TCP connection → nhanh hơn và không rò rỉ.

---

**Q10. 30 học viên cùng trigger anomaly — `Semaphore(2)` xử lý thế nào?**

> **Đáp án:** 2 call đầu tiên acquire semaphore và gọi Groq ngay. 28 call còn lại block tại `async with _ai_semaphore` — chúng **không bị từ chối**, chỉ xếp hàng chờ. Khi một Groq call xong (~2-5s), semaphore release → call tiếp theo chạy. Vấn đề: với 28 call xếp hàng, call cuối chờ ~70 giây → timeout 45s sẽ cancel nó → trả mock response. Scale solution: tăng semaphore lên 5-10, thêm request dedup (nếu 5 học viên cùng trigger trong 2s, chỉ gọi AI 1 lần và broadcast kết quả cho tất cả).

---

**Q11. Tại sao Resend phải dùng `asyncio.to_thread()`?**

> **Đáp án:** `resend.Emails.send()` là blocking HTTP call (dùng `requests` library bên trong, không phải `httpx`). Nếu gọi trực tiếp trong async handler, nó block toàn bộ asyncio event loop trong thời gian Resend API respond (~500ms–2s). Trong thời gian đó, **tất cả WebSocket broadcast bị đóng băng** — không có sensor update nào đến dashboard. `asyncio.to_thread()` chạy blocking call trong thread pool riêng, event loop tiếp tục xử lý coroutine khác. `wait_for(timeout=15s)` đảm bảo nếu Resend unreachable, thread bị cancel sau 15s, không chiếm thread pool mãi.

---

**Q12. `_nodered_last_inject` trong 5 giây — edge case nào gây no data?**

> **Đáp án:** Nếu Node-RED chạy nhưng flow bị lỗi sau `Parse & Validate` (ví dụ payload không có `sensors` field), Node-RED vẫn không gọi `/simulate/inject`. Tuy nhiên `_nodered_last_inject` vẫn = 0 → `nodered_active = False` → MQTTBridge broadcast raw payload. Nhưng nếu Node-RED đang start up và đã gọi `/simulate/inject` 1 lần rồi crash ngay, `_nodered_last_inject` sẽ = now → MQTTBridge im lặng 5 giây trong khi Node-RED không gửi gì → dashboard trống 5s. Fix: giảm window xuống 2s hoặc thêm heartbeat endpoint từ Node-RED.

---

## 🤖 AI & Machine Learning

**Q13. Thông tin gì bị mất khi gửi JSON snapshot thay vì raw time-series?**

> **Đáp án:** LLM không thấy được: shape của curve (đột ngột tăng vs tăng dần), multi-sensor correlation (vibration tăng đúng lúc pressure giảm — dấu hiệu cavitation), oscillation patterns, hay outlier spikes. Node-RED bù một phần bằng cách tính `slope`, `std_dev`, `rate_of_change`, và `trending` (DEGRADING/IMPROVING/STABLE) — đây là feature engineering thủ công. Giải pháp tốt hơn: gửi cả array `history: [20 readings]` cho mỗi sensor group để LLM có thể suy luận về shape.

---

**Q14. Rủi ro khi tin vào ước tính số của LLM trong context an toàn?**

> **Đáp án:** LLM không có model vật lý của máy bơm — nó suy luận từ pattern trong training data. `estimated_hours_to_failure: 4` có thể sai lệch 10× trong thực tế. Rủi ro: (1) **Overconfidence** — operator tin tuyệt đối và delay bảo trì 3 giờ → máy hỏng sớm hơn. (2) **False urgency** — ước tính quá thấp → shutdown không cần thiết, mất sản xuất. Biện pháp: hiển thị rõ "AI estimate — not certified" trên UI, thêm confidence interval, chỉ dùng ước tính để *ưu tiên* hành động, không để *quyết định* thay người.

---

**Q15. Hiểu thế nào về `HIGH` risk với `confidence: 0.45`?**

> **Đáp án:** `confidence: 0.45` nghĩa là LLM chỉ khoảng 45% chắc chắn — ngang mức đoán mò. HIGH risk + thấp confidence = "có dấu hiệu đáng lo nhưng tôi không chắc". Operator không nên hành động như thể đây là emergency, nhưng cũng không nên bỏ qua. UI cải thiện: hiển thị confidence dưới dạng thanh màu (xanh → đỏ), thêm text "Dữ liệu không đủ rõ ràng để đưa ra kết luận chắc chắn", và recommend "tăng tần suất giám sát" thay vì "dừng máy ngay".

---

**Q16. Thiết kế throttling thông minh hơn cho AI call?**

> **Đáp án:** Thay vì throttle theo thời gian cố định (1/60s), dùng **change-triggered throttle**: so sánh snapshot hiện tại với snapshot lần AI call trước. Nếu `|current_health - last_ai_health| > 15` hoặc bất kỳ sensor nào đổi status (NORMAL→WARNING) → gọi AI ngay bất kể cooldown. Nếu không thay đổi đáng kể → tiếp tục chờ. Trong Node-RED: lưu `context.set('lastAISnapshot', snapshot)`, so sánh trước khi throttle. Điều này đảm bảo AI được gọi khi *tình huống thực sự xấu đi nhanh*, không bị bỏ lỡ do đang trong cooldown window.

---

**Q17. Rule-based anomaly detection vs trained ML model — giới hạn gì?**

> **Đáp án:** Rule-based (ngưỡng + slope): dễ giải thích, không cần data, nhưng ngưỡng cứng → không thích nghi với wear & tear (máy cũ bình thường ở mức cao hơn), bỏ sót pattern phức tạp (vibration bình thường nhưng kết hợp temperature cao = vấn đề). ML model (Isolation Forest): học "normal" từ dữ liệu, phát hiện outlier đa chiều, không cần set ngưỡng thủ công. Cần để train: ít nhất 1-2 tuần dữ liệu NORMAL (khoảng 1M+ readings), label cho các sự cố đã biết, feature engineering (lag features, rolling stats). Dataset 220K rows hiện có là điểm khởi đầu tốt.

---

**Q18. Truy cập 60 readings đầy đủ sẽ thay đổi AI thế nào?**

> **Đáp án:** Thay vì chỉ thấy `{vibration: {current: 7.1, slope: 0.05, status: CRITICAL}}`, LLM thấy array `[4.2, 4.5, 4.8, 5.1, 5.6, 6.2, 6.8, 7.1...]` — có thể nhận ra rằng tăng từ từ trong 60s (wear) khác với nhảy vọt trong 5s (impact damage). Phân biệt hai pattern này dẫn đến khuyến nghị khác nhau hoàn toàn: wear → lên lịch thay bearing trong 4h; impact damage → dừng máy ngay kiểm tra. Payload gửi đến Groq sẽ lớn hơn (~5-10KB vs ~1KB hiện tại) nhưng chất lượng phân tích tốt hơn đáng kể.

---

## 📊 Kinh doanh & Use Case IoT

**Q19. Cần dữ liệu gì để ước tính chi phí chính xác?**

> **Đáp án:** LLM hiện dùng con số mặc định trong system prompt. Để chính xác cần: (1) Chi phí downtime thực tế ($/giờ mất sản xuất), (2) Chi phí thay thế từng component (bearing, seal, impeller), (3) Chi phí nhân công bảo trì (giờ công × đơn giá), (4) Lịch sử MTTR (Mean Time To Repair) của nhà máy. Chi phí ẩn LLM hay bỏ sót: sản phẩm hỏng trong downtime, hư hại domino (một pump hỏng → overload pump dự phòng), penalty hợp đồng nếu giao hàng trễ, chi phí kiểm tra an toàn sau sự cố.

---

**Q20. Quản lý alert fatigue với 200 máy bơm?**

> **Đáp án:** Cơ chế hiện tại (1 email/60s/máy) → 200 máy × 24h = có thể hàng nghìn email/ngày. Giải pháp: (1) **Alert grouping**: tổng hợp nhiều cảnh báo cùng loại trong 5 phút thành 1 email "3 máy bơm có anomaly: B12, B15, B23". (2) **Severity routing**: WARNING → ticket trong hệ thống CMMS, không email; CRITICAL → email + SMS. (3) **Smart suppression**: nếu cùng máy đã cảnh báo và chưa được xử lý, không gửi lại. (4) **Shift-based routing**: cảnh báo 2h sáng → chỉ đến on-call engineer, không broadcast toàn team.

---

**Q21. Thêm gì để hỗ trợ phân tích xu hướng dài hạn?**

> **Đáp án:** Cần thêm: (1) **Time-series database**: InfluxDB hoặc TimescaleDB — tối ưu cho write nhiều, query theo range thời gian. Grafana connect trực tiếp để visualize. (2) **Event store**: PostgreSQL lưu AI recommendations và alert history với full-text search. (3) Trong `_on_message` hoặc `/simulate/inject`: ghi mỗi payload vào DB async (không block broadcast). Schema tối giản: `(timestamp, machine_id, vibration, temperature, pressure, flow_rate, health_score, overall_status)`. Với 6 Hz × 30 ngày = ~15M rows/máy → cần retention policy (giữ raw 7 ngày, aggregate 1 năm).

---

**Q22. Thiết kế topic hierarchy cho 50 máy?**

> **Đáp án:** Thay `pump/sensors` bằng: `factory/{site_id}/pump/{pump_id}/sensors`. Ví dụ: `factory/hanoi/pump/B12/sensors`. Node-RED dùng wildcard: `factory/+/pump/+/sensors` để nhận tất cả, hoặc `factory/hanoi/pump/B12/sensors` để chỉ nhận một máy. Thêm topics: `factory/hanoi/pump/B12/control` (nhận lệnh từ backend), `factory/hanoi/pump/B12/alerts` (publish alert từ edge). Backend cần thêm `pump_id` vào mọi payload để dashboard biết data của máy nào. Node-RED flow cần thêm node extract `pump_id` từ topic trước khi xử lý.

---

**Q23. 3 single points of failure trong Colab setup?**

> **Đáp án:** (1) **Colab Runtime**: tự ngắt sau 90 phút idle hoặc 12h — toàn bộ hệ thống dừng. Fix: dùng VPS (DigitalOcean $6/tháng) hoặc Google Cloud Run với container. (2) **Cloudflare Tunnel URL**: thay đổi mỗi lần restart — học viên mất link dashboard. Fix: domain tĩnh qua Cloudflare Named Tunnel hoặc ngrok với reserved domain. (3) **Single MQTT Broker**: broker down → mọi data flow dừng. Fix: Mosquitto cluster hoặc HiveMQ Cloud (managed, HA). Bonus SPOF: tất cả dịch vụ trên 1 process — nếu uvicorn crash, cả WebSocket lẫn REST đều mất. Fix: supervisor/systemd để auto-restart.

---

**Q24. Hoạt động offline được không?**

> **Đáp án:** **Offline hoàn toàn:** `mqtt_replay.py` ✅, Mosquitto ✅, Node-RED ✅, FastAPI (không AI, không email) ✅, Dashboard ✅. **Cần internet:** Groq API ❌ (LLM inference trên cloud), Resend email ❌, Cloudflare Tunnel ❌ (chỉ cần nếu remote access). **Để offline hoàn toàn:** (1) Thay Groq bằng Ollama chạy local (llama3.2 3B đủ dùng trên laptop tốt), (2) Thay Resend bằng SMTP local (Postfix) hoặc bỏ email thay bằng MQTT alert topic, (3) Dashboard truy cập qua `localhost:8000` thay vì tunnel. Use case: nhà máy trong khu công nghiệp không có internet ổn định.

---

## 🔬 Nâng cao / Mở rộng

**Q25. Tại sao dashboard suppress WebSocket update khi local sim chạy?**

> **Đáp án:** Khi presenter nhấn "⚠ Simulate Warning", `startSimStream('warning')` set `_simInterval` và bắt đầu gọi `processSensorUpdate(makeSensorPayload('warning'))` mỗi giây với dữ liệu giả lập. Đồng thời, WebSocket vẫn nhận `sensor_update` từ backend (có thể là data NORMAL từ MQTT). Nếu không suppress, hai luồng data chạy song song → badge status nhấp nháy NORMAL/WARNING liên tục, health score dao động lạ, chart bị "nhiễu" hai profile. Guard `if (!_simInterval)` đảm bảo khi đang demo local sim, WS data bị bỏ qua hoàn toàn.

---

**Q26. Thiết kế "digital twin" extension?**

> **Đáp án:** Cần thêm: (1) **Physics model**: equation đơn giản mô phỏng wear — ví dụ `vibration(t+1) = vibration(t) + slope * dt + noise`. Calibrate từ 60 readings lịch sử. (2) **Prediction engine**: chạy model forward 60 phút, trả về array `[{t: +10min, vibration: 7.5, health: 65}, ...]`. (3) **API endpoint** `/predict?horizon=60`: trả về prediction array. (4) **Dashboard panel**: chart dashed line hiển thị predicted trajectory, với confidence band (std_dev nhân hệ số). (5) Integration: Node-RED sau `Compute Trends` gửi slope/intercept đến `/predict`, kết quả broadcast qua WebSocket `{type: "prediction", ...}`.

---

**Q27. Làm ngưỡng thích nghi với load context?**

> **Đáp án:** Cần thêm `load_pct` (0–100) vào MQTT payload từ simulator. Node-RED lookup ngưỡng động: thay vì `vibration_warn = 4.5` cố định, dùng linear interpolation: `vibration_warn = 3.0 + (load_pct/100) * 2.5` → 50% load = warn ở 4.25, 100% load = warn ở 5.5. Trong `sensor_groups.json` thêm: `{"warn_at_0pct": 3.0, "warn_at_100pct": 5.5}`. Tiếp theo: học ngưỡng từ dữ liệu — tính percentile 95 của vibration trong rolling window 24h khi machine_status=NORMAL → đây là "baseline" động, tự điều chỉnh theo điều kiện thực tế.

---

**Q28. Thay Groq bằng model nhỏ on-device — đánh đổi gì?**

> **Đáp án:** **Phi-3 mini (3.8B)** chạy trên Raspberry Pi 5 (8GB RAM): inference ~15-30s/response vs Groq ~1-2s. **Mất:** tốc độ, độ phức tạp lý luận, khả năng tiếng Việt (model nhỏ yếu hơn). **Được:** hoàn toàn offline, không phụ thuộc API quota, độ trễ không phụ thuộc internet, dữ liệu nhạy cảm không rời khỏi nhà máy (privacy/compliance). **Scenarios xứng đáng:** nhà máy dầu khí offshore không có internet ổn định, khu công nghiệp restricted network (quân sự, dược phẩm), use case cần GDPR compliance nghiêm ngặt, hoặc khi Groq free tier không đủ cho 24/7 production monitoring.

---

*Tài liệu này chỉ dành cho giảng viên — không phát cho học viên trước buổi thảo luận.*
