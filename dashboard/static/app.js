async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

function fmtNum(v, suffix = "") {
  if (v == null || Number.isNaN(v)) return "—";
  return `${Math.round(v).toLocaleString()}${suffix}`;
}

function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function renderMarkdown(text) {
  if (!text) return "";
  if (typeof marked !== "undefined") {
    marked.setOptions({ breaks: true, gfm: true });
    const html = marked.parse(String(text));
    if (typeof DOMPurify !== "undefined") {
      return DOMPurify.sanitize(html);
    }
    return html;
  }
  return escapeHtml(text).replace(/\n/g, "<br>");
}

function appendMessage(logEl, role, content, { label } = {}) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  const roleLabel = label || (role === "user" ? "You" : role === "assistant" ? "Coach" : role);
  const bodyClass = role === "assistant" ? "msg-body prose" : "msg-body";
  const body = role === "assistant" ? renderMarkdown(content) : escapeHtml(content).replace(/\n/g, "<br>");
  div.innerHTML = `<div class="role">${escapeHtml(roleLabel)}</div><div class="${bodyClass}">${body}</div>`;
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
  return div;
}

function renderMessages(logEl, messages, { userLabel = "You", assistantLabel = "Coach" } = {}) {
  logEl.innerHTML = "";
  for (const msg of messages) {
    const label = msg.role === "user" ? userLabel : assistantLabel;
    appendMessage(logEl, msg.role, msg.content, { label });
  }
}

function renderMetrics(summary) {
  const avg = summary.averages || {};
  document.getElementById("m-steps").textContent = fmtNum(avg.steps);
  document.getElementById("m-sleep").textContent = avg.sleep_hours != null ? `${avg.sleep_hours}h` : "—";
  document.getElementById("m-rhr").textContent = avg.resting_hr != null ? `${Math.round(avg.resting_hr)} bpm` : "—";
  document.getElementById("m-stress").textContent = avg.avg_stress != null ? avg.avg_stress : "—";
  document.getElementById("m-acts").textContent = (summary.recent_activities || []).length;
  document.getElementById("m-days").textContent = summary.days_with_data ?? "—";
}

