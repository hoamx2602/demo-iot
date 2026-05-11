# IIoT Curriculum Expansion Ideas (Summary)

> **Philosophy:** Focus on Low-Code/No-Code execution and high-visual impact for operational professionals.

## 1. Hands-On Lab Ideas (Low-Code / Drag-and-Drop)

*   **Idea 1: OT/IT Integration (Modbus to MQTT)**
    *   *Task:* Students use a pre-installed `Modbus Read` node in Node-RED to grab legacy PLC data and wire it to the Cloud. Zero Python needed.
*   **Idea 2: 3D Digital Twin**
    *   *Task:* Students inject simulated high-temperature data via Node-RED and watch a pre-built 3D pump model react (e.g., glowing red, shaking) on the dashboard.
*   **Idea 3: Remote Monitoring (LoRaWAN + Grafana)**
    *   *Task:* Hex decoding is handled in the background. Students focus purely on routing the clean data to InfluxDB and building line charts in Grafana.
*   **Idea 4: Cyber Attack & Defense**
    *   *Task:* Instructor runs a visual "Hacker" script to intercept plain-text pump data. Students thwart the attack by simply swapping to a "Secure MQTT (TLS)" node in Node-RED.

## 2. Instructor Showcase Demos (High Conceptual Impact)

*   **Showcase 1: "Edge vs. Cloud" Race (Latency & Reliability)**
    *   *Demo:* Split-screen dashboard. Instructor clicks "Cut the Internet". The Cloud-connected pump freezes and breaks, while the Edge-AI pump safely triggers a local emergency shutoff.
*   **Showcase 2: AR Maintenance Overlay (The Connected Worker)**
    *   *Demo:* Students point their smartphone cameras at a printed QR code. WebAR displays live MQTT sensor data and 3D warning arrows floating over the physical paper.
*   **Showcase 3: Energy Flow Sankey Diagram (ESG/Cost Optimization)**
    *   *Demo:* A dynamic energy pipeline visualization. When a bearing fails, the "Energy Loss" branch visually bulges red, and a counter aggressively ticks up "Wasted Dollars" and "CO2 Emissions".

## 3. Recommended 5-Phase Flow

1. **Foundation:** PumpGuard AI Lab (Predictive Maintenance basics).
2. **OT/IT Bridge:** Node-RED Modbus integration.
3. **Storage & Analytics:** LoRaWAN data visualized in Grafana.
4. **Advanced Visualization:** 3D Digital Twin observation.
5. **Security:** Defeating an active MITM attack using TLS.
