const DEFAULT_WS_PORT = 8765;
const EMPTY_PIXEL = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==";

function renderModuleCardBody(body, components, metricRows, payload) {
  components.clearNode(body);
  body.appendChild(components.createMetricList(metricRows));
  body.appendChild(components.createJsonBlock(payload));
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

function createPlaceholderPayload(moduleName, reason) {
  return {
    module: moduleName,
    status: "placeholder",
    reason,
  };
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
              <div id="stage-empty" class="stage-empty">等待 A+B 配对帧...</div>
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
                <h4>moduleA 输入帧</h4>
                <span class="badge mono">A</span>
              </div>
              <div id="card-module-a" class="module-card-body"></div>
            </section>
            <section class="module-card">
              <div class="module-card-head">
                <h4>moduleB 场景识别</h4>
                <span class="badge mono">B</span>
              </div>
              <div id="card-module-b" class="module-card-body"></div>
            </section>
            <section class="module-card">
              <div class="module-card-head">
                <h4>moduleC 检测结果</h4>
                <span class="badge mono">CD</span>
              </div>
              <div id="card-module-cd" class="module-card-body"></div>
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
            <h3 class="card-title">实时日志流</h3>
          </div>
        </header>
        <div class="card-body">
          <ol id="fullflow-log-list" class="log-list"></ol>
        </div>
      </article>
    </section>
  `;

  const wsStatusBadge = container.querySelector("#ws-status-badge");
  const wsEndpoint = container.querySelector("#ws-endpoint");
  const stageImage = container.querySelector("#stage-image");
  const stageEmpty = container.querySelector("#stage-empty");
  const frameBadge = container.querySelector("#frame-badge");
  const stageTimeBadge = container.querySelector("#stage-time-badge");
  const stageSceneBadge = container.querySelector("#stage-scene-badge");
  const cardA = container.querySelector("#card-module-a");
  const cardB = container.querySelector("#card-module-b");
  const cardCD = container.querySelector("#card-module-cd");
  const cardE = container.querySelector("#card-module-e");
  const logList = container.querySelector("#fullflow-log-list");

  function setWsStatus(text, tone = "") {
    wsStatusBadge.className = `badge${tone ? ` ${tone}` : ""}`;
    wsStatusBadge.textContent = text;
  }

  function pushLog(text) {
    components.appendLog(logList, { text }, 80);
  }

  function renderStaticCards() {
    renderModuleCardBody(
      cardA,
      components,
      [
        { label: "状态", value: "占位" },
        { label: "说明", value: "主画面已实时展示A图像" },
      ],
      createPlaceholderPayload("moduleA", "本阶段仅将A图像用于驾驶场景主画面"),
    );

    renderModuleCardBody(
      cardCD,
      components,
      [
        { label: "状态", value: "占位" },
        { label: "说明", value: "未接入实时流" },
      ],
      createPlaceholderPayload("moduleC", "后续迭代再接入实时推送"),
    );

    renderModuleCardBody(
      cardE,
      components,
      [
        { label: "状态", value: "占位" },
        { label: "说明", value: "未接入实时流" },
      ],
      createPlaceholderPayload("moduleE", "后续迭代再接入实时推送"),
    );
  }

  function renderModuleB(moduleBPayload, fallbackFrameId) {
    const frameId = moduleBPayload?.frame_id ?? fallbackFrameId;
    const scene = typeof moduleBPayload?.scene === "string" ? moduleBPayload.scene : "unknown";
    const confidence = normalizeConfidence(moduleBPayload?.conference ?? moduleBPayload?.confidence);
    const speed = toNumber(moduleBPayload?.speed);

    renderModuleCardBody(
      cardB,
      components,
      [
        { label: "frame_id", value: String(frameId ?? "-") },
        { label: "scene", value: scene },
        { label: "confidence", value: confidence === null ? "-" : `${confidence.toFixed(2)}%` },
        { label: "speed", value: speed === null ? "-" : `${Math.round(speed)} km/h` },
      ],
      moduleBPayload,
    );
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
      stageEmpty.hidden = true;
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
      pushLog("WebSocket 已连接，等待A/B配对帧");
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

  renderStaticCards();
  renderModuleB({}, null);
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
