const DEFAULT_WS_PORT = 8765;
const EMPTY_PIXEL = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==";

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

function getWebSocketUrl() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const host = window.location.hostname || "127.0.0.1";
  return `${protocol}://${host}:${DEFAULT_WS_PORT}`;
}

export function mount(container, { components }) {
  const state = {
    socket: null,
    reconnectTimer: null,
    reconnectAttempt: 0,
    destroyed: false,
    scenes: [],
    selectedScene: "",
    controllerState: null,
  };

  const listeners = [];

  container.innerHTML = `
    <section class="module-b-page">
      <article class="card">
        <header class="card-head">
          <div>
            <h3 class="card-title">模块B独立展示</h3>
            <p class="card-subtitle">左侧原始场景序列，右侧为热力图窗口</p>
          </div>
          <span id="module-b-play-state" class="badge">初始化中</span>
        </header>
        <div class="card-body">
          <div class="btn-row">
            <label>
              <span class="card-subtitle">场景</span>
              <select id="module-b-scene-select" class="select"></select>
            </label>
            <button id="module-b-play" class="btn is-primary" type="button">播放</button>
            <button id="module-b-pause" class="btn" type="button">暂停</button>
            <button id="module-b-reset" class="btn" type="button">重置</button>
          </div>
        </div>
      </article>

      <section class="module-b-main">
        <article class="card">
          <header class="card-head">
            <div>
              <h3 class="card-title">驾驶场景原图</h3>
              <p id="module-b-scene-desc" class="card-subtitle">等待选择场景</p>
            </div>
            <span id="module-b-frame-badge" class="badge mono">frame_id -</span>
          </header>
          <div class="card-body">
            <div class="viewer-frame">
              <img id="module-b-image" alt="模块B场景帧" loading="lazy" src="${EMPTY_PIXEL}" />
            </div>
            <div class="module-b-meta">
              <span id="module-b-progress-label">帧进度 -/-</span>
              <span id="module-b-progress-percent">0%</span>
            </div>
            <input id="module-b-progress" class="range" type="range" min="0" value="0" step="1" disabled />
          </div>
        </article>

        <article class="card">
          <header class="card-head">
            <div>
              <h3 class="card-title">热力图预留窗口</h3>
              <p class="card-subtitle">后端热力图能力接入后直接替换该区域</p>
            </div>
          </header>
          <div class="card-body">
            <div class="heatmap-frame">
              <div class="scanning-line" aria-hidden="true"></div>
              <div class="heatmap-placeholder">
                <h4>模块B 热力图待接入</h4>
                <p>当前版本保留展示容器，不接入后端实时热力图流。</p>
              </div>
            </div>
          </div>
        </article>
      </section>

      <article class="card">
        <header class="card-head">
          <div>
            <h3 class="card-title">模块B当前帧输出</h3>
            <p class="card-subtitle">场景分类、置信度与速度估计</p>
          </div>
        </header>
        <div class="card-body">
          <div id="module-b-metrics"></div>
          <ol id="module-b-log-list" class="module-b-logs"></ol>
        </div>
      </article>
    </section>
  `;

  const sceneSelect = container.querySelector("#module-b-scene-select");
  const playBtn = container.querySelector("#module-b-play");
  const pauseBtn = container.querySelector("#module-b-pause");
  const resetBtn = container.querySelector("#module-b-reset");
  const progress = container.querySelector("#module-b-progress");

  const playState = container.querySelector("#module-b-play-state");
  const sceneDesc = container.querySelector("#module-b-scene-desc");
  const frameBadge = container.querySelector("#module-b-frame-badge");
  const image = container.querySelector("#module-b-image");
  const progressLabel = container.querySelector("#module-b-progress-label");
  const progressPercent = container.querySelector("#module-b-progress-percent");

  const metricsRoot = container.querySelector("#module-b-metrics");
  const logList = container.querySelector("#module-b-log-list");

  function bind(target, eventName, handler) {
    target.addEventListener(eventName, handler);
    listeners.push(() => target.removeEventListener(eventName, handler));
  }

  function appendLog(text) {
    const item = document.createElement("li");
    item.className = "module-b-log-item";
    item.textContent = `${components.formatClock()} ${text}`;
    logList.appendChild(item);
    while (logList.childElementCount > 80) {
      logList.removeChild(logList.firstElementChild);
    }
    logList.scrollTop = logList.scrollHeight;
  }

  async function apiJson(url, options = {}) {
    const resp = await fetch(url, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(options.headers ?? {}),
      },
    });
    const payload = await resp.json().catch(() => ({}));
    if (!resp.ok || payload?.ok === false) {
      const msg = payload?.error || `HTTP ${resp.status}`;
      throw new Error(msg);
    }
    return payload;
  }

  function renderEmptyMetrics() {
    components.clearNode(metricsRoot);
    metricsRoot.appendChild(
      components.createMetricList([
        { label: "scene", value: "-" },
        { label: "confidence", value: "-" },
        { label: "speed", value: "-" },
      ]),
    );
  }

  function setPlayStateText(text, tone = "") {
    playState.className = `badge${tone ? ` ${tone}` : ""}`;
    playState.textContent = text;
  }

  function renderControllerState(ctrl) {
    if (!ctrl || typeof ctrl !== "object") {
      return;
    }

    state.controllerState = ctrl;

    const mode = typeof ctrl.mode === "string" ? ctrl.mode : "unknown";
    const playing = ctrl.playing === true;
    const frameTotal = toNumber(ctrl.frame_total) ?? 0;
    const frameIndex = toNumber(ctrl.frame_index) ?? 0;
    const folder = typeof ctrl.scene_folder === "string" ? ctrl.scene_folder : "";

    if (mode !== "local") {
      setPlayStateText("ZMQ模式", "warn");
    } else if (playing) {
      setPlayStateText("播放中", "success");
    } else if (frameTotal > 0 && frameIndex === 0) {
      setPlayStateText("未播放");
    } else {
      setPlayStateText("已暂停", "warn");
    }

    if (folder) {
      sceneDesc.textContent = `${folder} · 本地目录播放`;
    }

    const safeTotal = frameTotal > 0 ? frameTotal : 1;
    const safeIndex = Math.max(0, Math.min(frameIndex, safeTotal - 1));
    progress.max = String(Math.max(0, safeTotal - 1));
    progress.value = String(safeIndex);
    progressLabel.textContent = `帧进度 ${safeIndex + 1}/${safeTotal}`;
    progressPercent.textContent = `${Math.round(((safeIndex + 1) / safeTotal) * 100)}%`;
  }

  function renderBFrame(moduleBPayload, frameId) {
    if (!moduleBPayload || typeof moduleBPayload !== "object") {
      return;
    }

    const scene = typeof moduleBPayload.scene === "string" ? moduleBPayload.scene : "unknown";
    const confidence = normalizeConfidence(moduleBPayload.confidence ?? moduleBPayload.conference);
    const speed = toNumber(moduleBPayload.speed);

    const sourceMode = typeof moduleBPayload.source_mode === "string" ? moduleBPayload.source_mode : "";
    const sceneFolder = typeof moduleBPayload.scene_folder === "string" ? moduleBPayload.scene_folder : "";
    const imageRelpath = typeof moduleBPayload.image_relpath === "string" ? moduleBPayload.image_relpath : "";
    const frameIndex = toNumber(moduleBPayload.frame_index);
    const frameTotal = toNumber(moduleBPayload.frame_total);

    if (imageRelpath) {
      const cleaned = imageRelpath.replace(/^\/+/, "");
      image.src = `./${cleaned}`;
      image.alt = `模块B场景帧 ${frameId}`;
    }

    frameBadge.textContent = `frame_id ${frameId}`;

    if (sceneFolder) {
      sceneDesc.textContent = `${sceneFolder} · 本地目录播放`;
    }

    if (frameIndex !== null && frameTotal !== null && frameTotal > 0) {
      progress.max = String(Math.max(0, frameTotal - 1));
      progress.value = String(Math.max(0, Math.min(frameIndex, frameTotal - 1)));
      progressLabel.textContent = `帧进度 ${Math.min(frameIndex + 1, frameTotal)}/${frameTotal}`;
      progressPercent.textContent = `${Math.round((Math.min(frameIndex + 1, frameTotal) / frameTotal) * 100)}%`;
    }

    components.clearNode(metricsRoot);
    metricsRoot.appendChild(
      components.createMetricList([
        { label: "source_mode", value: sourceMode || "-" },
        { label: "scene", value: scene },
        { label: "confidence", value: confidence === null ? "-" : `${confidence.toFixed(2)}%` },
        { label: "speed", value: speed === null ? "-" : `${Math.round(speed)} km/h` },
      ]),
    );

    if (sourceMode === "local") {
      setPlayStateText("播放中", "success");
    }
  }

  function renderSceneOptions() {
    const previous = state.selectedScene;
    components.clearNode(sceneSelect);

    if (!state.scenes.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "无可用场景目录";
      sceneSelect.appendChild(option);
      sceneSelect.disabled = true;
      return;
    }

    sceneSelect.disabled = false;
    for (const scene of state.scenes) {
      const option = document.createElement("option");
      option.value = scene.name;
      option.textContent = `${scene.name} (${scene.frame_count})`;
      sceneSelect.appendChild(option);
    }

    if (previous && state.scenes.some((item) => item.name === previous)) {
      state.selectedScene = previous;
    } else {
      state.selectedScene = state.scenes[0].name;
    }
    sceneSelect.value = state.selectedScene;
  }

  async function refreshSceneList() {
    const payload = await apiJson("/api/scenes", { method: "GET", headers: {} });
    const scenes = Array.isArray(payload?.scenes) ? payload.scenes : [];
    state.scenes = scenes
      .map((item) => ({
        name: typeof item?.name === "string" ? item.name : "",
        frame_count: toNumber(item?.frame_count) ?? 0,
      }))
      .filter((item) => item.name);

    renderSceneOptions();
  }

  async function fetchControllerState() {
    const payload = await apiJson("/api/module-b/state", { method: "GET", headers: {} });
    renderControllerState(payload?.state ?? null);
  }

  async function switchToLocalMode() {
    const payload = await apiJson("/api/module-b/mode", {
      method: "POST",
      body: JSON.stringify({ mode: "local" }),
    });
    renderControllerState(payload?.state ?? null);
    appendLog("已切换 moduleB 到本地目录模式");
  }

  async function selectScene(sceneName) {
    if (!sceneName) {
      return;
    }
    const payload = await apiJson("/api/module-b/scene", {
      method: "POST",
      body: JSON.stringify({ scene: sceneName }),
    });
    state.selectedScene = sceneName;
    renderControllerState(payload?.state ?? null);
    appendLog(`已切换场景: ${sceneName}`);
  }

  async function sendPlayerAction(action) {
    const payload = await apiJson("/api/module-b/player", {
      method: "POST",
      body: JSON.stringify({ action }),
    });
    renderControllerState(payload?.state ?? null);
    const actionText = action === "play" ? "播放" : action === "pause" ? "暂停" : "重置";
    appendLog(`已发送控制: ${actionText}`);
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
    const socket = new WebSocket(wsUrl);
    state.socket = socket;

    socket.addEventListener("open", () => {
      if (state.destroyed) {
        return;
      }
      state.reconnectAttempt = 0;
      appendLog(`WebSocket 已连接: ${wsUrl}`);
    });

    socket.addEventListener("message", (event) => {
      if (state.destroyed) {
        return;
      }

      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch (_err) {
        return;
      }

      if (payload?.event !== "b_frame") {
        return;
      }

      const frameId = toNumber(payload?.frame_id);
      if (frameId === null) {
        return;
      }

      const moduleBPayload = payload?.moduleB && typeof payload.moduleB === "object" ? payload.moduleB : {};
      renderBFrame(moduleBPayload, Math.trunc(frameId));
    });

    socket.addEventListener("close", () => {
      if (state.destroyed) {
        return;
      }
      appendLog("WebSocket 已断开，准备自动重连");
      scheduleReconnect();
    });

    socket.addEventListener("error", () => {
      if (state.destroyed) {
        return;
      }
      appendLog("WebSocket 连接异常");
    });
  }

  bind(sceneSelect, "focus", () => {
    refreshSceneList().catch((err) => {
      appendLog(`刷新场景列表失败: ${err.message}`);
    });
  });

  bind(sceneSelect, "pointerdown", () => {
    refreshSceneList().catch((err) => {
      appendLog(`刷新场景列表失败: ${err.message}`);
    });
  });

  bind(sceneSelect, "change", (event) => {
    const sceneName = event.target.value;
    selectScene(sceneName).catch((err) => {
      appendLog(`切换场景失败: ${err.message}`);
    });
  });

  bind(playBtn, "click", () => {
    sendPlayerAction("play").catch((err) => {
      appendLog(`播放失败: ${err.message}`);
    });
  });

  bind(pauseBtn, "click", () => {
    sendPlayerAction("pause").catch((err) => {
      appendLog(`暂停失败: ${err.message}`);
    });
  });

  bind(resetBtn, "click", () => {
    sendPlayerAction("reset").catch((err) => {
      appendLog(`重置失败: ${err.message}`);
    });
  });

  renderEmptyMetrics();

  (async () => {
    try {
      await switchToLocalMode();
      await refreshSceneList();
      if (state.selectedScene) {
        await selectScene(state.selectedScene);
      }
      await fetchControllerState();
      connectWebSocket();
    } catch (err) {
      appendLog(`初始化失败: ${err.message}`);
      setPlayStateText("初始化失败", "danger");
    }
  })();

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
    for (const off of listeners) {
      off();
    }
  };
}
