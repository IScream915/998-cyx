const DEFAULT_WS_PORT = 8765;
const EMPTY_PIXEL = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==";
const HAN_TEXT_RE = /[\u3400-\u9fff]/u;
const ERROR_TEXT_MAP = [
  [/^scene .*$/i, "Invalid scene"],
  [/scene \u4e0d\u80fd\u4e3a\u7a7a/u, "scene is required"],
  [/scene \u975e\u6cd5/u, "Invalid scene"],
  [/scene \u8d8a\u754c/u, "Scene path is outside the scenes root"],
  [/\u573a\u666f\u76ee\u5f55\u4e0d\u5b58\u5728/u, "Scene directory does not exist"],
  [/\u573a\u666f\u76ee\u5f55\u4e0b\u6ca1\u6709\u53ef\u64ad\u653e\u56fe\u7247/u, "No playable images found in the scene directory"],
  [/\u5f53\u524d\u672a\u9009\u62e9\u573a\u666f\u6216\u573a\u666f\u65e0\u56fe\u7247/u, "No scene is selected or the selected scene has no images"],
  [/mode \u4ec5\u652f\u6301 zmq\/local/u, "mode only supports zmq/local"],
  [/action \u4ec5\u652f\u6301 play\/pause\/reset/u, "action only supports play/pause/reset"],
  [/Content-Length \u975e\u6cd5/u, "Invalid Content-Length"],
  [/\u8bf7\u6c42\u4f53\u4e0d\u662f\u5408\u6cd5JSON/u, "Request body is not valid JSON"],
  [/\u8bf7\u6c42\u4f53JSON\u9876\u5c42\u5fc5\u987b\u662f\u5bf9\u8c61/u, "Request body JSON top level must be an object"],
  [/\u63a5\u53e3\u4e0d\u5b58\u5728/u, "Endpoint does not exist"],
  [/\u670d\u52a1\u5185\u90e8\u9519\u8bef/u, "Internal service error"],
  [/\u63a7\u5236\u670d\u52a1\u4e0d\u53ef\u7528/u, "Control service is unavailable"],
];

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

function toEnglishMessage(message) {
  const text = String(message ?? "");
  if (!HAN_TEXT_RE.test(text)) {
    return text;
  }
  for (const [pattern, replacement] of ERROR_TEXT_MAP) {
    if (pattern.test(text)) {
      return replacement;
    }
  }
  return "Backend returned a non-English message. Check service logs for details.";
}

