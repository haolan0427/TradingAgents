/* ============================================================
   TradingAgents Web UI — Application Logic
   ============================================================ */

const API_BASE = "";

let pollTimer = null;
let currentTaskId = null;

// ---- 初始化 ----

document.addEventListener("DOMContentLoaded", async () => {
  // 设置默认日期（一年前，用本地时间避免 UTC 偏移）
  const d = new Date();
  d.setFullYear(d.getFullYear() - 1);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  document.getElementById("date").value = `${y}-${m}-${day}`;

  // 加载配置
  await loadInfo();

  // 事件绑定
  document.getElementById("validateBtn").addEventListener("click", validateTicker);
  document.getElementById("ticker").addEventListener("input", onTickerChange);
  document.getElementById("submitBtn").addEventListener("click", submitAnalysis);
  document.getElementById("downloadBtn").addEventListener("click", downloadReport);
});

// ---- API 调用 ----

async function api(method, path, body = null) {
  const opts = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body) opts.body = JSON.stringify(body);

  const resp = await fetch(`${API_BASE}${path}`, opts);
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}));
    throw new Error(detail.detail || `HTTP ${resp.status}`);
  }
  return resp.json();
}

// ---- 加载配置 ----

async function loadInfo() {
  try {
    const info = await api("GET", "/api/info");

    // 分析师复选框
    const analystsGroup = document.getElementById("analystsGroup");
    analystsGroup.innerHTML = info.analysts
      .map(
        (a) => `
        <label>
          <input type="checkbox" name="analyst" value="${a.key}"
                 ${info.defaults.analysts.includes(a.key) ? "checked" : ""}
                 data-types="${a.supported_asset_types.join(",")}">
          <span>${a.label}</span>
        </label>
      `
      )
      .join("");

    // LLM 模型下拉
    const quickSelect = document.getElementById("quickLlm");
    const deepSelect = document.getElementById("deepLlm");
    quickSelect.innerHTML = info.llm_models.quick
      .map((m) => `<option value="${m.id}">${m.label}</option>`)
      .join("");
    deepSelect.innerHTML = info.llm_models.deep
      .map((m) => `<option value="${m.id}">${m.label}</option>`)
      .join("");

    quickSelect.value = info.defaults.quick_think_llm;
    deepSelect.value = info.defaults.deep_think_llm;

    document.getElementById("apiVersion").textContent = info.defaults.quick_think_llm
      ? `v0.2.0 · ${info.defaults.quick_think_llm}`
      : "v0.2.0";

    // 启用提交按钮
    document.getElementById("submitBtn").disabled = false;
  } catch (err) {
    console.error("加载配置失败:", err);
    setStatus("failed", "无法连接到服务端，请确认 API 是否启动");
  }
}

// ---- Ticker 验证 ----

let tickerValidated = false;

async function validateTicker() {
  const ticker = document.getElementById("ticker").value.trim();
  if (!ticker) return;

  const statusEl = document.getElementById("tickerStatus");

  try {
    const result = await api("POST", "/api/validate", { ticker });

    if (result.valid) {
      statusEl.className = "validation-msg valid";
      statusEl.textContent = `✅ ${result.message}`;
      tickerValidated = true;

      // 根据 asset_type 过滤分析师
      filterAnalystsByAsset(result.asset_type);
    } else {
      statusEl.className = "validation-msg invalid";
      statusEl.textContent = `❌ ${result.message}`;
      tickerValidated = false;
    }
  } catch (err) {
    statusEl.className = "validation-msg invalid";
    statusEl.textContent = `❌ 验证失败: ${err.message}`;
    tickerValidated = false;
  }
}

function onTickerChange() {
  tickerValidated = false;
  const statusEl = document.getElementById("tickerStatus");
  statusEl.className = "validation-msg";
  statusEl.textContent = "点击 🔍 验证";
}

function filterAnalystsByAsset(assetType) {
  document.querySelectorAll('input[name="analyst"]').forEach((cb) => {
    const supported = cb.dataset.types.split(",");
    const isSupported = supported.includes(assetType);
    cb.disabled = !isSupported;
    if (!isSupported) cb.checked = false;
    // 视觉提示
    cb.closest("label").style.opacity = isSupported ? "1" : "0.4";
  });
}

// ---- 提交分析 ----

