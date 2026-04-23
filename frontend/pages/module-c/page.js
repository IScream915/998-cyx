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
    <section class="module-c-page">
      <article class="card">
        <header class="card-head">
          <div>
            <h3 class="card-title">模块C独立展示</h3>
            <p class="card-subtitle">左侧原始场景序列，右侧为YOLO识别框预留窗口</p>
          </div>
          <span id="module-c-play-state" class="badge">初始化中</span>
        </header>
        <div class="card-body">
          <div class="btn-row">
            <label>
              <span class="card-subtitle">场景</span>
              <select id="module-c-scene-select" class="select"></select>
            </label>
            <button id="module-c-play" class="btn is-primary" type="button">播放</button>
            <button id="module-c-pause" class="btn" type="button">暂停</button>
            <button id="module-c-reset" class="btn" type="button">重置</button>
          </div>
        </div>
      </article>

      <section class="module-c-main">
        <article class="card">
          <header class="card-head">
            <div>
              <h3 class="card-title">驾驶场景原图</h3>
              <p id="module-c-scene-desc" class="card-subtitle">等待选择场景</p>
            </div>
            <span id="module-c-frame-badge" class="badge mono">frame_id -</span>
          </header>
          <div class="card-body">
            <div class="viewer-frame">
              <img id="module-c-image" alt="模块C场景帧" loading="lazy" src="${EMPTY_PIXEL}" />
            </div>
            <div class="module-c-meta">
              <span id="module-c-progress-label">帧进度 -/-</span>
              <span id="module-c-progress-percent">0%</span>
            </div>
            <input id="module-c-progress" class="range" type="range" min="0" value="0" step="1" disabled />
          </div>
        </article>

        <article class="card">
          <header class="card-head">
            <div>
              <h3 class="card-title">YOLO识别框预留窗口</h3>
              <p class="card-subtitle">后续接入检测框叠加图实时展示</p>
            </div>
          </header>
          <div class="card-body">
            <div class="yolo-frame">
              <img
                id="module-c-yolo-image"
                class="yolo-image"
                alt="模块C检测框"
                loading="lazy"
                src="${EMPTY_PIXEL}"
                hidden
              />
              <div id="module-c-yolo-scanline" class="scanning-line" aria-hidden="true"></div>
              <div id="module-c-yolo-placeholder" class="yolo-placeholder"></div>
            </div>
          </div>
        </article>
      </section>

      <article class="card">
        <header class="card-head">
          <div>
            <h3 class="card-title">模块C当前帧输出</h3>
            <p class="card-subtitle">交通标志、行人、车辆检测统计</p>
          </div>
        </header>
        <div class="card-body">
          <div id="module-c-metrics"></div>
          <ol id="module-c-log-list" class="module-c-logs"></ol>
        </div>
      </article>
    </section>
  `;

  const sceneSelect = container.querySelector("#module-c-scene-select");
  const playBtn = container.querySelector("#module-c-play");
  const pauseBtn = container.querySelector("#module-c-pause");
  const resetBtn = container.querySelector("#module-c-reset");
  const progress = container.querySelector("#module-c-progress");

  const playState = container.querySelector("#module-c-play-state");
  const sceneDesc = container.querySelector("#module-c-scene-desc");
  const frameBadge = container.querySelector("#module-c-frame-badge");
  const image = container.querySelector("#module-c-image");
  const yoloImage = container.querySelector("#module-c-yolo-image");
  const yoloScanline = container.querySelector("#module-c-yolo-scanline");
  const yoloPlaceholder = container.querySelector("#module-c-yolo-placeholder");
  const progressLabel = container.querySelector("#module-c-progress-label");
  const progressPercent = container.querySelector("#module-c-progress-percent");

  const metricsRoot = container.querySelector("#module-c-metrics");
  const logList = container.querySelector("#module-c-log-list");

  function bind(target, eventName, handler) {
    target.addEventListener(eventName, handler);
    listeners.push(() => target.removeEventListener(eventName, handler));
  }

  function appendLog(text) {
    const item = document.createElement("li");
    item.className = "module-c-log-item";
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
        { label: "num_traffic_signs", value: "-" },
        { label: "num_pedestrians", value: "-" },
        { label: "num_vehicles", value: "-" },
      ]),
    );
  }

  function renderYoloOverlay(yoloBase64) {
    const hasOverlay = typeof yoloBase64 === "string" && yoloBase64.trim().length > 0;
    if (hasOverlay) {
      yoloImage.src = `data:image/jpeg;base64,${yoloBase64}`;
      yoloImage.hidden = false;
      yoloPlaceholder.hidden = true;
      yoloScanline.hidden = true;
      return;
    }

    yoloImage.hidden = true;
    yoloImage.src = EMPTY_PIXEL;
    yoloPlaceholder.hidden = false;
    yoloScanline.hidden = false;
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

  function renderCFrame(moduleCPayload, frameId) {
    if (!moduleCPayload || typeof moduleCPayload !== "object") {
      return;
    }

    const sourceMode = typeof moduleCPayload.source_mode === "string" ? moduleCPayload.source_mode : "";
    const sceneFolder = typeof moduleCPayload.scene_folder === "string" ? moduleCPayload.scene_folder : "";
    const imageRelpath = typeof moduleCPayload.image_relpath === "string" ? moduleCPayload.image_relpath : "";
    const frameIndex = toNumber(moduleCPayload.frame_index);
    const frameTotal = toNumber(moduleCPayload.frame_total);
    const numTrafficSigns = toNumber(moduleCPayload.num_traffic_signs);
    const numPedestrians = toNumber(moduleCPayload.num_pedestrians);
    const numVehicles = toNumber(moduleCPayload.num_vehicles);
    const yoloOverlayBase64 =
      typeof moduleCPayload.yolo_overlay_base64 === "string" ? moduleCPayload.yolo_overlay_base64 : "";

    if (imageRelpath) {
      const cleaned = imageRelpath.replace(/^\/+/, "");
      image.src = `./${cleaned}`;
      image.alt = `模块C场景帧 ${frameId}`;
    }

    if (sourceMode === "local") {
      renderYoloOverlay(yoloOverlayBase64);
    } else {
      renderYoloOverlay("");
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
        { label: "num_traffic_signs", value: numTrafficSigns === null ? "-" : String(Math.trunc(numTrafficSigns)) },
        { label: "num_pedestrians", value: numPedestrians === null ? "-" : String(Math.trunc(numPedestrians)) },
        { label: "num_vehicles", value: numVehicles === null ? "-" : String(Math.trunc(numVehicles)) },
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
    const payload = await apiJson("/api/module-c/state", { method: "GET", headers: {} });
    renderControllerState(payload?.state ?? null);
  }

  async function switchToLocalMode() {
    const payload = await apiJson("/api/module-c/mode", {
      method: "POST",
      body: JSON.stringify({ mode: "local" }),
    });
    renderControllerState(payload?.state ?? null);
    appendLog("已切换 moduleC 到本地目录模式");
  }

  async function selectScene(sceneName) {
    if (!sceneName) {
      return;
    }
    const payload = await apiJson("/api/module-c/scene", {
      method: "POST",
      body: JSON.stringify({ scene: sceneName }),
    });
    state.selectedScene = sceneName;
    renderControllerState(payload?.state ?? null);
    appendLog(`已切换场景: ${sceneName}`);
  }

  async function sendPlayerAction(action) {
    const payload = await apiJson("/api/module-c/player", {
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

      if (payload?.event !== "c_frame") {
        return;
      }

      const frameId = toNumber(payload?.frame_id);
      if (frameId === null) {
        return;
      }

      const moduleCPayload = payload?.moduleC && typeof payload.moduleC === "object" ? payload.moduleC : {};
      renderCFrame(moduleCPayload, Math.trunc(frameId));
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
  renderYoloOverlay("");

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
