const DEFAULT_WS_PORT = 8765;
const EMPTY_PIXEL = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==";
const DISPLAY_TEXT_MAP = new Map([
  ["\u524d\u65b9\u65bd\u5de5", "Roadwork Ahead"],
  ["\u9650\u901f20", "Speed Limit 20"],
  ["\u9650\u901f40", "Speed Limit 40"],
  ["\u9650\u901f60", "Speed Limit 60"],
  ["\u9650\u901f80", "Speed Limit 80"],
  ["\u9650\u901f100", "Speed Limit 100"],
  ["\u9650\u901f120", "Speed Limit 120"],
]);

function toDisplayText(value) {
  if (typeof value !== "string") {
    return value;
  }
  return DISPLAY_TEXT_MAP.get(value) ?? value;
}

function renderModuleMetricBody(body, components, metricRows) {
  components.clearNode(body);
  body.appendChild(components.createMetricList(metricRows));
}

function toNumber(value) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function getWebSocketUrl() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const host = window.location.hostname || "127.0.0.1";
  return `${protocol}://${host}:${DEFAULT_WS_PORT}`;
}

function formatTime(tsSeconds, components) {
  const ts = toNumber(tsSeconds);
  if (ts === null) {
    return components.formatClock();
  }
  return new Date(ts * 1000).toLocaleTimeString("en-US", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function normalizeConfidence(value) {
  const parsed = toNumber(value);
  if (parsed === null) {
    return null;
  }
  if (parsed >= 0 && parsed <= 1) {
    return parsed * 100;
  }
  return parsed;
}

function formatBooleanFlag(value) {
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }
  return "-";
}

function formatModuleATableValue(value) {
  if (value === null || value === undefined) {
    return "-";
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return value.toFixed(6).replace(/\.?0+$/, "");
  }
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }
  if (typeof value === "string") {
    return value;
  }
  try {
    return JSON.stringify(value);
  } catch (_err) {
    return String(value);
  }
}