function displaySceneName(name, index = 0) {
  const text = String(name || "");
  return HAN_TEXT_RE.test(text) ? `Scene ${index + 1}` : text;
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
    <section class="module-d-page">
      <article class="card">
        <header class="card-head">
          <div>
            <h3 class="card-title">Module D Standalone Demo</h3>
            <p class="card-subtitle">Original scene sequence on the left, YOLO detection overlay on the right</p>
          </div>
          <span id="module-d-play-state" class="badge">Initializing</span>
        </header>
        <div class="card-body">
          <div class="btn-row">
            <label>
              <span class="card-subtitle">Scene</span>
              <select id="module-d-scene-select" class="select"></select>
            </label>
            <button id="module-d-play" class="btn is-primary" type="button">Play</button>
            <button id="module-d-pause" class="btn" type="button">Pause</button>
            <button id="module-d-reset" class="btn" type="button">Reset</button>
          </div>
        </div>
      </article>

      <section class="module-d-main">
        <article class="card">
          <header class="card-head">
            <div>
              <h3 class="card-title">Original Driving Scene</h3>
              <p id="module-d-scene-desc" class="card-subtitle">Waiting for scene selection</p>
            </div>
            <span id="module-d-frame-badge" class="badge mono">frame_id -</span>
          </header>
          <div class="card-body">
            <div class="viewer-frame">
              <img id="module-d-image" alt="Module D scene frame" loading="lazy" src="${EMPTY_PIXEL}" />
            </div>
            <div class="module-d-meta">
              <span id="module-d-progress-label">Frame progress -/-</span>
              <span id="module-d-progress-percent">0%</span>
            </div>
            <input id="module-d-progress" class="range" type="range" min="0" value="0" step="1" disabled />
          </div>
        </article>

        <article class="card">
          <header class="card-head">
            <div>
              <h3 class="card-title">YOLO Detection Overlay</h3>
              <p class="card-subtitle">Live detection-box overlay</p>
            </div>
          </header>
          <div class="card-body">
            <div class="yolo-frame">
              <img
                id="module-d-yolo-image"
                class="yolo-image"
                alt="Module D detection overlay"
                loading="lazy"
                src="${EMPTY_PIXEL}"
                hidden
              />
              <div id="module-d-yolo-scanline" class="scanning-line" aria-hidden="true"></div>
              <div id="module-d-yolo-placeholder" class="yolo-placeholder"></div>
            </div>
          </div>
        </article>
      </section>

      <article class="card">
        <header class="card-head">
          <div>
            <h3 class="card-title">Module D Current Frame Output</h3>
            <p class="card-subtitle">Traffic sign, pedestrian, and vehicle detection stats</p>
          </div>
        </header>
        <div class="card-body">
          <div id="module-d-metrics"></div>
          <ol id="module-d-log-list" class="module-d-logs"></ol>
        </div>
      </article>
    </section>
  `;

  const sceneSelect = container.querySelector("#module-d-scene-select");
  const playBtn = container.querySelector("#module-d-play");
  const pauseBtn = container.querySelector("#module-d-pause");
  const resetBtn = container.querySelector("#module-d-reset");
  const progress = container.querySelector("#module-d-progress");

  const playState = container.querySelector("#module-d-play-state");
  const sceneDesc = container.querySelector("#module-d-scene-desc");
  const frameBadge = container.querySelector("#module-d-frame-badge");
  const image = container.querySelector("#module-d-image");
  const yoloImage = container.querySelector("#module-d-yolo-image");
  const yoloScanline = container.querySelector("#module-d-yolo-scanline");
  const yoloPlaceholder = container.querySelector("#module-d-yolo-placeholder");
  const progressLabel = container.querySelector("#module-d-progress-label");
  const progressPercent = container.querySelector("#module-d-progress-percent");

  const metricsRoot = container.querySelector("#module-d-metrics");
  const logList = container.querySelector("#module-d-log-list");

  function bind(target, eventName, handler) {
    target.addEventListener(eventName, handler);
    listeners.push(() => target.removeEventListener(eventName, handler));
  }

  function appendLog(text) {
    const item = document.createElement("li");
    item.className = "module-d-log-item";
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
      const msg = toEnglishMessage(payload?.error || `HTTP ${resp.status}`);
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
      setPlayStateText("ZMQ Mode", "warn");
    } else if (playing) {
      setPlayStateText("Playing", "success");
    } else if (frameTotal > 0 && frameIndex === 0) {
      setPlayStateText("Not Played");
    } else {
      setPlayStateText("Paused", "warn");
    }

    if (folder) {
      sceneDesc.textContent = `${displaySceneName(folder)} · Local directory playback`;
    }

    const safeTotal = frameTotal > 0 ? frameTotal : 1;
    const safeIndex = Math.max(0, Math.min(frameIndex, safeTotal - 1));
    progress.max = String(Math.max(0, safeTotal - 1));
    progress.value = String(safeIndex);
    progressLabel.textContent = `Frame progress ${safeIndex + 1}/${safeTotal}`;
    progressPercent.textContent = `${Math.round(((safeIndex + 1) / safeTotal) * 100)}%`;
  }

  function renderDFrame(moduleDPayload, frameId) {
    if (!moduleDPayload || typeof moduleDPayload !== "object") {
      return;
    }

    const sourceMode = typeof moduleDPayload.source_mode === "string" ? moduleDPayload.source_mode : "";
    const sceneFolder = typeof moduleDPayload.scene_folder === "string" ? moduleDPayload.scene_folder : "";
    const imageRelpath = typeof moduleDPayload.image_relpath === "string" ? moduleDPayload.image_relpath : "";
    const frameIndex = toNumber(moduleDPayload.frame_index);
    const frameTotal = toNumber(moduleDPayload.frame_total);
    const numTrafficSigns = toNumber(moduleDPayload.num_traffic_signs);
    const numPedestrians = toNumber(moduleDPayload.num_pedestrians);
    const numVehicles = toNumber(moduleDPayload.num_vehicles);
    const yoloOverlayBase64 =
      typeof moduleDPayload.yolo_overlay_base64 === "string" ? moduleDPayload.yolo_overlay_base64 : "";

    if (imageRelpath) {
      const cleaned = imageRelpath.replace(/^\/+/, "");
      image.src = `./${cleaned}`;
      image.alt = `Module D scene frame ${frameId}`;
    }

    if (sourceMode === "local") {
      renderYoloOverlay(yoloOverlayBase64);
    } else {
      renderYoloOverlay("");
    }

    frameBadge.textContent = `frame_id ${frameId}`;

    if (sceneFolder) {
      sceneDesc.textContent = `${displaySceneName(sceneFolder)} · Local directory playback`;
    }

    if (frameIndex !== null && frameTotal !== null && frameTotal > 0) {
      progress.max = String(Math.max(0, frameTotal - 1));
      progress.value = String(Math.max(0, Math.min(frameIndex, frameTotal - 1)));
      progressLabel.textContent = `Frame progress ${Math.min(frameIndex + 1, frameTotal)}/${frameTotal}`;
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
      setPlayStateText("Playing", "success");
    }
  }

  function renderSceneOptions() {
    const previous = state.selectedScene;
    components.clearNode(sceneSelect);

    if (!state.scenes.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "No scene directories available";
      sceneSelect.appendChild(option);
      sceneSelect.disabled = true;
      return;
    }

    sceneSelect.disabled = false;
    state.scenes.forEach((scene, index) => {
      const option = document.createElement("option");
      option.value = scene.name;
      option.textContent = `${displaySceneName(scene.name, index)} (${scene.frame_count})`;
      sceneSelect.appendChild(option);
    });

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
    const payload = await apiJson("/api/module-d/state", { method: "GET", headers: {} });
    renderControllerState(payload?.state ?? null);
  }

  async function switchToLocalMode() {
    const payload = await apiJson("/api/module-d/mode", {
      method: "POST",
      body: JSON.stringify({ mode: "local" }),
    });
    renderControllerState(payload?.state ?? null);
    appendLog("Switched moduleD to local directory mode");
  }

  async function selectScene(sceneName) {
    if (!sceneName) {
      return;
    }
    const payload = await apiJson("/api/module-d/scene", {
      method: "POST",
      body: JSON.stringify({ scene: sceneName }),
    });
    state.selectedScene = sceneName;
    renderControllerState(payload?.state ?? null);
    appendLog(`Scene switched: ${displaySceneName(sceneName)}`);
  }

  async function sendPlayerAction(action) {
    const payload = await apiJson("/api/module-d/player", {
      method: "POST",
      body: JSON.stringify({ action }),
    });
    renderControllerState(payload?.state ?? null);
    const actionText = action === "play" ? "Play" : action === "pause" ? "Pause" : "Reset";
    appendLog(`Control sent: ${actionText}`);
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
      appendLog(`WebSocket connected: ${wsUrl}`);
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

      if (payload?.event !== "d_frame") {
        return;
      }

      const frameId = toNumber(payload?.frame_id);
      if (frameId === null) {
        return;
      }

      const moduleDPayload = payload?.moduleD && typeof payload.moduleD === "object" ? payload.moduleD : {};
      renderDFrame(moduleDPayload, Math.trunc(frameId));
    });

    socket.addEventListener("close", () => {
      if (state.destroyed) {
        return;
      }
      appendLog("WebSocket disconnected. Reconnecting automatically");
      scheduleReconnect();
    });

    socket.addEventListener("error", () => {
      if (state.destroyed) {
        return;
      }
      appendLog("WebSocket connection error");
    });
  }

  bind(sceneSelect, "focus", () => {
    refreshSceneList().catch((err) => {
      appendLog(`Failed to refresh scene list: ${err.message}`);
    });
  });

  bind(sceneSelect, "pointerdown", () => {
    refreshSceneList().catch((err) => {
      appendLog(`Failed to refresh scene list: ${err.message}`);
    });
  });

  bind(sceneSelect, "change", (event) => {
    const sceneName = event.target.value;
    selectScene(sceneName).catch((err) => {
      appendLog(`Failed to switch scene: ${err.message}`);
    });
  });

  bind(playBtn, "click", () => {
    sendPlayerAction("play").catch((err) => {
      appendLog(`Play failed: ${err.message}`);
    });
  });

  bind(pauseBtn, "click", () => {
    sendPlayerAction("pause").catch((err) => {
      appendLog(`Pause failed: ${err.message}`);
    });
  });

  bind(resetBtn, "click", () => {
    sendPlayerAction("reset").catch((err) => {
      appendLog(`Reset failed: ${err.message}`);
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
      appendLog(`Initialization failed: ${err.message}`);
      setPlayStateText("Initialization Failed", "danger");
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
