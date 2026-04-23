const LEVEL_STYLE = {
  SAFE: { label: "SAFE", color: "#9ca6b2", tone: "" },
  WARNING: { label: "WARNING", color: "#f4d38b", tone: "warn" },
  DANGER: { label: "DANGER", color: "#ef9f9f", tone: "danger" },
};

function getLevelStyle(level) {
  return LEVEL_STYLE[level] ?? LEVEL_STYLE.SAFE;
}

function normalizeSidePayload(framePayload, side) {
  return framePayload?.moduleCD?.bsd?.[side] ?? {
    tracked_count: 0,
    alert_count: 0,
    zone: { level: "SAFE", polygon_px: [], visible: false },
    tracks: [],
    alerts: [],
  };
}

function shouldRenderZone(zone) {
  if (!zone) {
    return false;
  }
  if (typeof zone.visible === "boolean") {
    return zone.visible;
  }
  return zone.source === "external_mask";
}

function createEmptyState(canvas, title, detail) {
  const ctx = canvas.getContext("2d");
  const width = 960;
  const height = 540;
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#0d1117";
  ctx.fillRect(0, 0, width, height);
  ctx.strokeStyle = "#2a3340";
  ctx.lineWidth = 2;
  ctx.strokeRect(18, 18, width - 36, height - 36);

  ctx.fillStyle = "#f3f5f7";
  ctx.font = '600 26px "IBM Plex Sans", sans-serif';
  ctx.textAlign = "center";
  ctx.fillText(title, width / 2, height / 2 - 8);

  ctx.fillStyle = "#9ca6b2";
  ctx.font = '16px "IBM Plex Sans", sans-serif';
  ctx.fillText(detail, width / 2, height / 2 + 26);
}

function drawPolygon(ctx, polygon, fillColor, strokeColor) {
  if (!Array.isArray(polygon) || polygon.length < 3) {
    return;
  }
  ctx.save();
  ctx.beginPath();
  polygon.forEach((point, index) => {
    const x = Number(point?.[0] ?? 0);
    const y = Number(point?.[1] ?? 0);
    if (index === 0) {
      ctx.moveTo(x, y);
    } else {
      ctx.lineTo(x, y);
    }
  });
  ctx.closePath();
  ctx.fillStyle = fillColor;
  ctx.strokeStyle = strokeColor;
  ctx.lineWidth = 2;
  ctx.fill();
  ctx.stroke();
  ctx.restore();
}