function renderActivities(activities) {
  const tbody = document.getElementById("activities-body");
  tbody.innerHTML = "";
  for (const a of activities || []) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${(a.start_time || "").slice(0, 10) || "—"}</td>
      <td>${escapeHtml(a.garmin_label || a.name || "—")}</td>
      <td>${escapeHtml(a.type || "—")}</td>
      <td>${a.distance_km ?? "—"}</td>
      <td>${a.duration_min != null ? `${a.duration_min} min` : "—"}</td>
      <td>${a.avg_hr ?? "—"}</td>`;
    tbody.appendChild(tr);
  }
}

function renderGoals(goals) {
  const ul = document.getElementById("goals-list");
  ul.innerHTML = "";
  for (const g of goals) {
    const li = document.createElement("li");
    li.innerHTML = `
      <div class="goal-text">${escapeHtml(g.text)}</div>
      <div class="goal-meta">${g.category}${g.target_date ? " · " + g.target_date : ""}</div>
      <span class="goal-status ${g.status}">${g.status}</span>
      <button class="btn secondary small" data-complete="${g.id}">Done</button>
      <button class="btn secondary small" data-delete="${g.id}">×</button>`;
    ul.appendChild(li);
  }
  ul.querySelectorAll("[data-complete]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await api(`/api/goals/${btn.dataset.complete}`, {
        method: "PATCH",
        body: JSON.stringify({ status: "completed" }),
      });
      loadGoals();
    });
  });
  ul.querySelectorAll("[data-delete]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await api(`/api/goals/${btn.dataset.delete}`, { method: "DELETE" });
      loadGoals();
    });
  });
}

function renderChat(history) {
  renderMessages(document.getElementById("chat-log"), history);
}

let insightsLoaded = false;
let insightsGenerating = false;

function openInsightsOverlay() {
  const overlay = document.getElementById("insights-overlay");
  overlay.classList.remove("hidden");
  overlay.setAttribute("aria-hidden", "false");
  document.body.classList.add("overlay-open");
  document.getElementById("insights-input").focus();
  if (!insightsLoaded) {
    loadInsightsSession();
  }
}

function closeInsightsOverlay() {
  const overlay = document.getElementById("insights-overlay");
  overlay.classList.add("hidden");
  overlay.setAttribute("aria-hidden", "true");
  document.body.classList.remove("overlay-open");
}

async function loadInsightsSession() {
  const log = document.getElementById("insights-log");
  log.innerHTML = "";

  const [chatData, insightsData] = await Promise.all([api("/api/chat"), api("/api/insights")]);
  const messages = [];

  if (insightsData.insights?.text) {
    messages.push({
      role: "assistant",
      content: insightsData.insights.text,
      label: "Training Analysis",
    });
  }

  for (const msg of chatData.history || []) {
    messages.push({
      role: msg.role,
      content: msg.content,
      label: msg.role === "user" ? "You" : "Coach",
    });
  }

  if (!messages.length) {
    messages.push({
      role: "assistant",
      content:
        "I'll analyze your last 30 days of Garmin data and goals. Click **Refresh Analysis** or ask me anything to get started.",
      label: "Coach",
    });
  }

  for (const msg of messages) {
    appendMessage(log, msg.role, msg.content, { label: msg.label });
  }

  insightsLoaded = true;
}

async function generateInsights() {
  if (insightsGenerating) return;
  insightsGenerating = true;
  const btn = document.getElementById("btn-insights-refresh");
  const log = document.getElementById("insights-log");
  btn.disabled = true;
  btn.textContent = "Analyzing…";

  const loading = appendMessage(log, "assistant", "_Analyzing your Garmin data and goals…_", {
    label: "Training Analysis",
  });

  try {
    await api("/api/insights", { method: "POST" });
    loading.remove();
    insightsLoaded = false;
    await loadInsightsSession();
    await loadChat();
  } catch (err) {
    loading.remove();
    appendMessage(log, "assistant", `**Error:** ${err.message}`, { label: "Training Analysis" });
  } finally {
    btn.disabled = false;
    btn.textContent = "Refresh Analysis";
    insightsGenerating = false;
  }
}

async function sendInsightsMessage(message) {
  const log = document.getElementById("insights-log");
  const input = document.getElementById("insights-input");
  appendMessage(log, "user", message);
  input.disabled = true;

  const loading = appendMessage(log, "assistant", "_Thinking…_", { label: "Coach" });

  try {
    const data = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message }),
    });
    loading.remove();
    appendMessage(log, "assistant", data.reply, { label: "Coach" });
    await loadChat();
  } catch (err) {
    loading.remove();
    appendMessage(log, "assistant", `**Error:** ${err.message}`, { label: "Coach" });
  } finally {
    input.disabled = false;
    input.focus();
  }
}

async function loadStatus() {
  const status = await api("/api/status");
  const line = document.getElementById("status-line");
  if (!status.cache_exists) {
    line.textContent = "No Garmin export yet — run Sync Garmin Data or garmin_export.py --all";
    return;
  }
  const range = status.date_range;
  const rangeText = range ? `${range.start} → ${range.end}` : "no daily data";
  line.textContent = `${status.daily_days} days · ${status.activities} activities · ${rangeText}`;
}

async function loadSummary() {
  const summary = await api("/api/summary?days=30");
  renderMetrics(summary);
  renderActivities(summary.recent_activities);
}

async function loadGoals() {
  const data = await api("/api/goals");
  renderGoals(data.goals);
}

async function loadChat() {
  const data = await api("/api/chat");
  renderChat(data.history);
}

document.getElementById("goal-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = document.getElementById("goal-text").value.trim();
  const target_date = document.getElementById("goal-date").value || null;
  const category = document.getElementById("goal-category").value;
  await api("/api/goals", {
    method: "POST",
    body: JSON.stringify({ text, target_date, category }),
  });
  e.target.reset();
  loadGoals();
});

document.getElementById("chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("chat-input");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  input.disabled = true;
  const log = document.getElementById("chat-log");
  appendMessage(log, "user", message);
  const loading = appendMessage(log, "assistant", "_Thinking…_", { label: "Coach" });
  try {
    const data = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message }),
    });
    loading.remove();
    appendMessage(log, "assistant", data.reply, { label: "Coach" });
  } catch (err) {
    loading.remove();
    appendMessage(log, "assistant", `**Error:** ${err.message}`, { label: "Coach" });
  } finally {
    input.disabled = false;
    input.focus();
  }
});

document.getElementById("btn-clear-chat").addEventListener("click", async () => {
  if (!confirm("Clear chat history?")) return;
  await api("/api/chat", { method: "DELETE" });
  insightsLoaded = false;
  loadChat();
});

document.getElementById("btn-open-insights").addEventListener("click", openInsightsOverlay);
document.getElementById("btn-insights-close").addEventListener("click", closeInsightsOverlay);
document.getElementById("btn-insights-refresh").addEventListener("click", generateInsights);

document.getElementById("insights-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("insights-input");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  await sendInsightsMessage(message);
});

document.getElementById("insights-overlay").addEventListener("click", (e) => {
  if (e.target.id === "insights-overlay") closeInsightsOverlay();
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !document.getElementById("insights-overlay").classList.contains("hidden")) {
    closeInsightsOverlay();
  }
});

document.getElementById("btn-export").addEventListener("click", async () => {
  const btn = document.getElementById("btn-export");
  btn.disabled = true;
  btn.textContent = "Syncing…";
  try {
    await api("/api/export/update", { method: "POST" });
    alert("Export started in background. Refresh in a few minutes.");
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Sync Garmin Data";
  }
});

async function init() {
  try {
    await loadStatus();
    await loadSummary();
    await loadGoals();
    await loadChat();
  } catch (err) {
    document.getElementById("status-line").textContent = `Error: ${err.message}`;
  }
}

init();