async function submitAnalysis() {
  // 先验证 ticker
  if (!tickerValidated) {
    await validateTicker();
    if (!tickerValidated) {
      setStatus("failed", "请先输入有效的股票代码");
      return;
    }
  }

  const ticker = document.getElementById("ticker").value.trim();
  const date = document.getElementById("date").value;
  if (!date) {
    setStatus("failed", "请选择分析日期");
    return;
  }

  const analysts = Array.from(
    document.querySelectorAll('input[name="analyst"]:checked')
  ).map((cb) => cb.value);

  if (analysts.length === 0) {
    setStatus("failed", "请至少选择一位分析师");
    return;
  }

  const researchDepth = document.querySelector('input[name="depth"]:checked').value;
  const quickLlm = document.getElementById("quickLlm").value;
  const deepLlm = document.getElementById("deepLlm").value;
  const language = document.getElementById("language").value;
  const saveReport = document.getElementById("saveReport").checked;

  const body = {
    ticker,
    date,
    analysts,
    research_depth: researchDepth,
    quick_think_llm: quickLlm,
    deep_think_llm: deepLlm,
    output_language: language,
    save_report: saveReport,
  };

  // 隐藏旧结果
  document.getElementById("decisionCard").classList.add("hidden");
  document.getElementById("errorCard").classList.add("hidden");
  document.getElementById("emptyState").classList.add("hidden");
  document.getElementById("statusCard").classList.remove("hidden");

  setStatus("pending", "正在提交任务...");

  try {
    const resp = await api("POST", "/api/analyze", body);
    currentTaskId = resp.task_id;
    document.getElementById("taskIdDisplay").textContent = `ID: ${resp.task_id.slice(0, 12)}...`;

    setStatus("running", "任务已进入队列，等待 worker 处理...");
    updateProgressBar(10);

    // 开始轮询
    startPolling(resp.task_id);
  } catch (err) {
    setStatus("failed", `提交失败: ${err.message}`);
  }
}

// ---- 轮询结果 ----

function startPolling(taskId) {
  clearInterval(pollTimer);

  let attempts = 0;
  const maxAttempts = 600; // 最多等 30 分钟（600 * 3s）

  pollTimer = setInterval(async () => {
    attempts++;
    if (attempts > maxAttempts) {
      clearInterval(pollTimer);
      setStatus("failed", "轮询超时，任务可能仍在运行，请用 task_id 手动查询");
      return;
    }

    try {
      const result = await api("GET", `/api/result/${taskId}`);

      if (result.status === "pending" || result.status === "queued") {
        setStatus("pending", "任务排队中...");
        updateProgressBar(15);
        return;
      }

      if (result.status === "running") {
        setStatus("running", result.progress?.message || "分析进行中...");
        // 进度从 20% 逐渐增加到 80%
        const progress = Math.min(20 + (attempts * 2), 80);
        updateProgressBar(progress);
        return;
      }

      // done 或 failed
      clearInterval(pollTimer);

      if (result.status === "failed") {
        setStatus("failed", result.progress?.message || "任务失败");
        updateProgressBar(100, "failed");
        showError(result.error);
        return;
      }

      if (result.status === "done") {
        setStatus("done", "分析完成");
        updateProgressBar(100, "done");
        showDecision(result.decision);
      }
    } catch (err) {
      // 网络错误重试
      if (attempts > 10) {
        clearInterval(pollTimer);
        setStatus("failed", `查询失败: ${err.message}`);
      }
    }
  }, 3000);
}

// ---- 显示结果 ----

function showDecision(decision) {
  if (!decision) return;

  // 信号标记
  const signal = decision.signal || "";
  const signalBadge = document.getElementById("signalBadge");
  signalBadge.textContent = signal;
  signalBadge.className = "signal-badge " + signal.toLowerCase();

  // 摘要信息
  document.getElementById("resultTicker").textContent = decision.ticker;
  document.getElementById("resultDate").textContent = decision.date;
  document.getElementById("resultSignal").textContent = signal;
  document.getElementById("resultSavedPath").textContent =
    decision._saved_report_path || "未保存";

  // 投资计划 & 交易提案
  document.getElementById("resultInvestmentPlan").textContent =
    decision.investment_plan || "无";
  document.getElementById("resultTraderProposal").textContent =
    decision.trader_proposal || "无";

  // 显示下载按钮
  document.getElementById("downloadBtn").classList.remove("hidden");

  document.getElementById("decisionCard").classList.remove("hidden");
}

// ---- 下载报告 ----

async function downloadReport() {
  if (!currentTaskId) return;

  const btn = document.getElementById("downloadBtn");
  btn.textContent = "⏳ 下载中...";
  btn.disabled = true;

  try {
    const resp = await fetch(`/api/report/${currentTaskId}`);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      alert(`下载失败: ${err.detail || resp.statusText}`);
      return;
    }

    // 获取内容并触发下载
    const blob = await resp.blob();
    const disposition = resp.headers.get("Content-Disposition");
    const match = disposition && disposition.match(/filename="?([^"]+)"?/);
    const filename = match ? match[1] : `TradingAgents_report_${currentTaskId.slice(0, 8)}.md`;

    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch (err) {
    alert(`下载失败: ${err.message}`);
  } finally {
    btn.textContent = "⬇️ 下载报告";
    btn.disabled = false;
  }
}

function showError(errorText) {
  document.getElementById("errorCard").classList.remove("hidden");
  document.getElementById("errorDetail").textContent = errorText || "未知错误";
}

// ---- 状态更新辅助 ----

function setStatus(status, message) {
  const badge = document.getElementById("statusBadge");
  const msgEl = document.getElementById("statusMessage");

  const labels = {
    pending: "⏳ 排队中",
    running: "🔄 运行中",
    done: "✅ 已完成",
    failed: "❌ 失败",
  };

  badge.textContent = labels[status] || status;
  badge.className = "status-badge " + status;
  msgEl.textContent = message || "";
}

function updateProgressBar(percent, state) {
  const bar = document.getElementById("progressBar");
  bar.style.width = `${percent}%`;
  if (state) {
    bar.className = "progress-bar " + state;
  }
}
