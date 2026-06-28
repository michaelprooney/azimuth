/* Azimuth Fitness Dashboard */

let currentDays = 30;
let summaryData = null;
let charts = {};
let insightsLoaded = false;
let insightsGenerating = false;

const VIEW_TITLES = {
  overview: ["Overview", "Your fitness at a glance"],
  activities: ["Activities", "Recent workouts and sessions"],
  goals: ["Goals", "Track what you're working toward"],
  coach: ["Coach", "AI-powered training guidance"],
};

const ACTIVITY_ICONS = {
  running: "🏃",
  cycling: "🚴",
  swimming: "🏊",
  strength: "💪",
  default: "⚡",
};

const CHART_COLORS = {
  steps: { line: "#2dd4bf", fill: "rgba(45, 212, 191, 0.12)" },
  sleep: { line: "#818cf8", fill: "rgba(129, 140, 248, 0.12)" },
  rhr: { line: "#fb7185", fill: "rgba(251, 113, 133, 0.1)" },
  stress: { line: "#fbbf24", fill: "rgba(251, 191, 36, 0.1)" },
  volume: { bar: "rgba(45, 212, 191, 0.7)", bar2: "rgba(129, 140, 248, 0.5)" },
};

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
    if (typeof DOMPurify !== "undefined") return DOMPurify.sanitize(html);
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
    appendMessage(logEl, msg.role, msg.content, {
      label: msg.label || (msg.role === "user" ? userLabel : assistantLabel),
    });
  }
}

function formatTrend(trend) {
  if (!trend || trend.pct == null) return { cls: "neutral", text: "—" };
  const sign = trend.pct > 0 ? "+" : "";
  const cls = trend.improved ? "improved" : trend.direction === "flat" ? "neutral" : trend.direction;
  return { cls: `${trend.direction} ${trend.improved ? "improved" : ""}`.trim(), text: `${sign}${trend.pct}%` };
}

function switchView(view) {
  document.querySelectorAll(".view").forEach((el) => el.classList.remove("active"));
  document.getElementById(`view-${view}`)?.classList.add("active");
  document.querySelectorAll(".nav-item, .mobile-nav-item").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === view);
  });
  const [title, sub] = VIEW_TITLES[view] || ["Azimuth", ""];
  document.getElementById("view-title").textContent = title;
  document.getElementById("view-subtitle").textContent = sub;
  document.getElementById("period-pills").style.display = view === "overview" ? "flex" : "none";
}

function renderTodayHero(data) {
  const today = data.today || {};
  const m = today.metrics || {};
  const heroDate = document.getElementById("hero-date");
  const heroMsg = document.getElementById("hero-message");
  const heroStats = document.getElementById("hero-stats");

  const d = new Date();
  heroDate.textContent = d.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });

  if (!today.has_data) {
    heroMsg.textContent = "No data synced for today yet. Run a sync or check back after your watch uploads.";
    heroStats.innerHTML = "";
    return;
  }

  const parts = [];
  if (m.steps != null) parts.push(`${fmtNum(m.steps)} steps`);
  if (m.active_calories != null) parts.push(`${fmtNum(m.active_calories)} active cal`);
  if (m.vigorous_minutes) parts.push(`${m.vigorous_minutes} vigorous min`);
  heroMsg.textContent = parts.length ? parts.join(" · ") : "Data recorded for today.";

  const stats = [
    { lbl: "Steps", val: fmtNum(m.steps) },
    { lbl: "Sleep", val: m.sleep_hours != null ? `${m.sleep_hours}h` : "—" },
    { lbl: "Resting HR", val: m.resting_hr != null ? `${m.resting_hr}` : "—" },
    { lbl: "Stress", val: m.avg_stress ?? "—" },
  ];
  if (m.body_battery_high != null) {
    stats.push({ lbl: "Body Battery", val: `${m.body_battery_low ?? "?"}–${m.body_battery_high}` });
  }

  heroStats.innerHTML = stats
    .map((s) => `<div class="hero-stat"><span class="val">${s.val}</span><span class="lbl">${s.lbl}</span></div>`)
    .join("");
}

