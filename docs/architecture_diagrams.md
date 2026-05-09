# PumpGuard AI — Architecture Diagrams

> Mermaid diagrams for all architecture/pipeline sections in the workshop.
> Paste into the corresponding notebook markdown cells.

---

## 1. System Overview (Cover / Part 0)

```mermaid
flowchart TB
    subgraph DATA["📊 Data Layer"]
        CSV["sensor.csv\n220K rows · 52 sensors"]
        ASC["analyze_sensors.py"]
        SGJ["sensor_groups.json\nThresholds · scale · units"]
        CSV --> ASC --> SGJ
    end

    subgraph SIM["🔁 Simulator"]
        REPLAY["mqtt_replay.py\n360× time compression\nNORMAL / BROKEN modes"]
        SGJ --> REPLAY
    end

    subgraph BROKER["📡 MQTT Broker"]
        MOSQ["Mosquitto\nlocalhost:1883\ntopic: pump/sensors"]
    end

    subgraph PIPELINE["⚙️ Node-RED Pipeline"]
        NR_IN["MQTT In"]
        NR_VAL["Parse & Validate"]
        NR_BUF["Rolling Buffer 60"]
        NR_TREND["Compute Trends\nanomaly_score 0–1"]
        NR_ROUTE{"score ≥ 0.4?"}
        NR_UPD["POST /sensor-update"]
        NR_THR["Throttle 60s"]
        NR_ALERT["POST /alert"]
        NR_IN --> NR_VAL --> NR_BUF --> NR_TREND --> NR_ROUTE
        NR_ROUTE -- "No"  --> NR_UPD
        NR_ROUTE -- "Yes" --> NR_THR --> NR_ALERT
    end

    subgraph BACKEND["🖥️ FastAPI :8000"]
        BRIDGE["MQTTBridge\nTLS · auto-reconnect"]
        WS["WebSocket /ws\nBroadcast to clients"]
        GROQ_AI["POST /analyze\nGroq llama-3.3-70b\nRisk · Recommendations"]
        ALERT_EP["POST /alert\nAI + Email"]
        BRIDGE --> WS
        NR_UPD --> WS
        NR_ALERT --> ALERT_EP --> GROQ_AI
    end

    subgraph EMAIL["📧 Email Alert"]
        RESEND["Resend API\nHTML template"]
    end

    subgraph DASH["🖥️ Dashboard"]
        IDX["index.html\nLive Feed · Timeline\nAI Recommendation"]
    end

    subgraph TUNNEL["🌐 Public Access"]
        CF["Cloudflare Tunnel\nor ngrok — HTTPS + WSS"]
    end

    REPLAY -->|"MQTT publish"| MOSQ
    MOSQ --> NR_IN
    MOSQ --> BRIDGE
    ALERT_EP --> RESEND
    WS -->|"sensor_update / ai_recommendation"| DASH
    BACKEND <--> TUNNEL
    TUNNEL --> DASH

    style DATA     fill:#1e3a2f,color:#a7f3d0
    style SIM      fill:#1a2e4a,color:#93c5fd
    style BROKER   fill:#2d1f3d,color:#c4b5fd
    style PIPELINE fill:#3d2a1a,color:#fcd34d
    style BACKEND  fill:#1a1f3d,color:#a5b4fc
    style EMAIL    fill:#3d1a1a,color:#fca5a5
    style DASH     fill:#1a3d2a,color:#6ee7b7
    style TUNNEL   fill:#2a2a2a,color:#e5e7eb
```

---

## 2. Project Directory Structure (Part 1)

```mermaid
graph TD
    ROOT["/content/pump-iot-demo/"]
    B["backend/\nserver.py · .env · requirements.txt"]
    D["dashboard/\nindex.html · control.html"]
    NR["nodered/\nflows.json"]
    SC["scripts/\nmqtt_replay.py · analyze_sensors.py"]
    DA["data/\nsensor.csv · sensor_groups.json"]

    ROOT --> B
    ROOT --> D
    ROOT --> NR
    ROOT --> SC
    ROOT --> DA

    style ROOT fill:#1e3a5f,color:#93c5fd
    style B    fill:#1a2e4a,color:#bfdbfe
    style D    fill:#1a3d2a,color:#6ee7b7
    style NR   fill:#3d2a1a,color:#fcd34d
    style SC   fill:#2d1f3d,color:#c4b5fd
    style DA   fill:#1e3a2f,color:#a7f3d0
```

---

## 3. Node-RED Sensor Processing Pipeline (Part 5)