export function mount(container, { components }) {
  const state = {
    socket: null,
    reconnectTimer: null,
    reconnectAttempt: 0,
    destroyed: false,
  };

  container.innerHTML = `
    <section class="fullflow-page">
      <article class="card fullflow-top">
        <header class="card-head">
          <div>
            <h3 class="card-title">Scenario Coordination Demo</h3>
          </div>
          <span id="ws-status-badge" class="badge warn">Connecting</span>
        </header>
        <div class="card-body">
          <p id="ws-endpoint" class="ws-endpoint mono"></p>
        </div>
      </article>

      <section class="fullflow-main">
        <article class="card">
          <header class="card-head">
            <div>
              <h3 class="card-title">Driving Scene Frame Sequence</h3>
            </div>
            <span id="frame-badge" class="badge mono">frame_id -</span>
          </header>
          <div class="card-body">
            <div class="stage-frame">
              <img id="stage-image" src="${EMPTY_PIXEL}" alt="Waiting for live frame" loading="lazy" />
              <div class="stage-overlay">
                <span id="stage-time-badge" class="badge mono">--:--:--</span>
                <span id="stage-scene-badge" class="badge">Live Stream</span>
              </div>
            </div>
          </div>
        </article>

        <article class="card">
          <header class="card-head">
            <div>
              <h3 class="card-title">Live Module Output Panel</h3>
            </div>
          </header>
          <div class="card-body module-grid">
            <section class="module-card">
              <div class="module-card-head">
                <h4>moduleB Scene Classification</h4>
                <span class="badge mono">B</span>
              </div>
              <div id="card-module-b" class="module-card-body"></div>
            </section>
            <section class="module-card">
              <div class="module-card-head">
                <h4>moduleC Blind-Spot Monitoring</h4>
                <span class="badge mono">C</span>
              </div>
              <div id="card-module-c" class="module-card-body"></div>
            </section>
            <section class="module-card">
              <div class="module-card-head">
                <h4>moduleD Detection Results</h4>
                <span class="badge mono">D</span>
              </div>
              <div id="card-module-d" class="module-card-body"></div>
            </section>
            <section class="module-card">
              <div class="module-card-head">
                <h4>moduleE Fused Reminder</h4>
                <span class="badge mono">E</span>
              </div>
              <div id="card-module-e" class="module-card-body"></div>
            </section>
          </div>
        </article>
      </section>

      <article class="card">
        <header class="card-head">
          <div>
            <h3 class="card-title">Module A Data</h3>
          </div>
        </header>
        <div id="module-a-data" class="card-body fullflow-module-a-data"></div>
      </article>
    </section>
  `;

  const wsStatusBadge = container.querySelector("#ws-status-badge");
  const wsEndpoint = container.querySelector("#ws-endpoint");
  const stageImage = container.querySelector("#stage-image");
  const frameBadge = container.querySelector("#frame-badge");
  const stageTimeBadge = container.querySelector("#stage-time-badge");
  const stageSceneBadge = container.querySelector("#stage-scene-badge");
  const cardC = container.querySelector("#card-module-c");
  const cardB = container.querySelector("#card-module-b");
  const cardD = container.querySelector("#card-module-d");
  const cardE = container.querySelector("#card-module-e");
  const moduleADataRoot = container.querySelector("#module-a-data");

  function setWsStatus(text, tone = "") {
    wsStatusBadge.className = `badge${tone ? ` ${tone}` : ""}`;
    wsStatusBadge.textContent = text;
  }

  function pushLog(text) {
    if (!text) {
      return;
    }
    console.info(`[fullflow] ${text}`);
  }

  function renderStaticCards() {
    renderModuleMetricBody(
      cardC,
      components,
      [
        { label: "status", value: "Waiting for moduleC output" },
        { label: "tracked_pedestrians", value: "-" },
      ],
    );
  }

  function renderModuleAData(moduleAPayload, fallbackFrameId) {
    const frameId = moduleAPayload?.frame_id ?? fallbackFrameId;
    const syncMeta =
      moduleAPayload?.sync_meta && typeof moduleAPayload.sync_meta === "object" ? moduleAPayload.sync_meta : {};
    const fieldNames = ["time_offsets", "kf_residuals", "quality_scores", "alignment_errors"];

    components.clearNode(moduleADataRoot);
    moduleADataRoot.appendChild(
      components.createMetricList([
        { label: "frame_id", value: String(frameId ?? "-") },
      ]),
    );
    const fieldsGrid = document.createElement("div");
    fieldsGrid.className = "fullflow-a-grid";
    moduleADataRoot.appendChild(fieldsGrid);

    for (const fieldName of fieldNames) {
      const rawValue = syncMeta[fieldName];
      const fieldPayload =
        rawValue && typeof rawValue === "object" && !Array.isArray(rawValue) ? rawValue : {};
      const rows = Object.entries(fieldPayload).sort((a, b) => a[0].localeCompare(b[0], "en-US"));

      const section = document.createElement("section");
      section.className = "fullflow-a-field";
      const title = document.createElement("p");
      title.className = "fullflow-a-field-title mono";
      title.textContent = fieldName;
      section.appendChild(title);

      const tableWrap = document.createElement("div");
      tableWrap.className = "fullflow-a-table-wrap";
      const table = document.createElement("table");
      table.className = "fullflow-a-table";
      table.innerHTML = `
        <thead>
          <tr>
            <th>Key</th>
            <th>Value</th>
          </tr>
        </thead>
      `;

      const tbody = document.createElement("tbody");
      if (!rows.length) {
        const tr = document.createElement("tr");
        const td = document.createElement("td");
        td.colSpan = 2;
        td.className = "fullflow-a-table-empty";
        td.textContent = "No data";
        tr.appendChild(td);
        tbody.appendChild(tr);
      } else {
        for (const [key, value] of rows) {
          const tr = document.createElement("tr");

          const keyCell = document.createElement("td");
          keyCell.className = "fullflow-a-table-key mono";
          keyCell.textContent = key;

          const valueCell = document.createElement("td");
          valueCell.className = "fullflow-a-table-value mono";
          valueCell.textContent = formatModuleATableValue(value);

          tr.appendChild(keyCell);
          tr.appendChild(valueCell);
          tbody.appendChild(tr);
        }
      }
      table.appendChild(tbody);
      tableWrap.appendChild(table);
      section.appendChild(tableWrap);
      fieldsGrid.appendChild(section);
    }
  }

  function renderModuleC(moduleCPayload, fallbackFrameId) {
    const frameId = moduleCPayload?.frame_id ?? fallbackFrameId;
    const trackedPedestrians = moduleCPayload?.tracked_pedestrians;

    renderModuleMetricBody(
      cardC,
      components,
      [
        { label: "frame_id", value: String(frameId ?? "-") },
        { label: "tracked_pedestrians", value: formatBooleanFlag(trackedPedestrians) },
      ],
    );
  }

  function renderModuleB(moduleBPayload, fallbackFrameId) {
    const frameId = moduleBPayload?.frame_id ?? fallbackFrameId;
    const scene = typeof moduleBPayload?.scene === "string" ? moduleBPayload.scene : "unknown";
    const confidence = normalizeConfidence(moduleBPayload?.conference ?? moduleBPayload?.confidence);
    const speed = toNumber(moduleBPayload?.speed);

    renderModuleMetricBody(
      cardB,
      components,
      [
        { label: "frame_id", value: String(frameId ?? "-") },
        { label: "scene", value: scene },
        { label: "confidence", value: confidence === null ? "-" : `${confidence.toFixed(2)}%` },
        { label: "speed", value: speed === null ? "-" : `${Math.round(speed)} km/h` },
      ],
    );
  }

  function renderModuleD(moduleDPayload, fallbackFrameId) {
    const frameId = moduleDPayload?.frame_id ?? fallbackFrameId;
    const numTrafficSigns = toNumber(moduleDPayload?.num_traffic_signs);
    const numPedestrians = toNumber(moduleDPayload?.num_pedestrians);
    const numVehicles = toNumber(moduleDPayload?.num_vehicles);

    renderModuleMetricBody(
      cardD,
      components,
      [
        { label: "frame_id", value: String(frameId ?? "-") },
        { label: "traffic_signs", value: numTrafficSigns === null ? "-" : String(Math.trunc(numTrafficSigns)) },
        { label: "pedestrians", value: numPedestrians === null ? "-" : String(Math.trunc(numPedestrians)) },
        { label: "vehicles", value: numVehicles === null ? "-" : String(Math.trunc(numVehicles)) },
      ],
    );
  }

  function renderModuleE(moduleEPayload, fallbackFrameId) {
    const status = typeof moduleEPayload?.status === "string" ? moduleEPayload.status : "unknown";
    const scene = typeof moduleEPayload?.scene === "string" ? moduleEPayload.scene : "-";
    const speed = toNumber(moduleEPayload?.speed);
    const detectedSigns = moduleEPayload?.detected_signs;

    let statusTone = "";
    if (status === "processed") {
      statusTone = "success";
    } else if (status === "process_error") {
      statusTone = "danger";
    } else {
      statusTone = "warn";
    }

    const metricRows = [
      { label: "status", value: status, tone: statusTone },
      { label: "scene", value: scene },
      { label: "speed", value: speed === null ? "-" : `${Math.round(speed)} km/h` },
      {
        label: "detected_signs",
        value: Array.isArray(detectedSigns)
          ? detectedSigns.map((item) => toDisplayText(String(item))).join(", ") || "-"
          : detectedSigns === undefined || detectedSigns === null
            ? "-"
            : toDisplayText(String(detectedSigns)),
      },
    ];
    renderModuleMetricBody(cardE, components, metricRows);
  }

  function renderFrame(payload) {
    const frameId = toNumber(payload?.frame_id);
    if (frameId === null) {
      pushLog("Received ab_frame with invalid frame_id; ignored");
      return;
    }

    if (typeof payload?.image_src === "string" && payload.image_src.startsWith("data:image/jpeg;base64,")) {
      stageImage.src = payload.image_src;
      stageImage.alt = `Driving scene frame ${frameId}`;
    } else {
      pushLog(`Invalid image_src for frame_id=${frameId}; ignored`);
      return;
    }

    frameBadge.textContent = `frame_id ${Math.trunc(frameId)}`;
    stageTimeBadge.textContent = formatTime(payload?.ts, components);
    stageSceneBadge.textContent = "Live Stream";

    const moduleBPayload =
      payload?.moduleB && typeof payload.moduleB === "object" ? payload.moduleB : {};
    renderModuleB(moduleBPayload, Math.trunc(frameId));
    pushLog(`Received paired frame frame_id=${Math.trunc(frameId)}; updated scene image and moduleB panel`);
  }

  function renderDFrame(payload) {
    const frameId = toNumber(payload?.frame_id);
    if (frameId === null) {
      pushLog("Received d_frame with invalid frame_id; ignored");
      return;
    }

    const moduleDPayload =
      payload?.moduleD && typeof payload.moduleD === "object" ? payload.moduleD : {};
    renderModuleD(moduleDPayload, Math.trunc(frameId));
    pushLog(`Received moduleD output frame_id=${Math.trunc(frameId)}; updated moduleD panel`);
  }

  function renderCFrame(payload) {
    const frameId = toNumber(payload?.frame_id);
    if (frameId === null) {
      pushLog("Received c_frame with invalid frame_id; ignored");
      return;
    }

    const moduleCPayload =
      payload?.moduleC && typeof payload.moduleC === "object" ? payload.moduleC : {};
    renderModuleC(moduleCPayload, Math.trunc(frameId));
    pushLog(`Received moduleC output frame_id=${Math.trunc(frameId)}; updated moduleC panel`);
  }

  function renderAFrame(payload) {
    const frameId = toNumber(payload?.frame_id);
    if (frameId === null) {
      pushLog("Received a_frame with invalid frame_id; ignored");
      return;
    }

    const moduleAPayload =
      payload?.moduleA && typeof payload.moduleA === "object" ? payload.moduleA : {};
    renderModuleAData(moduleAPayload, Math.trunc(frameId));
    pushLog(`Received moduleA output frame_id=${Math.trunc(frameId)}; updated Module A data`);
  }

  function renderEFrame(payload) {
    const frameId = toNumber(payload?.frame_id);
    if (frameId === null) {
      pushLog("Received e_frame with invalid frame_id; ignored");
      return;
    }

    const moduleEPayload =
      payload?.moduleE && typeof payload.moduleE === "object" ? payload.moduleE : {};
    renderModuleE(moduleEPayload, Math.trunc(frameId));
    pushLog(`Received moduleE output frame_id=${Math.trunc(frameId)}; updated moduleE panel`);
  }

  function scheduleReconnect() {
    if (state.destroyed) {
      return;
    }
    if (state.reconnectTimer !== null) {
      window.clearTimeout(state.reconnectTimer);
      state.reconnectTimer = null;
    }

    const delay = Math.min(10000, 1000 * 2 ** state.reconnectAttempt);
    state.reconnectAttempt += 1;
    setWsStatus(`Reconnecting in ${Math.ceil(delay / 1000)}s`, "warn");

    state.reconnectTimer = window.setTimeout(() => {
      state.reconnectTimer = null;
      connectWebSocket();
    }, delay);
  }

  function connectWebSocket() {
    if (state.destroyed) {
      return;
    }

    const wsUrl = getWebSocketUrl();
    wsEndpoint.textContent = `WebSocket: ${wsUrl}`;
    setWsStatus("Connecting", "warn");

    const socket = new WebSocket(wsUrl);
    state.socket = socket;

    socket.addEventListener("open", () => {
      if (state.destroyed) {
        return;
      }
      state.reconnectAttempt = 0;
      setWsStatus("Connected", "success");
      pushLog("WebSocket connected. Waiting for A/B paired frames and moduleC output");
    });

    socket.addEventListener("message", (event) => {
      if (state.destroyed) {
        return;
      }

      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch (_err) {
        pushLog("Received non-JSON message; ignored");
        return;
      }

      const evt = payload?.event;
      if (evt === "ab_frame") {
        renderFrame(payload);
        return;
      }
      if (evt === "a_frame") {
        renderAFrame(payload);
        return;
      }
      if (evt === "d_frame") {
        renderDFrame(payload);
        return;
      }
      if (evt === "c_frame") {
        renderCFrame(payload);
        return;
      }
      if (evt === "e_frame") {
        renderEFrame(payload);
        return;
      }
      if (evt === "status") {
        const status = payload?.status ?? "status";
        const message = payload?.message ?? "Status update";
        pushLog(`[bridge][${status}] ${message}`);
      }
    });

    socket.addEventListener("error", () => {
      if (state.destroyed) {
        return;
      }
      pushLog("WebSocket error");
    });

    socket.addEventListener("close", () => {
      if (state.destroyed) {
        return;
      }
      setWsStatus("Disconnected", "danger");
      pushLog("WebSocket disconnected. Reconnecting automatically");
      scheduleReconnect();
    });
  }

  async function switchModuleBToZmqMode() {
    try {
      const response = await fetch("/api/module-b/mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "zmq" }),
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const payload = await response.json().catch(() => ({}));
      if (payload?.ok === false) {
        throw new Error(payload?.error || "Switch failed");
      }
      pushLog("Switched moduleB to ZMQ input mode");
    } catch (error) {
      console.warn("[fullflow] Failed to switch moduleB to ZMQ mode:", error);
      pushLog(`moduleB mode switch-back failed: ${error?.message ?? "unknown error"}`);
    }
  }

  async function switchModuleDToZmqMode() {
    try {
      const response = await fetch("/api/module-d/mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "zmq" }),
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const payload = await response.json().catch(() => ({}));
      if (payload?.ok === false) {
        throw new Error(payload?.error || "Switch failed");
      }
      pushLog("Switched moduleD to ZMQ input mode");
    } catch (error) {
      console.warn("[fullflow] Failed to switch moduleD to ZMQ mode:", error);
      pushLog(`moduleD mode switch-back failed: ${error?.message ?? "unknown error"}`);
    }
  }

  renderStaticCards();
  renderModuleAData({}, null);
  renderModuleC({}, null);
  renderModuleB({}, null);
  renderModuleD({}, null);
  renderModuleE({}, null);
  switchModuleBToZmqMode();
  switchModuleDToZmqMode();
  connectWebSocket();

  return () => {
    state.destroyed = true;
    if (state.reconnectTimer !== null) {
      window.clearTimeout(state.reconnectTimer);
      state.reconnectTimer = null;
    }
    if (state.socket) {
      try {
        state.socket.close();
      } catch (_err) {
        // ignore
      }
      state.socket = null;
    }
  };
}