function renderStatGrid(data) {
  const avg = data.averages || {};
  const trends = data.trends || {};
  const totals = data.totals || {};

  const stats = [
    { key: "steps", label: "Avg Steps", value: fmtNum(avg.steps), trend: trends.steps },
    { key: "sleep", label: "Avg Sleep", value: avg.sleep_hours != null ? `${avg.sleep_hours}h` : "—", trend: trends.sleep_hours },
    { key: "rhr", label: "Resting HR", value: avg.resting_hr != null ? `${Math.round(avg.resting_hr)} bpm` : "—", trend: trends.resting_hr },
    { key: "stress", label: "Avg Stress", value: avg.avg_stress ?? "—", trend: trends.avg_stress },
    { key: "acts", label: "Activities", value: (data.recent_activities || []).length, trend: null },
    { key: "vigorous", label: "Vigorous Min", value: totals.vigorous_minutes != null ? fmtNum(totals.vigorous_minutes) : "—", trend: null },
    { key: "calories", label: "Avg Active Cal", value: avg.active_calories != null ? fmtNum(avg.active_calories) : "—", trend: trends.active_calories },
    { key: "days", label: "Days w/ Data", value: data.days_with_data ?? "—", trend: null },
  ];

  document.getElementById("stat-grid").innerHTML = stats
    .map((s) => {
      const t = formatTrend(s.trend);
      return `<div class="stat-card">
        <div class="stat-label">${s.label}</div>
        <div class="stat-value">${s.value}</div>
        <span class="stat-trend ${t.cls}">${t.text}</span>
      </div>`;
    })
    .join("");
}

function renderTrainingStrip(highlights) {
  const h = highlights || {};
  const chips = [];

  if (h.vo2_max != null) chips.push({ label: "VO₂ Max", value: h.vo2_max });
  if (h.fitness_age != null) chips.push({ label: "Fitness Age", value: h.fitness_age });
  if (h.training_status) chips.push({ label: "Status", value: h.training_status.replace(/_/g, " ") });
  if (h.load_balance) chips.push({ label: "Load", value: h.load_balance });
  if (h.weekly_training_load != null) chips.push({ label: "Weekly Load", value: h.weekly_training_load });

  const el = document.getElementById("training-chips");
  if (!chips.length) {
    el.innerHTML = '<span class="chip"><span class="chip-label">Sync data to see training metrics</span></span>';
    return;
  }

  el.innerHTML = chips
    .map((c) => `<span class="chip"><span class="chip-label">${c.label}</span> <strong>${escapeHtml(String(c.value))}</strong></span>`)
    .join("");
}

function chartDefaults() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: "#1e2733",
        titleColor: "#eef3f8",
        bodyColor: "#8b9cb0",
        borderColor: "rgba(255,255,255,0.08)",
        borderWidth: 1,
        padding: 10,
        cornerRadius: 8,
      },
    },
    scales: {
      x: {
        grid: { color: "rgba(255,255,255,0.04)" },
        ticks: { color: "#8b9cb0", maxTicksLimit: 8, font: { size: 10 } },
      },
      y: {
        grid: { color: "rgba(255,255,255,0.04)" },
        ticks: { color: "#8b9cb0", font: { size: 10 } },
        beginAtZero: false,
      },
    },
  };
}

