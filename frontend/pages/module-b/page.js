import { MODULE_B_SCENARIOS } from "./data.js";

export function mount(container, { components }) {
  const state = {
    scenarioIndex: 0,
    frameIndex: 0,
    playing: false,
    timerId: null,
  };

  const listeners = [];

  container.innerHTML = `
    <section class="module-b-page">
      <article class="card">
        <header class="card-head">
          <div>
            <h3 class="card-title">模块B独立展示</h3>
            <p class="card-subtitle">左侧原始场景序列，右侧热力图预留窗口</p>
          </div>
          <span id="module-b-play-state" class="badge">未播放</span>
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
              <p id="module-b-scene-desc" class="card-subtitle"></p>
            </div>
            <span id="module-b-frame-badge" class="badge mono"></span>
          </header>
          <div class="card-body">
            <div class="viewer-frame">
              <img id="module-b-image" alt="模块B场景帧" loading="lazy" />
            </div>
            <div class="module-b-meta">
              <span id="module-b-progress-label"></span>
              <span id="module-b-progress-percent"></span>
            </div>
            <input id="module-b-progress" class="range" type="range" min="0" value="0" step="1" />
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
          <pre id="module-b-json" class="json-block"></pre>
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
  const jsonRoot = container.querySelector("#module-b-json");
  const logList = container.querySelector("#module-b-log-list");

  function currentScenario() {
    return MODULE_B_SCENARIOS[state.scenarioIndex];
  }

  function stopPlayback() {
    if (state.timerId !== null) {
      window.clearInterval(state.timerId);
      state.timerId = null;
    }
    state.playing = false;
    playState.textContent = "已暂停";
  }

  function renderFrame() {
    const scenario = currentScenario();
    const frame = scenario.frames[state.frameIndex];
    const output = scenario.outputs[state.frameIndex];

    image.src = frame.src;
    sceneDesc.textContent = `${scenario.description} · ${scenario.frameIntervalMs}ms/帧`;
    frameBadge.textContent = `frame_id ${output.frameId}`;

    progress.max = String(scenario.frames.length - 1);
    progress.value = String(state.frameIndex);

    progressLabel.textContent = `帧进度 ${state.frameIndex + 1}/${scenario.frames.length}`;
    progressPercent.textContent = `${Math.round(((state.frameIndex + 1) / scenario.frames.length) * 100)}%`;

    components.clearNode(metricsRoot);
    metricsRoot.appendChild(
      components.createMetricList([
        { label: "scene", value: output.scene },
        { label: "confidence", value: components.formatPercent(output.confidence) },
        { label: "conference", value: components.formatPercent(output.conference) },
        { label: "speed", value: `${output.speed} km/h` },
      ]),
    );

    jsonRoot.textContent = components.safeStringify(output);

    logList.innerHTML = "";
    for (const message of output.log) {
      const item = document.createElement("li");
      item.className = "module-b-log-item";
      item.textContent = message;
      logList.appendChild(item);
    }
  }

  function startPlayback() {
    const scenario = currentScenario();
    if (state.playing) {
      return;
    }

    if (state.frameIndex >= scenario.frames.length - 1) {
      state.frameIndex = 0;
    }

    state.playing = true;
    playState.textContent = "播放中";

    state.timerId = window.setInterval(() => {
      const active = currentScenario();
      if (state.frameIndex >= active.frames.length - 1) {
        stopPlayback();
        playState.textContent = "已完成";
        return;
      }
      state.frameIndex += 1;
      renderFrame();
    }, scenario.frameIntervalMs);
  }

  function switchScenario(index) {
    stopPlayback();
    state.scenarioIndex = index;
    state.frameIndex = 0;
    playState.textContent = "未播放";
    renderFrame();
  }

  function bind(target, eventName, handler) {
    target.addEventListener(eventName, handler);
    listeners.push(() => target.removeEventListener(eventName, handler));
  }

  for (const [index, scenario] of MODULE_B_SCENARIOS.entries()) {
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = scenario.name;
    sceneSelect.appendChild(option);
  }

  bind(sceneSelect, "change", (event) => {
    const next = Number.parseInt(event.target.value, 10);
    if (!Number.isNaN(next)) {
      switchScenario(next);
    }
  });

  bind(playBtn, "click", startPlayback);
  bind(pauseBtn, "click", stopPlayback);
  bind(resetBtn, "click", () => switchScenario(state.scenarioIndex));

  bind(progress, "input", (event) => {
    const next = Number.parseInt(event.target.value, 10);
    if (Number.isNaN(next)) {
      return;
    }
    const maxIndex = currentScenario().frames.length - 1;
    state.frameIndex = components.clamp(next, 0, maxIndex);
    renderFrame();
  });

  sceneSelect.value = String(state.scenarioIndex);
  renderFrame();

  return () => {
    stopPlayback();
    for (const off of listeners) {
      off();
    }
  };
}