```mermaid
flowchart TD
    IN["📨 MQTT In\ntopic: pump/sensors"]
    VAL["🔍 Parse & Validate\nDrop NaN · Check schema"]
    BUF["🗄️ Rolling Buffer\nLast 60 readings (~30s)"]
    TREND["📈 Compute Trends\navg · slope · std_dev\nanomaly_score 0–1"]
    ROUTE{"anomaly_score\n≥ 0.4?"}
    UPD["POST /sensor-update\nNormal stream update"]
    THR["⏱️ Throttle 60s\nPrevent alert flood"]
    ALERT["🚨 POST /alert\nTrigger AI + Email"]

    IN --> VAL --> BUF --> TREND --> ROUTE
    ROUTE -- "No"  --> UPD
    ROUTE -- "Yes" --> THR --> ALERT

    style IN    fill:#1a2e4a,color:#93c5fd
    style VAL   fill:#1e3a2f,color:#a7f3d0
    style BUF   fill:#2d1f3d,color:#c4b5fd
    style TREND fill:#3d2a1a,color:#fcd34d
    style ROUTE fill:#3d1a1a,color:#fca5a5
    style UPD   fill:#1a3d2a,color:#6ee7b7
    style THR   fill:#2a2a2a,color:#e5e7eb
    style ALERT fill:#3d1a1a,color:#fca5a5
```

---

## 4. Sensor Simulator Data Pipeline (Part 7)

```mermaid
flowchart LR
    CSV["sensor.csv\n220K rows"]
    ASC["analyze_sensors.py\nGroup sensors\nCompute thresholds"]
    SGJ["sensor_groups.json\nConfig: warn/crit\nscale · offset · unit"]
    REPLAY["mqtt_replay.py\n360× speed\nNORMAL / BROKEN"]
    MOSQ["Mosquitto\n:1883\npump/sensors"]
    NR["Node-RED\n:1880"]
    DASH["Dashboard\nBrowser"]

    CSV --> ASC --> SGJ --> REPLAY
    REPLAY -->|"MQTT publish\nevery 167ms"| MOSQ
    MOSQ --> NR -->|"POST /sensor-update\nor POST /alert"| DASH

    style CSV    fill:#1e3a2f,color:#a7f3d0
    style ASC    fill:#1e3a2f,color:#a7f3d0
    style SGJ    fill:#1e3a2f,color:#a7f3d0
    style REPLAY fill:#1a2e4a,color:#93c5fd
    style MOSQ   fill:#2d1f3d,color:#c4b5fd
    style NR     fill:#3d2a1a,color:#fcd34d
    style DASH   fill:#1a3d2a,color:#6ee7b7
```

---

## 5. End-to-End Pipeline Check (Part 8)

```mermaid
flowchart TD
    SIM["mqtt_replay.py\n🔁 Simulator"]

    subgraph LOCAL["Colab localhost"]
        MOSQ["Mosquitto :1883"]
        NR["Node-RED :1880"]
        API["FastAPI :8000"]
    end

    subgraph CLIENTS["Connected Clients"]
        BROWSER["Dashboard Browser"]
        EMAIL["📧 Email Inbox"]
    end

    TUNNEL["🌐 Cloudflare / ngrok\nHTTPS + WSS"]

    SIM -->|"MQTT pub pump/sensors"| MOSQ
    MOSQ --> NR
    NR -->|"POST /sensor-update"| API
    NR -->|"POST /alert on anomaly"| API
    API -->|"WebSocket broadcast"| BROWSER
    API -->|"Resend API"| EMAIL
    API <-->|"tunnel"| TUNNEL
    TUNNEL --> BROWSER

    style LOCAL   fill:#1a1f3d,color:#a5b4fc
    style CLIENTS fill:#1a3d2a,color:#6ee7b7
    style TUNNEL  fill:#2a2a2a,color:#e5e7eb
```

---

## 6. AI Analysis & Email Alert Flow (Part 9)

```mermaid
sequenceDiagram
    participant NR   as Node-RED
    participant API  as FastAPI :8000
    participant GROQ as Groq API<br/>llama-3.3-70b
    participant WS   as Dashboard<br/>WebSocket
    participant MAIL as Resend Email

    NR  ->> API : POST /alert<br/>{sensor_snapshot, anomaly_score}
    activate API

    API ->> GROQ : chat.completions.create<br/>{system_prompt + sensor data}
    activate GROQ
    GROQ -->> API : JSON response<br/>{risk_level, recommendations,<br/>estimated_hours_to_failure}
    deactivate GROQ

    API ->> WS   : broadcast ai_recommendation
    API ->> MAIL : Send HTML alert email<br/>level: CRITICAL or WARNING

    WS  -->> WS  : Dashboard renders<br/>AI panel + alert banner
    MAIL -->> MAIL: Delivered to ALERT_TO inbox

    deactivate API
```

---

## Summary

| # | Diagram | Notebook Section | Mermaid Type |
|---|---------|-----------------|--------------|
| 1 | System Overview | Cover | `flowchart TB` |
| 2 | Directory Structure | Part 1 | `graph TD` |
| 3 | Node-RED Pipeline | Part 5 | `flowchart TD` |
| 4 | Simulator Pipeline | Part 7 | `flowchart LR` |
| 5 | End-to-End Check | Part 8 | `flowchart TD` |
| 6 | AI & Alert Flow | Part 9 | `sequenceDiagram` |
