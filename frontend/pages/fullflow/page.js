import { FULLFLOW_SCENARIOS } from "./data.js";

function renderModuleCardBody(body, components, metricRows, payload) {
  components.clearNode(body);
  body.appendChild(components.createMetricList(metricRows));
  body.appendChild(components.createJsonBlock(payload));
}

export function mount(container, { components }) {
  const state = {
    frameIndex: 0,
  };

  const listeners = [];

  container.innerHTML = `
    <section class="fullflow-page">
      <article class="card fullflow-top">
        <header class="card-head">
          <div>
            <h3 class="card-title">场景联动展示</h3>
          </div>
      </article>

      <section class="fullflow-main">
        <article class="card">
          <header class="card-head">
            <div>
              <h3 class="card-title">驾驶场景帧序列</h3>
            </div>
            <span id="frame-badge" class="badge mono"></span>
          </header>
          <div class="card-body">
            <div class="stage-frame">
              <img id="stage-image" alt="驾驶场景帧" loading="lazy" />
              <div class="stage-overlay">
                <span id="stage-time-badge" class="badge mono"></span>
                <span id="stage-scene-badge" class="badge"></span>
              </div>
            </div>
            <div class="progress-meta">
              <span id="progress-label"></span>
              <span id="progress-percent"></span>
            </div>
            <input id="fullflow-progress" class="range" type="range" min="0" value="0" step="1" />
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
                <h4>moduleCD 检测结果</h4>
                <span class="badge mono">CD</span>
              </div>
              <div id="card-module-cd" class="module-card-body"></div>
            </section>
            <section class="module-card">
              <div class="module-card-head">
                <h4>moduleE 融合提醒</h4>
                <span id="module-e-level" class="badge mono">E</span>
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
            <p class="card-subtitle">按当前帧推进追加，语音相关日志高亮</p>
          </div>
        </header>
        <div class="card-body">
          <ol id="fullflow-log-list" class="log-list"></ol>
        </div>
      </article>
    </section>
  `;

  const progress = container.querySelector("#fullflow-progress");

  const stageImage = container.querySelector("#stage-image");
  const frameBadge = container.querySelector("#frame-badge");
  const stageTimeBadge = container.querySelector("#stage-time-badge");
  const stageSceneBadge = container.querySelector("#stage-scene-badge");
  const progressLabel = container.querySelector("#progress-label");
  const progressPercent = container.querySelector("#progress-percent");

  const cardA = container.querySelector("#card-module-a");
  const cardB = container.querySelector("#card-module-b");
  const cardCD = container.querySelector("#card-module-cd");
  const cardE = container.querySelector("#card-module-e");
  const cardELevel = container.querySelector("#module-e-level");
  const logList = container.querySelector("#fullflow-log-list");

  function currentScenario() {
    return FULLFLOW_SCENARIOS[0];
  }

  function pushFrameLogs(timelineEntry) {
    const baseLogs = (timelineEntry?.log ?? []).map((text) => ({
      text,
      tone: text.includes("提醒") || text.includes("超速") ? "voice" : "",
    }));

    components.appendLog(logList, baseLogs, 80);
  }

  function renderCurrentFrame({ appendLogs = true } = {}) {
    const scenario = currentScenario();
    const frame = scenario.frames[state.frameIndex];
    const timelineEntry = scenario.timeline[state.frameIndex];

    const total = scenario.frames.length;
    progress.max = String(total - 1);
    progress.value = String(state.frameIndex);

    stageImage.src = frame.src;
    frameBadge.textContent = `frame_id ${timelineEntry.frameId}`;
    stageTimeBadge.textContent = frame.ts;
    stageSceneBadge.textContent = scenario.name;
    progressLabel.textContent = `帧进度 ${state.frameIndex + 1}/${total}`;
    progressPercent.textContent = `${Math.round(((state.frameIndex + 1) / total) * 100)}%`;

    const signName = timelineEntry.moduleCD.traffic_signs[0]?.class_name ?? "无";

    renderModuleCardBody(
      cardA,
      components,
      [
        { label: "frame_id", value: String(timelineEntry.moduleA.frame_id) },
        { label: "topic", value: timelineEntry.moduleA.topic },
        { label: "图像载荷", value: timelineEntry.moduleA.image_source },
      ],
      timelineEntry.moduleA,
    );

    renderModuleCardBody(
      cardB,
      components,
      [
        { label: "scene", value: timelineEntry.moduleB.scene },
        { label: "confidence", value: components.formatPercent(timelineEntry.moduleB.confidence) },
        { label: "speed", value: `${timelineEntry.moduleB.speed} km/h` },
      ],
      timelineEntry.moduleB,
    );

    renderModuleCardBody(
      cardCD,
      components,
      [
        { label: "交通标志", value: `${timelineEntry.moduleCD.num_traffic_signs}` },
        { label: "行人数量", value: `${timelineEntry.moduleCD.num_pedestrians}` },
        { label: "车辆数量", value: `${timelineEntry.moduleCD.num_vehicles}` },
        { label: "主要标志", value: signName },
      ],
      timelineEntry.moduleCD,
    );

    components.clearNode(cardE);
    cardE.appendChild(
      components.createMetricList([
        { label: "status", value: timelineEntry.moduleE.status },
        { label: "alert_level", value: timelineEntry.moduleE.alert_level, tone: timelineEntry.moduleE.alert_level === "P1" ? "danger" : timelineEntry.moduleE.alert_level === "P2" ? "warn" : "success" },
      ]),
    );
    cardE.appendChild(components.createJsonBlock(timelineEntry.moduleE));

    const voice = document.createElement("div");
    voice.className = "voice-banner";
    voice.textContent = timelineEntry.moduleE.voice_prompt;
    cardE.appendChild(voice);

    cardELevel.className = "badge mono";
    if (timelineEntry.moduleE.alert_level === "P1") {
      cardELevel.classList.add("danger");
    } else if (timelineEntry.moduleE.alert_level === "P2") {
      cardELevel.classList.add("warn");
    } else {
      cardELevel.classList.add("success");
    }
    cardELevel.textContent = timelineEntry.moduleE.alert_level;

    if (appendLogs) {
      pushFrameLogs(timelineEntry);
    }
  }

  function bind(target, eventName, handler) {
    target.addEventListener(eventName, handler);
    listeners.push(() => target.removeEventListener(eventName, handler));
  }

  bind(progress, "input", (event) => {
    const next = Number.parseInt(event.target.value, 10);
    if (Number.isNaN(next)) {
      return;
    }
    const maxIndex = currentScenario().frames.length - 1;
    state.frameIndex = components.clamp(next, 0, maxIndex);
    renderCurrentFrame({ appendLogs: false });
  });

  bind(progress, "change", () => {
    renderCurrentFrame({ appendLogs: true });
  });

  renderCurrentFrame({ appendLogs: true });

  return () => {
    for (const off of listeners) {
      off();
    }
  };
}
