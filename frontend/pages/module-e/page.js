const FALLBACK_TEMPLATES = [
  {
    template_id: "p0_blind_spot",
    label: "P0 Blind Spot Critical",
    defaults: { scene: "city street", speed: 38, limit_speed: 60, num_pedestrians: 1, num_vehicles: 4 },
  },
  {
    template_id: "p1_overspeed",
    label: "P1 Overspeed Reminder",
    defaults: { scene: "highway", speed: 98, limit_speed: 80, num_pedestrians: 0, num_vehicles: 10 },
  },
  {
    template_id: "p2_warning",
    label: "P2 General Warning",
    defaults: { scene: "city street", speed: 46, limit_speed: 60, num_pedestrians: 2, num_vehicles: 7 },
  },
  {
    template_id: "p3_silent",
    label: "P3 Silent Advisory",
    defaults: { scene: "highway", speed: 78, limit_speed: 80, num_pedestrians: 0, num_vehicles: 6 },
  },
];

const SCENE_OPTIONS = ["city street", "highway", "tunnel", "residential", "unknown"];
const SPEED_OPTIONS = [20, 30, 38, 40, 46, 50, 60, 70, 78, 80, 90, 98, 100, 110, 120];
const LIMIT_OPTIONS = [20, 40, 60, 80, 100, 120];
const PEDESTRIAN_OPTIONS = [0, 1, 2, 3, 4, 5, 6];
const VEHICLE_OPTIONS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14, 16];
const DISPLAY_TEXT_MAP = new Map([
  ["P0\u76f2\u533a\u9ad8\u5371", "P0 Blind Spot Critical"],
  ["P1\u8d85\u901f\u63d0\u9192", "P1 Overspeed Reminder"],
  ["P2\u666e\u901a\u9884\u8b66", "P2 General Warning"],
  ["P3\u9759\u9ed8\u5efa\u8bae", "P3 Silent Advisory"],
  ["\u524d\u65b9\u9650\u901f\u4e8c\u5341\uff0c\u8bf7\u6ce8\u610f\u51cf\u901f", "Speed limit 20 ahead. Please slow down."],
  ["\u524d\u65b9\u9650\u901f\u56db\u5341\u516c\u91cc", "Speed limit 40 km/h ahead."],
  ["\u524d\u65b9\u9650\u901f\u516d\u5341\u516c\u91cc", "Speed limit 60 km/h ahead."],
  ["\u524d\u65b9\u9650\u901f\u516b\u5341\u516c\u91cc", "Speed limit 80 km/h ahead."],
  ["\u524d\u65b9\u9650\u901f\u4e00\u767e\u516c\u91cc", "Speed limit 100 km/h ahead."],
  ["\u524d\u65b9\u9650\u901f\u4e00\u767e\u4e8c\u5341\u516c\u91cc", "Speed limit 120 km/h ahead."],
  ["\u524d\u65b9\u9650\u901f40\u516c\u91cc\uff0c\u8bf7\u51cf\u901f\u6162\u884c", "Speed limit 40 km/h ahead. Please slow down."],
  ["\u60a8\u5df2\u8d85\u901f\uff0c\u5f53\u524d\u9650\u901f80\u516c\u91cc", "You are speeding. Current speed limit is 80 km/h."],
  ["\u96a7\u9053\u8def\u6bb5\u8bf7\u964d\u901f\uff0c\u5f53\u524d\u9650\u901f60\u516c\u91cc", "Slow down in the tunnel segment. Current speed limit is 60 km/h."],
  ["\u524d\u65b9\u6ce8\u610f\u884c\u4eba\uff0c\u8bf7\u968f\u65f6\u51c6\u5907\u5239\u8f66", "Pedestrians ahead. Be prepared to brake."],
  ["\u524d\u65b9\u4eba\u884c\u6a2a\u9053\uff0c\u8bf7\u793c\u8ba9\u884c\u4eba", "Pedestrian crossing ahead. Yield to pedestrians."],
  ["\u524d\u65b9\u5b66\u6821\u6216\u513f\u7ae5\u6d3b\u52a8\u533a\u57df\uff0c\u8bf7\u51cf\u901f\u907f\u8ba9", "School or children activity area ahead. Slow down and yield."],
  ["\u6ce8\u610f\uff0c\u524d\u65b9\u9053\u8def\u65bd\u5de5\uff0c\u8bf7\u51cf\u901f\u6162\u884c", "Roadwork ahead. Slow down."],
  ["\u524d\u65b9\u8fde\u7eed\u5f2f\u9053\uff0c\u8bf7\u63a7\u5236\u8f66\u901f\uff0c\u6ce8\u610f\u5b89\u5168", "Winding road ahead. Control speed and stay alert."],
  ["\u524d\u65b9\u4e3a\u4e8b\u6545\u6613\u53d1\u8def\u6bb5\uff0c\u8bf7\u8c28\u614e\u9a7e\u9a76", "Accident-prone section ahead. Drive carefully."],
  ["\u524d\u65b9\u8def\u51b5\u590d\u6742\uff0c\u8bf7\u6ce8\u610f\u5371\u9669", "Complex road conditions ahead. Watch for hazards."],
  ["\u524d\u65b9\u5c71\u533a\u9053\u8def\uff0c\u8bf7\u6ce8\u610f\u843d\u77f3", "Mountain road ahead. Watch for falling rocks."],
  ["\u524d\u65b9\u6ce8\u610f\u6a2a\u98ce\uff0c\u8bf7\u7d27\u63e1\u65b9\u5411\u76d8", "Crosswind ahead. Keep a firm grip on the steering wheel."],
  ["\u8b66\u544a\uff0c\u524d\u65b9\u7981\u6b62\u8f66\u8f86\u9a76\u5165", "Warning: vehicles are prohibited from entering ahead."],
  ["\u8be5\u8def\u53e3\u7981\u6b62\u6389\u5934", "No U-turn at this intersection."],
  ["\u8be5\u8def\u6bb5\u7981\u6b62\u8d85\u8f66\uff0c\u8bf7\u4fdd\u6301\u8f66\u9053", "No overtaking on this section. Keep your lane."],
  ["\u524d\u65b9\u7981\u6b62\u8d85\u8f66\uff0c\u8bf7\u4fdd\u6301\u8f66\u8ddd", "No overtaking ahead. Keep a safe following distance."],
  ["\u524d\u65b9\u7981\u6b62\u9e23\u7b1b\uff0c\u8bf7\u6587\u660e\u9a7e\u9a76", "No honking ahead. Drive courteously."],
  ["\u96a7\u9053\u5185\u7981\u6b62\u9e23\u7b1b\uff0c\u8bf7\u5e73\u7a33\u9a7e\u9a76", "No honking in the tunnel. Drive smoothly."],
  ["\u8be5\u8def\u6bb5\u5168\u7ebf\u7981\u6b62\u505c\u8f66", "No parking along this section."],
  ["\u524d\u65b9\u8def\u53e3\u7981\u6b62\u5de6\u8f6c\u5f2f", "No left turn ahead."],
  ["\u524d\u65b9\u8def\u53e3\u7981\u6b62\u53f3\u8f6c\u5f2f", "No right turn ahead."],
  ["\u524d\u65b9\u8def\u53e3\uff0c\u8bf7\u505c\u8f66\u89c2\u5bdf\uff0c\u8ba9\u884c\u4e3b\u8def\u8f66\u8f86", "Stop ahead. Check traffic and yield to main-road vehicles."],
  ["\u524d\u65b9\u8def\u53e3\uff0c\u8bf7\u51cf\u901f\u8ba9\u884c", "Yield ahead. Slow down."],
  ["\u4fdd\u6301\u5f53\u524d\u8f66\u901f\uff0c\u6ce8\u610f\u524d\u65b9\u8f66\u6d41", "Maintain current speed and watch the traffic ahead."],
  ["\u8f66\u901f\u6062\u590d\u6b63\u5e38\uff0c\u8bf7\u7ee7\u7eed\u4fdd\u6301\u4e13\u6ce8\u9a7e\u9a76", "Speed is back to normal. Stay focused while driving."],
  ["\u8f66\u901f\u6062\u590d\u6b63\u5e38", "Speed is back to normal."],
  ["\u5373\u5c06\u9a76\u51fa\u96a7\u9053\uff0c\u8bf7\u5173\u6ce8\u5149\u7ebf\u53d8\u5316", "Approaching tunnel exit. Watch for lighting changes."],
]);

