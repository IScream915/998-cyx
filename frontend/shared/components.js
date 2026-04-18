export function clearNode(node) {
  while (node.firstChild) {
    node.removeChild(node.firstChild);
  }
}

export function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export function safeStringify(payload) {
  try {
    return JSON.stringify(payload, null, 2);
  } catch (_err) {
    return "{\n  \"error\": \"payload 无法序列化\"\n}";
  }
}

export function createMetricList(items) {
  const list = document.createElement("div");
  list.className = "metric-list";

  for (const item of items) {
    const row = document.createElement("div");
    row.className = "metric-row";

    const label = document.createElement("span");
    label.className = "metric-label";
    label.textContent = item.label;

    const value = document.createElement("span");
    value.className = `metric-value${item.tone ? ` ${item.tone}` : ""}`;
    value.textContent = item.value;

    row.appendChild(label);
    row.appendChild(value);
    list.appendChild(row);
  }

  return list;
}

export function createJsonBlock(payload) {
  const pre = document.createElement("pre");
  pre.className = "json-block";
  pre.textContent = safeStringify(payload);
  return pre;
}

export function createBadge(text, tone = "") {
  const badge = document.createElement("span");
  badge.className = `badge${tone ? ` ${tone}` : ""}`;
  badge.textContent = text;
  return badge;
}

export function formatPercent(value) {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-";
  }
  return `${(value * 100).toFixed(1)}%`;
}

export function formatClock(date = new Date()) {
  return date.toLocaleTimeString("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function appendLog(logRoot, entries, maxCount = 60) {
  const normalized = Array.isArray(entries) ? entries : [entries];
  const fragment = document.createDocumentFragment();

  for (const entry of normalized) {
    if (!entry?.text) {
      continue;
    }
    const item = document.createElement("li");
    item.className = `log-item${entry.tone ? ` ${entry.tone}` : ""}`;

    const ts = document.createElement("span");
    ts.className = "log-ts mono";
    ts.textContent = entry.time ?? formatClock();

    const text = document.createElement("span");
    text.className = "log-text";
    text.textContent = entry.text;

    item.appendChild(ts);
    item.appendChild(text);
    fragment.appendChild(item);
  }

  logRoot.appendChild(fragment);

  while (logRoot.childElementCount > maxCount) {
    logRoot.removeChild(logRoot.firstElementChild);
  }

  logRoot.scrollTop = logRoot.scrollHeight;
}