function shortDate(dateStr) {
  const d = new Date(dateStr + "T12:00:00");
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function destroyCharts() {
  Object.values(charts).forEach((c) => c?.destroy());
  charts = {};
}

function renderCharts(data) {
  if (typeof Chart === "undefined") return;
  destroyCharts();

  const series = data.daily_series || [];
  const labels = series.map((d) => shortDate(d.date));

  function lineChart(id, field, colors, suffix = "") {
    const ctx = document.getElementById(id);
    if (!ctx) return;
    const values = series.map((d) => d[field] ?? null);
    charts[id] = new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [{
          data: values,
          borderColor: colors.line,
          backgroundColor: colors.fill,
          fill: true,
          tension: 0.35,
          pointRadius: series.length > 45 ? 0 : 3,
          pointHoverRadius: 5,
          borderWidth: 2,
          spanGaps: true,
        }],
      },
      options: {
        ...chartDefaults(),
        plugins: {
          ...chartDefaults().plugins,
          tooltip: {
            ...chartDefaults().plugins.tooltip,
            callbacks: {
              label: (ctx) => `${ctx.parsed.y ?? "—"}${suffix}`,
            },
          },
        },
      },
    });
  }

  lineChart("chart-steps", "steps", CHART_COLORS.steps);
  lineChart("chart-sleep", "sleep_hours", CHART_COLORS.sleep, "h");
  lineChart("chart-rhr", "resting_hr", CHART_COLORS.rhr, " bpm");
  lineChart("chart-stress", "avg_stress", CHART_COLORS.stress);

  const weekly = data.weekly_volume || [];
  const volCtx = document.getElementById("chart-volume");
  if (volCtx && weekly.length) {
    charts["chart-volume"] = new Chart(volCtx, {
      type: "bar",
      data: {
        labels: weekly.map((w) => w.week.replace("-W", " W")),
        datasets: [
          {
            label: "Distance (km)",
            data: weekly.map((w) => w.distance_km),
            backgroundColor: CHART_COLORS.volume.bar,
            borderRadius: 6,
            yAxisID: "y",
          },
          {
            label: "Activities",
            data: weekly.map((w) => w.count),
            backgroundColor: CHART_COLORS.volume.bar2,
            borderRadius: 6,
            yAxisID: "y1",
          },
        ],
      },
      options: {
        ...chartDefaults(),
        plugins: {
          ...chartDefaults().plugins,
          legend: {
            display: true,
            labels: { color: "#8b9cb0", boxWidth: 12, font: { size: 11 } },
          },
        },
        scales: {
          x: chartDefaults().scales.x,
          y: {
            ...chartDefaults().scales.y,
            position: "left",
            title: { display: true, text: "km", color: "#8b9cb0", font: { size: 10 } },
          },
          y1: {
            ...chartDefaults().scales.y,
            position: "right",
            grid: { drawOnChartArea: false },
            title: { display: true, text: "count", color: "#8b9cb0", font: { size: 10 } },
          },
        },
      },
    });
  }
}

function activityIcon(type) {
  const t = (type || "").toLowerCase();
  for (const [key, icon] of Object.entries(ACTIVITY_ICONS)) {
    if (t.includes(key)) return icon;
  }
  return ACTIVITY_ICONS.default;
}

function patternLabel(pattern) {
  const labels = {
    mile_repeats: "Mile repeats",
    "400m_intervals": "400m intervals",
    "800m_intervals": "800m intervals",
    kilometer_repeats: "K repeats",
    structured_intervals: "Intervals",
    single_hard_effort: "Hard effort",
    continuous: "Continuous",
    multi_lap_unstructured: "Multi-lap",
  };
  return labels[pattern] || pattern?.replace(/_/g, " ") || "";
}

function renderActivities(activities) {
  const list = document.getElementById("activities-list");
  document.getElementById("activities-count").textContent = `${activities.length} recent`;

  if (!activities.length) {
    list.innerHTML = '<div class="empty-state">No activities yet. Sync your Garmin data to get started.</div>';
    return;
  }

  list.innerHTML = activities
    .map((a) => {
      const type = (a.type || "activity").toLowerCase();
      const pattern = a.structure?.pattern;
      return `<div class="activity-row" data-id="${a.activity_id}">
        <div class="activity-icon">${activityIcon(type)}</div>
        <div class="activity-main">
          <h4>${escapeHtml(a.garmin_label || a.name || "Unnamed")}</h4>
          <div class="meta">${(a.start_time || "").slice(0, 16) || "—"}</div>
          ${pattern && pattern !== "continuous" ? `<div class="pattern-tag">${patternLabel(pattern)}</div>` : ""}
        </div>
        <div class="activity-stats">
          <div class="dist">${a.distance_km ?? "—"} km</div>
          <div>${a.duration_min != null ? `${a.duration_min} min` : "—"} · HR ${a.avg_hr ?? "—"}</div>
        </div>
        <span class="type-badge ${type.split("_")[0]}">${escapeHtml(a.type || "—")}</span>
      </div>`;
    })
    .join("");

  list.querySelectorAll(".activity-row").forEach((row) => {
    row.addEventListener("click", () => openActivityDrawer(row.dataset.id));
  });
}