function toDisplayText(value) {
  if (typeof value !== "string") {
    return value;
  }
  return DISPLAY_TEXT_MAP.get(value) ?? value;
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

function getWsUrl() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${window.location.host}/api/module-e/ws`;
}

function setSelectOptions(select, values, formatter = (v) => String(v)) {
  select.innerHTML = "";
  for (const value of values) {
    const option = document.createElement("option");
    option.value = String(value);
    option.textContent = formatter(value);
    select.appendChild(option);
  }
}

function findTemplate(templates, templateId) {
  return templates.find((item) => item.template_id === templateId) || templates[0] || null;
}

export function mount(container, { components }) {
  const state = {
    destroyed: false,
    reconnectTimer: null,
    reconnectAttempt: 0,
    socket: null,
    templates: FALLBACK_TEMPLATES,
    statePollTimer: null,
    lastInput: null,
    lastGatewayError: "",
  };

  container.innerHTML = `
    <section class="module-e-page">
      <article class="card">
        <header class="card-head">
          <div>
            <h3 class="card-title">Module E Standalone Demo</h3>
            <p class="card-subtitle">Simulated upstream input triggers moduleE arbitration and announcements</p>
          </div>
          <span id="module-e-gateway-badge" class="badge warn">Connecting</span>
        </header>
        <div class="card-body">
          <div class="module-e-form-grid">
            <label class="module-e-field">
              <span class="card-subtitle">Trigger Template</span>
              <select id="module-e-template" class="select"></select>
            </label>
            <label class="module-e-field">
              <span class="card-subtitle">scene</span>
              <select id="module-e-scene" class="select"></select>
            </label>
            <label class="module-e-field">
              <span class="card-subtitle">speed</span>
              <select id="module-e-speed" class="select"></select>
            </label>
            <label class="module-e-field">
              <span class="card-subtitle">limit_speed</span>
              <select id="module-e-limit" class="select"></select>
            </label>
            <label class="module-e-field">
              <span class="card-subtitle">num_pedestrians</span>
              <select id="module-e-ped" class="select"></select>
            </label>
            <label class="module-e-field">
              <span class="card-subtitle">num_vehicles</span>
              <select id="module-e-veh" class="select"></select>
            </label>
          </div>
          <div class="btn-row module-e-actions">
            <button id="module-e-trigger" class="btn is-primary" type="button">Trigger Once</button>
            <button id="module-e-reset" class="btn" type="button">Reset State</button>
          </div>
        </div>
      </article>

      <section class="module-e-main">
        <article class="card">
          <header class="card-head">
            <div>
              <h3 class="card-title">Latest Input</h3>
              <p class="card-subtitle">POST /api/module-e/simulate</p>
            </div>
          </header>
          <div class="card-body">
            <div id="module-e-input-metrics"></div>
          </div>
        </article>

        <article class="card">
          <header class="card-head">
            <div>
              <h3 class="card-title">Latest Output</h3>
              <p class="card-subtitle">From /api/module-e/ws e_frame</p>
            </div>
          </header>
          <div class="card-body">
            <div id="module-e-output-metrics"></div>
          </div>
        </article>
      </section>

      <article class="card">
        <header class="card-head">
          <div>
            <h3 class="card-title">Gateway Status and Logs</h3>
          </div>
        </header>
        <div class="card-body module-e-bottom">
          <div id="module-e-state-metrics"></div>
          <ol id="module-e-log-list" class="module-e-log-list"></ol>
        </div>
      </article>
    </section>
  `;

  const badge = container.querySelector("#module-e-gateway-badge");
  const templateSelect = container.querySelector("#module-e-template");
  const sceneSelect = container.querySelector("#module-e-scene");
  const speedSelect = container.querySelector("#module-e-speed");
  const limitSelect = container.querySelector("#module-e-limit");
  const pedSelect = container.querySelector("#module-e-ped");
  const vehSelect = container.querySelector("#module-e-veh");
  const triggerBtn = container.querySelector("#module-e-trigger");
  const resetBtn = container.querySelector("#module-e-reset");
  const inputMetrics = container.querySelector("#module-e-input-metrics");
  const outputMetrics = container.querySelector("#module-e-output-metrics");
  const gatewayMetrics = container.querySelector("#module-e-state-metrics");
  const logList = container.querySelector("#module-e-log-list");

  function appendLog(text, tone = "") {
    components.appendLog(logList, { text, tone }, 120);
  }

  function clearDisplayQueue() {
    state.lastInput = null;
    renderInput(null);
    renderOutput({});
    components.clearNode(logList);
  }

  async function apiJson(url, options = {}) {
    const response = await fetch(url, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(options.headers ?? {}),
      },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload?.ok === false) {
      throw new Error(payload?.error || `HTTP ${response.status}`);
    }
    return payload;
  }

  function setGatewayBadge(text, tone = "") {
    badge.className = `badge${tone ? ` ${tone}` : ""}`;
    badge.textContent = text;
  }

  function renderTemplates(templates) {
    state.templates = templates.length ? templates : FALLBACK_TEMPLATES;
    templateSelect.innerHTML = "";
    for (const item of state.templates) {
      const option = document.createElement("option");
      option.value = item.template_id;
      option.textContent = `${toDisplayText(item.label)} (${item.template_id})`;
      templateSelect.appendChild(option);
    }
    if (!templateSelect.value && state.templates[0]) {
      templateSelect.value = state.templates[0].template_id;
    }
  }

  function getSelectedTemplate() {
    return findTemplate(state.templates, templateSelect.value);
  }

  function applyTemplateDefaults() {
    const selected = getSelectedTemplate();
    if (!selected) {
      return;
    }
    const defaults = selected.defaults ?? {};
    if (defaults.scene !== undefined) {
      sceneSelect.value = String(defaults.scene);
    }
    if (defaults.speed !== undefined) {
      speedSelect.value = String(defaults.speed);
    }
    if (defaults.limit_speed !== undefined) {
      limitSelect.value = String(defaults.limit_speed);
    }
    if (defaults.num_pedestrians !== undefined) {
      pedSelect.value = String(defaults.num_pedestrians);
    }
    if (defaults.num_vehicles !== undefined) {
      vehSelect.value = String(defaults.num_vehicles);
    }
  }

  function getCurrentParams() {
    return {
      scene: sceneSelect.value,
      speed: Number(speedSelect.value),
      limit_speed: Number(limitSelect.value),
      num_pedestrians: Number(pedSelect.value),
      num_vehicles: Number(vehSelect.value),
    };
  }

  function renderInput(lastInput) {
    if (!lastInput) {
      components.clearNode(inputMetrics);
      inputMetrics.appendChild(
        components.createMetricList([
          { label: "frame_id", value: "-" },
          { label: "template_id", value: "-" },
          { label: "scene", value: "-" },
          { label: "speed", value: "-" },
          { label: "limit_speed", value: "-" },
          { label: "num_pedestrians", value: "-" },
          { label: "num_vehicles", value: "-" },
        ]),
      );
      return;
    }
    const params = lastInput.params ?? {};
    components.clearNode(inputMetrics);
    inputMetrics.appendChild(
      components.createMetricList([
        { label: "frame_id", value: String(lastInput.frame_id ?? "-") },
        { label: "template_id", value: String(lastInput.template_id ?? "-") },
        { label: "topic", value: String(lastInput.topic ?? "-") },
        { label: "scene", value: String(params.scene ?? "-") },
        { label: "speed", value: `${Math.round(toNumber(params.speed) ?? 0)} km/h` },
        { label: "limit_speed", value: `${Math.round(toNumber(params.limit_speed) ?? 0)} km/h` },
        { label: "num_pedestrians", value: String(Math.trunc(toNumber(params.num_pedestrians) ?? 0)) },
        { label: "num_vehicles", value: String(Math.trunc(toNumber(params.num_vehicles) ?? 0)) },
      ]),
    );
  }

  function renderOutput(payload) {
    const moduleE = payload?.moduleE && typeof payload.moduleE === "object" ? payload.moduleE : {};
    const decision = moduleE?.decision && typeof moduleE.decision === "object" ? moduleE.decision : {};
    const status = typeof moduleE.status === "string" ? moduleE.status : "unknown";
    const decisionCode = typeof decision.decision_code === "string" ? decision.decision_code : "-";
    const voicePrompt = typeof decision.voice_prompt === "string" ? toDisplayText(decision.voice_prompt) : "-";
    const priority = decision.priority === null || decision.priority === undefined ? "-" : String(decision.priority);
    const speak = decision.speak === true ? "true" : decision.speak === false ? "false" : "-";
    let statusTone = "";
    if (status === "processed") {
      statusTone = "success";
    } else if (status === "process_error") {
      statusTone = "danger";
    } else {
      statusTone = "warn";
    }
    components.clearNode(outputMetrics);
    outputMetrics.appendChild(
      components.createMetricList([
        { label: "status", value: status, tone: statusTone },
        { label: "decision_code", value: decisionCode },
        { label: "speak", value: speak },
        { label: "priority", value: priority },
        { label: "scene", value: String(moduleE.scene ?? "-") },
        { label: "speed", value: moduleE.speed === undefined ? "-" : `${Math.round(toNumber(moduleE.speed) ?? 0)} km/h` },
        { label: "voice_prompt", value: voicePrompt },
      ]),
    );
  }

  function renderGatewayState(statePayload) {
    const simState = statePayload?.state && typeof statePayload.state === "object" ? statePayload.state : {};
    const demoConnected = simState.demo_connected === true;
    const publishedCount = Math.trunc(toNumber(simState.published_count) ?? 0);
    const receivedCount = Math.trunc(toNumber(simState.received_count) ?? 0);
    const lastFrameId = toNumber(simState.last_frame_id);
    const invalidOutputCount = Math.trunc(toNumber(simState.invalid_output_count) ?? 0);
    const demoState = simState.demo_state && typeof simState.demo_state === "object" ? simState.demo_state : {};
    const processedCount = Math.trunc(toNumber(demoState?.state?.processed_count) ?? 0);
    const processErrorCount = Math.trunc(toNumber(demoState?.state?.process_error_count) ?? 0);
    const ttsQueue = Math.trunc(toNumber(demoState?.state?.engine?.tts_queue_size) ?? 0);
    const ttsState = demoState?.state?.engine?.tts && typeof demoState.state.engine.tts === "object"
      ? demoState.state.engine.tts
      : {};
    const ttsWorkerAlive = ttsState.worker_alive === true;
    const ttsBackend = typeof ttsState.backend === "string" && ttsState.backend ? ttsState.backend : "-";
    const ttsVoice = typeof ttsState.voice_name === "string" && ttsState.voice_name
      ? ttsState.voice_name
      : typeof ttsState.voice_id === "string" && ttsState.voice_id
        ? ttsState.voice_id
        : "-";
    const ttsLastError = typeof ttsState.last_error === "string" && ttsState.last_error ? ttsState.last_error : "-";
    const ttsEnqueueCount = Math.trunc(toNumber(ttsState.enqueue_count) ?? 0);
    const ttsSpokenCount = Math.trunc(toNumber(ttsState.spoken_count) ?? 0);
    const ttsDropCount = Math.trunc(toNumber(ttsState.drop_count) ?? 0);

    components.clearNode(gatewayMetrics);
    gatewayMetrics.appendChild(
      components.createMetricList([
        { label: "demo_connected", value: demoConnected ? "true" : "false", tone: demoConnected ? "success" : "danger" },
        { label: "published_count", value: String(publishedCount) },
        { label: "received_count", value: String(receivedCount) },
        { label: "last_frame_id", value: lastFrameId === null ? "-" : String(Math.trunc(lastFrameId)) },
        { label: "invalid_output_count", value: String(invalidOutputCount) },
        { label: "processed_count", value: String(processedCount) },
        { label: "process_error_count", value: String(processErrorCount) },
        { label: "tts_queue_size", value: String(ttsQueue) },
        { label: "tts_worker_alive", value: ttsWorkerAlive ? "true" : "false", tone: ttsWorkerAlive ? "success" : "danger" },
        { label: "tts_backend", value: ttsBackend },
        { label: "tts_voice", value: ttsVoice },
        { label: "tts_enqueue_count", value: String(ttsEnqueueCount) },
        { label: "tts_spoken_count", value: String(ttsSpokenCount) },
        { label: "tts_drop_count", value: String(ttsDropCount) },
        { label: "tts_last_error", value: ttsLastError, tone: ttsLastError === "-" ? "" : "danger" },
      ]),
    );
  }

  async function refreshGatewayState() {
    try {
      const payload = await apiJson("/api/module-e/state", { method: "GET", headers: {} });
      renderGatewayState(payload);
      const templates = Array.isArray(payload?.state?.templates)
        ? payload.state.templates
            .map((item) => {
              if (!item || typeof item !== "object" || typeof item.template_id !== "string") {
                return null;
              }
              return {
                template_id: item.template_id,
                label: typeof item.label === "string" ? item.label : item.template_id,
                defaults: item.defaults && typeof item.defaults === "object" ? item.defaults : {},
              };
            })
            .filter(Boolean)
        : [];
      if (templates.length) {
        const previous = templateSelect.value;
        renderTemplates(templates);
        if (previous && templates.some((item) => item.template_id === previous)) {
          templateSelect.value = previous;
        } else {
          applyTemplateDefaults();
        }
      }
      setGatewayBadge("Connected", "success");
      state.lastGatewayError = "";
    } catch (error) {
      setGatewayBadge("Gateway Error", "danger");
      const message = `Failed to fetch gateway state: ${error?.message ?? "unknown error"}`;
      if (state.lastGatewayError !== message) {
        state.lastGatewayError = message;
        appendLog(message, "danger");
      }
    }
  }

  async function triggerOnce() {
    const templateId = templateSelect.value;
    const params = getCurrentParams();
    try {
      triggerBtn.disabled = true;
      const payload = await apiJson("/api/module-e/simulate", {
        method: "POST",
        body: JSON.stringify({
          template_id: templateId,
          params,
        }),
      });
      state.lastInput = payload;
      renderInput(state.lastInput);
      appendLog(
        `Triggered template=${payload.template_id} frame_id=${payload.frame_id} scene=${payload?.params?.scene ?? "-"}`,
        "success",
      );
    } catch (error) {
      appendLog(`Trigger failed: ${error?.message ?? "unknown error"}`, "danger");
    } finally {
      triggerBtn.disabled = false;
    }
  }

  async function resetEngineState() {
    try {
      resetBtn.disabled = true;
      await apiJson("/api/module-e/reset", {
        method: "POST",
        body: JSON.stringify({}),
      });
      clearDisplayQueue();
      await refreshGatewayState();
    } catch (error) {
      appendLog(`Reset failed: ${error?.message ?? "unknown error"}`, "danger");
    } finally {
      resetBtn.disabled = false;
    }
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
    setGatewayBadge(`WS reconnecting in ${Math.ceil(delay / 1000)}s`, "warn");
    state.reconnectTimer = window.setTimeout(() => {
      state.reconnectTimer = null;
      connectWebSocket();
    }, delay);
  }

  function connectWebSocket() {
    if (state.destroyed) {
      return;
    }
    setGatewayBadge("WS Connecting", "warn");
    const socket = new WebSocket(getWsUrl());
    state.socket = socket;

    socket.addEventListener("open", () => {
      if (state.destroyed) {
        return;
      }
      state.reconnectAttempt = 0;
      setGatewayBadge("Connected", "success");
      appendLog("moduleE simulation WebSocket connected");
    });

    socket.addEventListener("message", (event) => {
      if (state.destroyed) {
        return;
      }
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch (_err) {
        appendLog("Received non-JSON message; ignored", "danger");
        return;
      }
      if (payload?.event === "e_frame") {
        renderOutput(payload);
        const code = payload?.moduleE?.decision?.decision_code ?? "-";
        appendLog(`Received e_frame frame_id=${payload?.frame_id ?? "-"} decision=${code}`);
      }
    });

    socket.addEventListener("error", () => {
      if (state.destroyed) {
        return;
      }
      appendLog("WebSocket error", "danger");
    });

    socket.addEventListener("close", () => {
      if (state.destroyed) {
        return;
      }
      appendLog("WebSocket disconnected. Reconnecting", "warn");
      scheduleReconnect();
    });
  }

  setSelectOptions(sceneSelect, SCENE_OPTIONS);
  setSelectOptions(speedSelect, SPEED_OPTIONS, (v) => `${v} km/h`);
  setSelectOptions(limitSelect, LIMIT_OPTIONS, (v) => `${v} km/h`);
  setSelectOptions(pedSelect, PEDESTRIAN_OPTIONS);
  setSelectOptions(vehSelect, VEHICLE_OPTIONS);
  renderTemplates(FALLBACK_TEMPLATES);
  applyTemplateDefaults();
  renderInput(null);
  renderOutput({});
  renderGatewayState({});

  templateSelect.addEventListener("change", applyTemplateDefaults);
  triggerBtn.addEventListener("click", triggerOnce);
  resetBtn.addEventListener("click", resetEngineState);

  refreshGatewayState();
  connectWebSocket();
  state.statePollTimer = window.setInterval(refreshGatewayState, 5000);

  return () => {
    state.destroyed = true;
    if (state.statePollTimer !== null) {
      window.clearInterval(state.statePollTimer);
      state.statePollTimer = null;
    }
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