function drawTrackPredictions(ctx, track, color) {
  const center = Array.isArray(track?.center_px) ? track.center_px : null;
  const predictions = Array.isArray(track?.predictions) ? track.predictions : [];
  if (!center || predictions.length === 0) {
    return;
  }

  ctx.save();
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = 2;

  for (const prediction of predictions) {
    const point = Array.isArray(prediction?.point_px) ? prediction.point_px : null;
    if (!point) {
      continue;
    }
    ctx.beginPath();
    ctx.moveTo(Number(center[0] ?? 0), Number(center[1] ?? 0));
    ctx.lineTo(Number(point[0] ?? 0), Number(point[1] ?? 0));
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(Number(point[0] ?? 0), Number(point[1] ?? 0), 4, 0, Math.PI * 2);
    ctx.fill();
  }

  ctx.beginPath();
  ctx.arc(Number(center[0] ?? 0), Number(center[1] ?? 0), 4, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function drawTrackBox(ctx, track, alertLevel) {
  const bbox = Array.isArray(track?.bbox) ? track.bbox : null;
  if (!bbox || bbox.length < 4) {
    return;
  }
  const style = getLevelStyle(alertLevel);
  const x1 = Number(bbox[0] ?? 0);
  const y1 = Number(bbox[1] ?? 0);
  const x2 = Number(bbox[2] ?? 0);
  const y2 = Number(bbox[3] ?? 0);
  const width = x2 - x1;
  const height = y2 - y1;

  ctx.save();
  ctx.strokeStyle = style.color;
  ctx.lineWidth = alertLevel === "DANGER" ? 4 : 3;
  ctx.strokeRect(x1, y1, width, height);

  const confidence = Number(track?.confidence ?? 0);
  const label = `#${track?.track_id ?? "-"} ${track?.class_name ?? "obj"} ${(confidence * 100).toFixed(0)}%`;
  ctx.font = '600 16px "IBM Plex Sans", sans-serif';
  const labelWidth = ctx.measureText(label).width + 18;
  const labelHeight = 26;
  const labelY = Math.max(6, y1 - labelHeight - 4);
  ctx.fillStyle = "rgba(7, 9, 12, 0.82)";
  ctx.fillRect(x1, labelY, labelWidth, labelHeight);
  ctx.strokeRect(x1, labelY, labelWidth, labelHeight);
  ctx.fillStyle = style.color;
  ctx.fillText(label, x1 + 9, labelY + 18);
  ctx.restore();

  drawTrackPredictions(ctx, track, style.color);
}

function drawSideFrame(canvas, framePayload, side, sideLabel, imageObject) {
  const sidePayload = normalizeSidePayload(framePayload, side);
  const zoneLevel = sidePayload.zone?.level ?? "SAFE";
  const zoneStyle = getLevelStyle(zoneLevel);

  if (!imageObject) {
    createEmptyState(canvas, `${sideLabel} 画面待接入`, "等待桥接层收到图像与 tracker 结果");
    return;
  }

  const width = Number(imageObject.naturalWidth || imageObject.width || 960);
  const height = Number(imageObject.naturalHeight || imageObject.height || 540);
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }

  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, width, height);
  ctx.drawImage(imageObject, 0, 0, width, height);

  drawPolygon(
    ctx,
    shouldRenderZone(sidePayload.zone) ? sidePayload.zone?.polygon_px : [],
    `${zoneStyle.color}26`,
    zoneStyle.color,
  );

  const alertByTrack = new Map();
  for (const alert of sidePayload.alerts ?? []) {
    const trackId = alert?.track_id;
    if (trackId === undefined || trackId === null) {
      continue;
    }
    const current = alertByTrack.get(trackId);
    const nextLevel = alert?.level ?? "SAFE";
    if (current === "DANGER" || current === nextLevel) {
      continue;
    }
    if (nextLevel === "DANGER" || current !== "WARNING") {
      alertByTrack.set(trackId, nextLevel);
    }
  }

  for (const track of sidePayload.tracks ?? []) {
    drawTrackBox(ctx, track, alertByTrack.get(track?.track_id) ?? zoneLevel);
  }
}

function flattenAlerts(framePayload) {
  const leftAlerts = normalizeSidePayload(framePayload, "left").alerts ?? [];
  const rightAlerts = normalizeSidePayload(framePayload, "right").alerts ?? [];
  return [
    ...leftAlerts.map((alert) => ({ ...alert, side: "left" })),
    ...rightAlerts.map((alert) => ({ ...alert, side: "right" })),
  ].sort((a, b) => {
    const priority = { DANGER: 0, WARNING: 1, SAFE: 2 };
    return (priority[a.level] ?? 9) - (priority[b.level] ?? 9);
  });
}

function createDisplayJson(framePayload) {
  if (!framePayload) {
    return {};
  }
  return {
    frame_id: framePayload.frame_id,
    t_sync: framePayload.t_sync,
    cameras: {
      left: {
        ...framePayload.cameras?.left,
        src: "[base64 omitted]",
      },
      right: {
        ...framePayload.cameras?.right,
        src: "[base64 omitted]",
      },
    },
    moduleCD: framePayload.moduleCD,
  };
}

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("image load failed"));
    image.src = src;
  });
}

async function resolveImages(framePayload, state) {
  const sides = ["left", "right"];
  const entries = await Promise.all(
    sides.map(async (side) => {
      const src = framePayload?.cameras?.[side]?.src;
      if (!src) {
        return [side, null];
      }
      const cached = state.imageCache[side];
      if (cached?.src === src && cached.image) {
        return [side, cached.image];
      }
      const image = await loadImage(src);
      state.imageCache[side] = { src, image };
      return [side, image];
    }),
  );
  return Object.fromEntries(entries);
}