async function openActivityDrawer(activityId) {
  const drawer = document.getElementById("activity-drawer");
  drawer.classList.remove("hidden");
  drawer.setAttribute("aria-hidden", "false");
  document.body.classList.add("drawer-open");

  document.getElementById("drawer-body").innerHTML = '<div class="empty-state">Loading…</div>';

  try {
    const d = await api(`/api/activities/${activityId}`);
    document.getElementById("drawer-type").textContent = d.activity_type || "Activity";
    document.getElementById("drawer-title").textContent = d.garmin_label || "Unnamed";
    document.getElementById("drawer-date").textContent = d.start_time || "—";

    const s = d.summary || {};
    const inf = d.inferred_structure || {};
    const pace = d.pace_summary || {};

    let body = `<div class="detail-grid">
      <div class="detail-item"><div class="lbl">Distance</div><div class="val">${s.distance_km ?? "—"} km</div></div>
      <div class="detail-item"><div class="lbl">Duration</div><div class="val">${s.duration_min ?? "—"} min</div></div>
      <div class="detail-item"><div class="lbl">Avg HR</div><div class="val">${s.avg_hr ?? "—"} bpm</div></div>
      <div class="detail-item"><div class="lbl">Max HR</div><div class="val">${s.max_hr ?? "—"} bpm</div></div>
      <div class="detail-item"><div class="lbl">Avg Pace</div><div class="val">${s.avg_pace_min_per_mile ? s.avg_pace_min_per_mile + "/mi" : "—"}</div></div>
      <div class="detail-item"><div class="lbl">Calories</div><div class="val">${s.calories ?? "—"}</div></div>
    </div>`;

    if (inf.description) {
      body += `<div class="drawer-section"><h4>Structure</h4><p>${escapeHtml(inf.description)}</p></div>`;
    }

    if (pace.avg_work_pace_min_per_mile) {
      body += `<div class="drawer-section"><h4>Work Intervals</h4>
        <p>Avg pace <strong>${pace.avg_work_pace_min_per_mile}/mi</strong>
        · Best ${pace.best_work_pace_min_per_mile}/mi · ${pace.work_interval_count} intervals</p></div>`;
    }

    const work = d.work_intervals || [];
    if (work.length) {
      body += `<div class="drawer-section"><h4>Lap Breakdown</h4>
        <table class="lap-table"><thead><tr><th>#</th><th>Dist</th><th>Time</th><th>Pace</th><th>HR</th></tr></thead><tbody>
        ${work.map((lap) => `<tr>
          <td>${lap.lap}</td>
          <td>${lap.distance_km ?? "—"} km</td>
          <td>${lap.duration ?? "—"}</td>
          <td>${lap.pace_min_per_mile ? lap.pace_min_per_mile + "/mi" : "—"}</td>
          <td>${lap.avg_hr ?? "—"}</td>
        </tr>`).join("")}
        </tbody></table></div>`;
    }

    document.getElementById("drawer-body").innerHTML = body;
  } catch (err) {
    document.getElementById("drawer-body").innerHTML = `<div class="empty-state">Error: ${escapeHtml(err.message)}</div>`;
  }
}

function closeActivityDrawer() {
  document.getElementById("activity-drawer").classList.add("hidden");
  document.getElementById("activity-drawer").setAttribute("aria-hidden", "true");
  document.body.classList.remove("drawer-open");
}

