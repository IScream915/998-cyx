const DEFAULT_WS_PORT = 8765;
const EMPTY_PIXEL = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==";

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
  return new Date(ts * 1000).toLocaleTimeString("zh-CN", {
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
            <h3 class="card-title">场景联动展示</h3>
          </div>
          <span id="ws-status-badge" class="badge warn">连接中</span>
        </header>
        <div class="card-body">
          <p id="ws-endpoint" class="ws-endpoint mono"></p>
        </div>
      </article>

      <section class="fullflow-main">
        <article class="card">
          <header class="card-head">
            <div>
              <h3 class="card-title">驾驶场景帧序列</h3>
            </div>
            <span id="frame-badge" class="badge mono">frame_id -</span>
          </header>
          <div class="card-body">
            <div class="stage-frame">
              <img id="stage-image" src="${EMPTY_PIXEL}" alt="等待实时帧" loading="lazy" />
              <div class="stage-overlay">
                <span id="stage-time-badge" class="badge mono">--:--:--</span>
                <span id="stage-scene-badge" class="badge">实时流</span>
              </div>
            </div>
          </div>
        </article>

        <article class="card">
          <header class="card-head">
            <div>
              <h3 class="card-title">模块输出实时面板</h3>
            </div>
          </header>
          <div class="card-body module-grid">
            <section class="module-card">
              <div class="module-card-head">
                <h4>moduleB 场景识别</h4>
                <span class="badge mono">B</span>
              </div>
              <div id="card-module-b" class="module-card-body"></div>
            </section>
            <section class="module-card">
              <div class="module-card-head">
                <h4>moduleC 盲区监测</h4>
                <span class="badge mono">C</span>
              </div>
              <div id="card-module-c" class="module-card-body"></div>
            </section>
            <section class="module-card">
              <div class="module-card-head">
                <h4>moduleD 检测结果</h4>
                <span class="badge mono">D</span>
              </div>
              <div id="card-module-d" class="module-card-body"></div>
            </section>
            <section class="module-card">
              <div class="module-card-head">
                <h4>moduleE 融合提醒</h4>
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
            <h3 class="card-title">模块A数据</h3>
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
        { label: "状态", value: "等待moduleC输出" },
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

    for (const fieldName of fieldNames) {
      const rawValue = syncMeta[fieldName];
      const fieldPayload =
        rawValue && typeof rawValue === "object" && !Array.isArray(rawValue) ? rawValue : {};

      const section = document.createElement("section");
      section.className = "fullflow-a-field";
      const title = document.createElement("p");
      title.className = "fullflow-a-field-title mono";
      title.textContent = fieldName;
      section.appendChild(title);
      section.appendChild(components.createJsonBlock(fieldPayload));
      moduleADataRoot.appendChild(section);
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
          ? detectedSigns.join(", ") || "-"
          : detectedSigns === undefined || detectedSigns === null
            ? "-"
            : String(detectedSigns),
      },
    ];
    renderModuleMetricBody(cardE, components, metricRows);
  }

  function renderFrame(payload) {
    const frameId = toNumber(payload?.frame_id);
    if (frameId === null) {
      pushLog("收到ab_frame但frame_id非法，已忽略");
      return;
    }

    if (typeof payload?.image_src === "string" && payload.image_src.startsWith("data:image/jpeg;base64,")) {
      stageImage.src = payload.image_src;
      stageImage.alt = `驾驶场景帧 ${frameId}`;
    } else {
      pushLog(`frame_id=${frameId} 的image_src非法，已忽略`);
      return;
    }

    frameBadge.textContent = `frame_id ${Math.trunc(frameId)}`;
    stageTimeBadge.textContent = formatTime(payload?.ts, components);
    stageSceneBadge.textContent = "实时流";

    const moduleBPayload =
      payload?.moduleB && typeof payload.moduleB === "object" ? payload.moduleB : {};
    renderModuleB(moduleBPayload, Math.trunc(frameId));
    pushLog(`接收配对帧 frame_id=${Math.trunc(frameId)}，已更新场景图与moduleB面板`);
  }

  function renderDFrame(payload) {
    const frameId = toNumber(payload?.frame_id);
    if (frameId === null) {
      pushLog("收到d_frame但frame_id非法，已忽略");
      return;
    }

    const moduleDPayload =
      payload?.moduleD && typeof payload.moduleD === "object" ? payload.moduleD : {};
    renderModuleD(moduleDPayload, Math.trunc(frameId));
    pushLog(`接收模块D输出 frame_id=${Math.trunc(frameId)}，已更新moduleD面板`);
  }

  function renderCFrame(payload) {
    const frameId = toNumber(payload?.frame_id);
    if (frameId === null) {
      pushLog("收到c_frame但frame_id非法，已忽略");
      return;
    }

    const moduleCPayload =
      payload?.moduleC && typeof payload.moduleC === "object" ? payload.moduleC : {};
    renderModuleC(moduleCPayload, Math.trunc(frameId));
    pushLog(`接收模块C输出 frame_id=${Math.trunc(frameId)}，已更新moduleC面板`);
  }

  function renderAFrame(payload) {
    const frameId = toNumber(payload?.frame_id);
    if (frameId === null) {
      pushLog("收到a_frame但frame_id非法，已忽略");
      return;
    }

    const moduleAPayload =
      payload?.moduleA && typeof payload.moduleA === "object" ? payload.moduleA : {};
    renderModuleAData(moduleAPayload, Math.trunc(frameId));
    pushLog(`接收模块A输出 frame_id=${Math.trunc(frameId)}，已更新模块A数据`);
  }

  function renderEFrame(payload) {
    const frameId = toNumber(payload?.frame_id);
    if (frameId === null) {
      pushLog("收到e_frame但frame_id非法，已忽略");
      return;
    }

    const moduleEPayload =
      payload?.moduleE && typeof payload.moduleE === "object" ? payload.moduleE : {};
    renderModuleE(moduleEPayload, Math.trunc(frameId));
    pushLog(`接收模块E输出 frame_id=${Math.trunc(frameId)}，已更新moduleE面板`);
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
    setWsStatus(`重连中 ${Math.ceil(delay / 1000)}s`, "warn");

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
    setWsStatus("连接中", "warn");

    const socket = new WebSocket(wsUrl);
    state.socket = socket;

    socket.addEventListener("open", () => {
      if (state.destroyed) {
        return;
      }
      state.reconnectAttempt = 0;
      setWsStatus("已连接", "success");
      pushLog("WebSocket 已连接，等待A/B配对帧与moduleC输出");
    });

    socket.addEventListener("message", (event) => {
      if (state.destroyed) {
        return;
      }

      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch (_err) {
        pushLog("收到非JSON消息，已忽略");
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
        const message = payload?.message ?? "状态更新";
        pushLog(`[bridge][${status}] ${message}`);
      }
    });

    socket.addEventListener("error", () => {
      if (state.destroyed) {
        return;
      }
      pushLog("WebSocket 发生错误");
    });

    socket.addEventListener("close", () => {
      if (state.destroyed) {
        return;
      }
      setWsStatus("已断开", "danger");
      pushLog("WebSocket 已断开，准备自动重连");
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
        throw new Error(payload?.error || "切换失败");
      }
      pushLog("已将 moduleB 切换到 ZMQ 输入模式");
    } catch (error) {
      console.warn("[fullflow] 切换 moduleB 到 ZMQ 模式失败:", error);
      pushLog(`moduleB模式回切失败: ${error?.message ?? "unknown error"}`);
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
        throw new Error(payload?.error || "切换失败");
      }
      pushLog("已将 moduleD 切换到 ZMQ 输入模式");
    } catch (error) {
      console.warn("[fullflow] 切换 moduleD 到 ZMQ 模式失败:", error);
      pushLog(`moduleD模式回切失败: ${error?.message ?? "unknown error"}`);
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