export function mount(container, { components }) {
  const state = {
    latestFrame: null,
    latestImages: { left: null, right: null },
    health: null,
    streamState: "connecting",
    lastMessageAt: 0,
    renderToken: 0,
    imageCache: { left: null, right: null },
    socket: null,
    healthTimerId: null,
    reconnectTimerId: null,
    destroyed: false,
  };

  container.innerHTML = `
    <section class="module-c-page">
      <article class="card">
        <header class="card-head">
          <div>
            <h3 class="card-title">moduleCD tracker 实时叠加</h3>
            <p class="card-subtitle">WebSocket 实时流 + 左右双窗盲区追踪叠加</p>
          </div>
          <div class="module-c-legend">
            <span class="legend-chip"><i class="legend-dot safe"></i>SAFE</span>
            <span class="legend-chip"><i class="legend-dot warn"></i>WARNING</span>
            <span class="legend-chip"><i class="legend-dot danger"></i>DANGER</span>
          </div>
        </header>
        <div class="card-body module-c-status-grid">
          <div class="status-card">
            <span class="status-label">WebSocket 状态</span>
            <span id="module-c-stream-status" class="status-pill">连接中</span>
          </div>
          <div class="status-card">
            <span class="status-label">最近 frame_id</span>
            <span id="module-c-frame-id" class="status-value mono">-</span>
          </div>
          <div class="status-card">
            <span class="status-label">桥接客户端</span>
            <span id="module-c-client-count" class="status-value mono">0</span>
          </div>
          <div class="status-card">
            <span class="status-label">待配对 input/output</span>
            <span id="module-c-pending" class="status-value mono">0 / 0</span>
          </div>
          <div class="status-card">
            <span class="status-label">丢弃 input/output</span>
            <span id="module-c-dropped" class="status-value mono">0 / 0</span>
          </div>
          <div class="status-card">
            <span class="status-label">输出帧链路</span>
            <span id="module-c-frame-chain" class="status-value mono">- / - / -</span>
          </div>
        </div>
      </article>

      <section class="module-c-viewers">
        <article class="card viewer-card">
          <header class="card-head">
            <div>
              <h3 class="card-title">左后视画面</h3>
              <p class="card-subtitle">zone / tracks / alerts / predictions</p>
            </div>
            <div class="viewer-side-meta">
              <span id="module-c-left-level" class="badge mono">SAFE</span>
              <span id="module-c-left-counts" class="badge mono">0T / 0A</span>
            </div>
          </header>
          <div class="card-body viewer-stage">
            <canvas id="module-c-left-canvas" class="viewer-canvas" aria-label="左后视追踪画面"></canvas>
          </div>
        </article>

        <article class="card viewer-card">
          <header class="card-head">
            <div>
              <h3 class="card-title">右后视画面</h3>
              <p class="card-subtitle">zone / tracks / alerts / predictions</p>
            </div>
            <div class="viewer-side-meta">
              <span id="module-c-right-level" class="badge mono">SAFE</span>
              <span id="module-c-right-counts" class="badge mono">0T / 0A</span>
            </div>
          </header>
          <div class="card-body viewer-stage">
            <canvas id="module-c-right-canvas" class="viewer-canvas" aria-label="右后视追踪画面"></canvas>
          </div>
        </article>
      </section>

      <section class="module-c-bottom">
        <article class="card">
          <header class="card-head">
            <div>
              <h3 class="card-title">当前帧摘要</h3>
              <p class="card-subtitle">moduleCD 原始统计 + BSD 侧窗状态</p>
            </div>
          </header>
          <div class="card-body">
            <div id="module-c-summary"></div>
            <div class="module-c-alerts">
              <div class="module-c-alerts-head">
                <h4>告警列表</h4>
                <span id="module-c-alert-total" class="badge mono">0</span>
              </div>
              <ol id="module-c-alert-list" class="module-c-alert-list"></ol>
            </div>
          </div>
        </article>

        <article class="card">
          <header class="card-head">
            <div>
              <h3 class="card-title">原始 JSON</h3>
              <p class="card-subtitle">保留 moduleCD 完整输出，摄像头 base64 已省略</p>
            </div>
          </header>
          <div class="card-body">
            <pre id="module-c-json" class="json-block module-c-json"></pre>
          </div>
        </article>
      </section>
    </section>
  `;

  const refs = {
    streamStatus: container.querySelector("#module-c-stream-status"),
    frameId: container.querySelector("#module-c-frame-id"),
    clientCount: container.querySelector("#module-c-client-count"),
    pending: container.querySelector("#module-c-pending"),
    dropped: container.querySelector("#module-c-dropped"),
    frameChain: container.querySelector("#module-c-frame-chain"),
    leftCanvas: container.querySelector("#module-c-left-canvas"),
    rightCanvas: container.querySelector("#module-c-right-canvas"),
    leftLevel: container.querySelector("#module-c-left-level"),
    rightLevel: container.querySelector("#module-c-right-level"),
    leftCounts: container.querySelector("#module-c-left-counts"),
    rightCounts: container.querySelector("#module-c-right-counts"),
    summary: container.querySelector("#module-c-summary"),
    json: container.querySelector("#module-c-json"),
    alertList: container.querySelector("#module-c-alert-list"),
    alertTotal: container.querySelector("#module-c-alert-total"),
  };

  function applyBadgeTone(node, tone) {
    node.className = "badge mono";
    if (tone) {
      node.classList.add(tone);
    }
  }

  function renderTextState() {
    const health = state.health ?? {};
    const framePayload = state.latestFrame;
    const leftPayload = normalizeSidePayload(framePayload, "left");
    const rightPayload = normalizeSidePayload(framePayload, "right");
    const streamTone =
      state.streamState === "live" ? "success" : state.streamState === "reconnecting" ? "warn" : "danger";
    const streamLabel =
      state.streamState === "live"
        ? "实时接收中"
        : state.streamState === "reconnecting"
          ? "重连中"
          : state.streamState === "connecting"
            ? "连接中"
            : "等待数据";

    refs.streamStatus.textContent = streamLabel;
    refs.streamStatus.className = `status-pill ${streamTone}`;
    refs.frameId.textContent = framePayload?.frame_id ?? "-";
    refs.clientCount.textContent = String(health.client_count ?? 0);
    refs.pending.textContent = `${health.pending_input ?? 0} / ${health.pending_output ?? 0}`;
    refs.dropped.textContent = `${health.dropped_input ?? 0} / ${health.dropped_output ?? 0}`;
    refs.frameChain.textContent = `${health.last_input_frame_id ?? "-"} / ${health.last_output_frame_id ?? "-"} / ${health.last_merged_frame_id ?? "-"}`;

    const leftLevel = getLevelStyle(leftPayload.zone?.level ?? "SAFE");
    const rightLevel = getLevelStyle(rightPayload.zone?.level ?? "SAFE");
    refs.leftLevel.textContent = leftLevel.label;
    refs.rightLevel.textContent = rightLevel.label;
    applyBadgeTone(refs.leftLevel, leftLevel.tone);
    applyBadgeTone(refs.rightLevel, rightLevel.tone);
    refs.leftCounts.textContent = `${leftPayload.tracked_count ?? 0}T / ${leftPayload.alert_count ?? 0}A`;
    refs.rightCounts.textContent = `${rightPayload.tracked_count ?? 0}T / ${rightPayload.alert_count ?? 0}A`;

    const system = framePayload?.moduleCD?.bsd?.system ?? {};
    components.clearNode(refs.summary);
    refs.summary.appendChild(
      components.createMetricList([
        { label: "frame_id", value: String(framePayload?.frame_id ?? "-") },
        { label: "fps", value: typeof system.fps === "number" ? system.fps.toFixed(2) : "-" },
        { label: "detector", value: system.detector_backend ?? "-" },
        { label: "device", value: system.detector_device ?? "-" },
        { label: "tracked vehicles", value: String(framePayload?.moduleCD?.num_vehicles ?? 0) },
        { label: "tracked pedestrians", value: String(framePayload?.moduleCD?.num_pedestrians ?? 0) },
        {
          label: "max_alert_level",
          value: system.max_alert_level ?? "SAFE",
          tone: getLevelStyle(system.max_alert_level).tone,
        },
      ]),
    );

    const alerts = flattenAlerts(framePayload);
    refs.alertTotal.textContent = String(alerts.length);
    refs.alertList.innerHTML = "";
    if (alerts.length === 0) {
      const item = document.createElement("li");
      item.className = "module-c-alert-item is-empty";
      item.textContent = "当前没有 WARNING / DANGER 告警。";
      refs.alertList.appendChild(item);
    } else {
      for (const alert of alerts) {
        const style = getLevelStyle(alert.level);
        const item = document.createElement("li");
        item.className = `module-c-alert-item ${style.tone || "safe"}`;
        const tte =
          typeof alert.time_to_entry_s === "number" ? `${alert.time_to_entry_s.toFixed(2)}s` : "n/a";
        item.textContent = `${alert.side.toUpperCase()} · #${alert.track_id} · ${style.label} · r=${Number(alert.r_score ?? 0).toFixed(2)} · tte=${tte}`;
        refs.alertList.appendChild(item);
      }
    }

    refs.json.textContent = components.safeStringify(createDisplayJson(framePayload));
  }

  function renderCanvases() {
    drawSideFrame(refs.leftCanvas, state.latestFrame, "left", "左后视", state.latestImages.left);
    drawSideFrame(refs.rightCanvas, state.latestFrame, "right", "右后视", state.latestImages.right);
  }

  async function applyIncomingFrame(framePayload) {
    state.latestFrame = framePayload;
    state.lastMessageAt = Date.now();
    state.streamState = "live";
    renderTextState();

    const token = ++state.renderToken;
    try {
      const images = await resolveImages(framePayload, state);
      if (token !== state.renderToken) {
        return;
      }
      state.latestImages = images;
    } catch (_error) {
      if (token !== state.renderToken) {
        return;
      }
      state.latestImages = { left: null, right: null };
    }
    renderCanvases();
  }

  async function refreshHealth() {
    try {
      const response = await fetch("/api/module-c/health", { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`health ${response.status}`);
      }
      state.health = await response.json();
      if (!state.latestFrame && state.streamState === "connecting") {
        state.streamState = "idle";
      }
      if (state.lastMessageAt > 0 && Date.now() - state.lastMessageAt > 5000) {
        state.streamState = "reconnecting";
      }
    } catch (_error) {
      state.health = null;
      if (!state.latestFrame) {
        state.streamState = "idle";
      } else {
        state.streamState = "reconnecting";
      }
    }
    renderTextState();
  }

  function buildWebSocketUrl(path) {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${window.location.host}${path}`;
  }

  function clearReconnectTimer() {
    if (state.reconnectTimerId !== null) {
      window.clearTimeout(state.reconnectTimerId);
      state.reconnectTimerId = null;
    }
  }

  function scheduleReconnect(delayMs = 1000) {
    if (state.destroyed || state.reconnectTimerId !== null) {
      return;
    }
    state.streamState = "reconnecting";
    renderTextState();
    state.reconnectTimerId = window.setTimeout(() => {
      state.reconnectTimerId = null;
      connectStream();
    }, delayMs);
  }

  function connectStream() {
    clearReconnectTimer();
    if (state.socket) {
      state.socket.onopen = null;
      state.socket.onmessage = null;
      state.socket.onerror = null;
      state.socket.onclose = null;
      state.socket.close();
      state.socket = null;
    }
    state.streamState = "connecting";
    renderTextState();

    const socket = new WebSocket(buildWebSocketUrl("/api/module-c/ws"));
    state.socket = socket;

    socket.onopen = () => {
      if (state.socket !== socket) {
        return;
      }
      if (!state.latestFrame) {
        state.streamState = "connecting";
        renderTextState();
      }
    };

    socket.onmessage = (event) => {
      if (state.socket !== socket) {
        return;
      }
      try {
        const payload = JSON.parse(event.data);
        applyIncomingFrame(payload);
      } catch (_error) {
        scheduleReconnect();
      }
    };

    socket.onerror = () => {
      if (state.socket !== socket) {
        return;
      }
      state.streamState = "reconnecting";
      renderTextState();
    };

    socket.onclose = () => {
      if (state.socket !== socket) {
        return;
      }
      state.socket = null;
      scheduleReconnect();
    };
  }

  createEmptyState(refs.leftCanvas, "左后视画面待接入", "等待桥接层收到图像与 tracker 结果");
  createEmptyState(refs.rightCanvas, "右后视画面待接入", "等待桥接层收到图像与 tracker 结果");
  renderTextState();
  connectStream();
  refreshHealth();
  state.healthTimerId = window.setInterval(refreshHealth, 2000);

  return () => {
    state.destroyed = true;
    clearReconnectTimer();
    if (state.socket) {
      state.socket.onopen = null;
      state.socket.onmessage = null;
      state.socket.onerror = null;
      state.socket.onclose = null;
      state.socket.close();
    }
    if (state.healthTimerId !== null) {
      window.clearInterval(state.healthTimerId);
    }
  };
}