function renderGoals(goals) {
  const ul = document.getElementById("goals-list");
  if (!goals.length) {
    ul.innerHTML = '<li class="empty-state" style="list-style:none">No goals yet. Add one above to track your progress.</li>';
    return;
  }
  ul.innerHTML = goals
    .map(
      (g) => `<li>
      <span class="goal-cat">${escapeHtml(g.category)}</span>
      <div class="goal-text">${escapeHtml(g.text)}</div>
      <div class="goal-meta">${g.target_date ? "Target: " + g.target_date : ""}</div>
      <span class="goal-status ${g.status}">${g.status}</span>
      <button class="btn secondary small" data-complete="${g.id}">Done</button>
      <button class="btn secondary small" data-delete="${g.id}">×</button>
    </li>`
    )
    .join("");

  ul.querySelectorAll("[data-complete]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await api(`/api/goals/${btn.dataset.complete}`, { method: "PATCH", body: JSON.stringify({ status: "completed" }) });
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

async function loadStatus() {
  const status = await api("/api/status");
  const line = document.getElementById("status-line");
  if (!status.cache_exists) {
    line.textContent = "No export yet — sync to pull Garmin data";
    return;
  }
  const range = status.date_range;
  const rangeText = range ? `${range.start} → ${range.end}` : "no daily data";
  line.textContent = `${status.daily_days} days · ${status.activities} activities · ${rangeText}`;
}

async function loadSummary() {
  summaryData = await api(`/api/summary?days=${currentDays}`);
  renderTodayHero(summaryData);
  renderStatGrid(summaryData);
  renderTrainingStrip(summaryData.training_highlights);
  renderCharts(summaryData);
  renderActivities(summaryData.recent_activities || []);
}

async function loadGoals() {
  const data = await api("/api/goals");
  renderGoals(data.goals);
}

async function loadChat() {
  const data = await api("/api/chat");
  renderChat(data.history);
}

/* Insights overlay */
function openInsightsOverlay() {
  const overlay = document.getElementById("insights-overlay");
  overlay.classList.remove("hidden");
  overlay.setAttribute("aria-hidden", "false");
  document.body.classList.add("overlay-open");
  document.getElementById("insights-input").focus();
  if (!insightsLoaded) loadInsightsSession();
}

function closeInsightsOverlay() {
  document.getElementById("insights-overlay").classList.add("hidden");
  document.getElementById("insights-overlay").setAttribute("aria-hidden", "true");
  document.body.classList.remove("overlay-open");
}

async function loadInsightsSession() {
  const log = document.getElementById("insights-log");
  log.innerHTML = "";
  const [chatData, insightsData] = await Promise.all([api("/api/chat"), api("/api/insights")]);
  const messages = [];

  if (insightsData.insights?.text) {
    messages.push({ role: "assistant", content: insightsData.insights.text, label: "Training Analysis" });
  }
  for (const msg of chatData.history || []) {
    messages.push({ role: msg.role, content: msg.content, label: msg.role === "user" ? "You" : "Coach" });
  }
  if (!messages.length) {
    messages.push({
      role: "assistant",
      content: "Click **Refresh Analysis** to get a personalized review of your training data and goals.",
      label: "Coach",
    });
  }
  for (const msg of messages) appendMessage(log, msg.role, msg.content, { label: msg.label });
  insightsLoaded = true;
}

async function generateInsights() {
  if (insightsGenerating) return;
  insightsGenerating = true;
  const btn = document.getElementById("btn-insights-refresh");
  const log = document.getElementById("insights-log");
  btn.disabled = true;
  btn.textContent = "Analyzing…";
  const loading = appendMessage(log, "assistant", "_Analyzing your Garmin data and goals…_", { label: "Training Analysis" });
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

/* Event listeners */
document.querySelectorAll(".nav-item, .mobile-nav-item").forEach((btn) => {
  btn.addEventListener("click", () => switchView(btn.dataset.view));
});

document.querySelectorAll(".pill").forEach((pill) => {
  pill.addEventListener("click", async () => {
    document.querySelectorAll(".pill").forEach((p) => p.classList.remove("active"));
    pill.classList.add("active");
    currentDays = parseInt(pill.dataset.days, 10);
    await loadSummary();
  });
});

document.getElementById("goal-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await api("/api/goals", {
    method: "POST",
    body: JSON.stringify({
      text: document.getElementById("goal-text").value.trim(),
      target_date: document.getElementById("goal-date").value || null,
      category: document.getElementById("goal-category").value,
    }),
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
    const data = await api("/api/chat", { method: "POST", body: JSON.stringify({ message }) });
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
  const log = document.getElementById("insights-log");
  appendMessage(log, "user", message);
  input.disabled = true;
  const loading = appendMessage(log, "assistant", "_Thinking…_", { label: "Coach" });
  try {
    const data = await api("/api/chat", { method: "POST", body: JSON.stringify({ message }) });
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
});

document.getElementById("insights-overlay").addEventListener("click", (e) => {
  if (e.target.id === "insights-overlay") closeInsightsOverlay();
});

document.getElementById("drawer-close").addEventListener("click", closeActivityDrawer);
document.getElementById("drawer-backdrop").addEventListener("click", closeActivityDrawer);

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (!document.getElementById("insights-overlay").classList.contains("hidden")) closeInsightsOverlay();
    if (!document.getElementById("activity-drawer").classList.contains("hidden")) closeActivityDrawer();
  }
});

document.getElementById("btn-export").addEventListener("click", async () => {
  const btn = document.getElementById("btn-export");
  btn.disabled = true;
  btn.innerHTML = '<span class="sync-icon">↻</span> Syncing…';
  try {
    await api("/api/export/update", { method: "POST" });
    btn.innerHTML = '<span class="sync-icon">↻</span> Sync started';
    setTimeout(() => {
      btn.innerHTML = '<span class="sync-icon">↻</span> Sync Garmin';
      btn.disabled = false;
    }, 3000);
  } catch (err) {
    alert(err.message);
    btn.innerHTML = '<span class="sync-icon">↻</span> Sync Garmin';
    btn.disabled = false;
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
