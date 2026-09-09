"use strict";

const chatEl = document.getElementById("chat");
const welcomeEl = document.getElementById("welcome");
const promptEl = document.getElementById("prompt");
const sendBtn = document.getElementById("sendBtn");
const fileInput = document.getElementById("fileInput");
const chipsEl = document.getElementById("chips");
const clearBtn = document.getElementById("clearBtn");
const sessionListEl = document.getElementById("sessionList");
const newChatBtn = document.getElementById("newChatBtn");
const sidebarEl = document.getElementById("sidebar");
const sidebarToggle = document.getElementById("sidebarToggle");
const exportLogBtn = document.getElementById("exportLogBtn");
const svcBanner = document.getElementById("svcBanner");
const svcRetryBtn = document.getElementById("svcRetryBtn");

/* ---------------- 客户端错误上报（原文进服务端日志） ---------------- */
function reportClientError(message, stack) {
    try {
        fetch("/api/client-log", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                level: "error",
                message: String(message).slice(0, 4000),
                stack: String(stack || "").slice(0, 8000),
                url: location.href,
            }),
        }).catch(() => {});
    } catch { /* 上报本身绝不影响主流程 */ }
}
window.addEventListener("error", (e) =>
    reportClientError(e.message, e.error && e.error.stack));
window.addEventListener("unhandledrejection", (e) => {
    const r = e.reason;
    reportClientError("UnhandledRejection: " + (r && (r.stack || r.message) || r), "");
});

/* ---------------- 导出报错日志（先确认用途，避免误解为对话导出） ---------------- */
const exportModal = document.getElementById("exportModal");
const exportConfirmBtn = document.getElementById("exportConfirmBtn");
const DEVELOPER_MAILTO = "boyang.xie@molex.com";

exportLogBtn.addEventListener("click", () => exportModal.classList.remove("hidden"));
exportModal.querySelectorAll("[data-close-export]").forEach((el) =>
    el.addEventListener("click", () => exportModal.classList.add("hidden"))
);

exportConfirmBtn.addEventListener("click", async () => {
    exportConfirmBtn.disabled = true;
    try {
        // 桌面窗口：WebView2 不处理页内下载，走 pywebview 原生"另存为"对话框落盘
        if (window.pywebview && window.pywebview.api && window.pywebview.api.save_export) {
            const path = await window.pywebview.api.save_export();
            if (path) {
                showToast("日志已导出", `已保存到：${path}\n请发送给开发者邮箱 ${DEVELOPER_MAILTO}`);
                exportModal.classList.add("hidden");
            } else {
                showToast("已取消导出", "未选择保存位置。");
            }
            return;
        }
        // 浏览器入口：blob 触发下载
        const res = await fetch("/api/logs/export");
        if (!res.ok) throw new Error(res.statusText || "服务返回异常");
        const blob = await res.blob();
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `hr-cv-logs-${new Date().toISOString().slice(0, 10)}.zip`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(a.href);
        showToast("日志已导出", `请将下载的 zip 发送给开发者邮箱 ${DEVELOPER_MAILTO}`);
        exportModal.classList.add("hidden");
    } catch (err) {
        netFail(err);
        showToast("导出失败", err.message);
    } finally {
        exportConfirmBtn.disabled = false;
    }
});

/* ---------------- 退出确认（桌面版点 × 时由后台唤起，居中模态） ---------------- */
const exitModal = document.getElementById("exitModal");

// 桌面壳在 closing 事件里通过 evaluate_js 调用；返回 true 表示弹窗已展示
window.hrExitConfirm = function () {
    exitModal.classList.remove("hidden");
    return true;
};

document.getElementById("exitCancelBtn").addEventListener("click", () =>
    exitModal.classList.add("hidden")
);

document.getElementById("exitOkBtn").addEventListener("click", async () => {
    exitModal.classList.add("hidden");
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.request_exit) {
            await window.pywebview.api.request_exit();
        }
    } catch (err) {
        reportClientError(`request_exit: ${err.message}`, err.stack);
        showToast("退出失败", err.message);
    }
});

/* ---------------- 服务未运行横幅 ---------------- */
let svcProbeTimer = null;
function showSvcBanner() {
    svcBanner.classList.remove("hidden");
    if (!svcProbeTimer) svcProbeTimer = setInterval(async () => {
        try { const r = await fetch("/api/health"); if (r.ok) hideSvcBanner(); } catch { /* 仍不可达 */ }
    }, 4000);
}
function hideSvcBanner() {
    svcBanner.classList.add("hidden");
    if (svcProbeTimer) { clearInterval(svcProbeTimer); svcProbeTimer = null; }
}
svcRetryBtn.addEventListener("click", async () => {
    try { const r = await fetch("/api/health"); if (r.ok) hideSvcBanner(); } catch { /* 保持横幅 */ }
});
function netFail(err) { if (String((err && err.message) || err).includes("Failed to fetch")) showSvcBanner(); }

/* ---------------- LLM 接入设置弹窗 ---------------- */
const llmModal = document.getElementById("llmModal");
const llmSettingsBtn = document.getElementById("llmSettingsBtn");
const llmBaseUrl = document.getElementById("llmBaseUrl");
const llmApiKey = document.getElementById("llmApiKey");
const llmModel = document.getElementById("llmModel");
const llmEffort = document.getElementById("llmEffort");
const llmTestBtn = document.getElementById("llmTestBtn");
const llmSaveBtn = document.getElementById("llmSaveBtn");
const llmTestResult = document.getElementById("llmTestResult");

function showTestResult(ok, text) {
    llmTestResult.className = "llm-test-result " + (ok ? "ok" : "err");
    llmTestResult.textContent = text;
}

let llmConfigCache = null; // 预检缓存：{base_url, model, has_key, ...}
async function getLlmConfig(force = false) {
    if (!force && llmConfigCache) return llmConfigCache;
    llmConfigCache = await api("/api/llm/config");
    return llmConfigCache;
}
function invalidateLlmConfig() { llmConfigCache = null; }

async function openLlmModal() {
    llmModal.classList.remove("hidden");
    llmTestResult.className = "llm-test-result hidden";
    llmApiKey.value = "";
    try {
        const c = await getLlmConfig();
        llmBaseUrl.value = c.base_url || "";
        llmModel.value = c.model || "";
        llmEffort.value = c.reasoning_effort || "";
        llmApiKey.placeholder = c.has_key ? `已保存 ${c.api_key_masked}` : "sk-...";
    } catch (err) {
        showTestResult(false, `读取当前配置失败：${err.message}`);
    }
}
llmSettingsBtn.addEventListener("click", openLlmModal);
llmModal.querySelectorAll("[data-close-llm]").forEach((el) =>
    el.addEventListener("click", () => llmModal.classList.add("hidden"))
);

function collectLlmForm() {
    return {
        base_url: llmBaseUrl.value.trim(),
        api_key: llmApiKey.value.trim(),
        model: llmModel.value.trim(),
        reasoning_effort: llmEffort.value,
    };
}

llmTestBtn.addEventListener("click", async () => {
    const p = collectLlmForm();
    if (!p.base_url || !p.model) {
        showTestResult(false, "请先填写 Base URL 与模型 ID");
        return;
    }
    llmTestBtn.disabled = true;
    showTestResult(false, "测试中…");
    try {
        const r = await api("/api/llm/test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(p),
        });
        if (r.ok) {
            const reply = r.reply ? `，模型回复：${r.reply}` : "";
            showTestResult(true, `✓ 连接成功（${r.latency_ms}ms，模型 ${r.model}）${reply}`);
        } else {
            const status = r.status ? ` [HTTP ${r.status}]` : "";
            showTestResult(false, `✗ 连接失败 ${r.error_type}${status}\n${r.error}`);
        }
    } catch (err) {
        showTestResult(false, `✗ 测试请求失败：${err.message}`);
    } finally {
        llmTestBtn.disabled = false;
    }
});

llmSaveBtn.addEventListener("click", async () => {
    const p = collectLlmForm();
    if (!p.base_url || !p.model) {
        showTestResult(false, "Base URL 与模型 ID 不能为空");
        return;
    }
    if (!p.api_key) delete p.api_key; // 留空 = 保留原值
    llmSaveBtn.disabled = true;
    try {
        await api("/api/llm/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(p),
        });
        showToast("已保存", "模型接入配置已更新，立即生效。");
        invalidateLlmConfig(); // 本标签页立即生效（广播只送达其他标签页）
        broadcast("llm-config-changed", {});
        llmModal.classList.add("hidden");
    } catch (err) {
        showTestResult(false, `保存失败：${err.message}`);
    } finally {
        llmSaveBtn.disabled = false;
    }
});

// 当前会话 id（服务端持久化）
let currentSid = "";
// 本次会话上传的简历
let uploadedIds = []; // [{id, filename}]
let busy = false;      // 对话请求进行中
let uploading = false; // 上传批次进行中
// meta 事件里下发的简历索引映射：index -> {id, filename, method}
let refMap = {};

// 助手头像：Lucide "bot" 图标（ISC License, lucide.dev），白色线条衬黑色圆底
const ROBOT_SVG = `
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
     stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <path d="M12 8V4H8"/>
  <rect width="16" height="12" x="4" y="8" rx="2"/>
  <path d="M2 14h2"/>
  <path d="M20 14h2"/>
  <path d="M15 13v2"/>
  <path d="M9 13v2"/>
</svg>`;

function escapeHtml(s) {
    return s.replace(/[&<>"']/g, (c) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
}

function escAttr(s) {
    return String(s).replace(/"/g, "&quot;");
}

/* ---------------- 会话管理 ---------------- */
async function api(url, opts) {
    let res;
    try {
        res = await fetch(url, opts);
    } catch (err) {
        netFail(err); // 网络不可达：亮出「服务未运行」横幅
        throw err;
    }
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    return res.json();
}

/* ---------------- 多标签同步（BroadcastChannel） ---------------- */
const bc = "BroadcastChannel" in window ? new BroadcastChannel("hr-cv") : null;
function broadcast(type, data) {
    try { if (bc) bc.postMessage(Object.assign({ type }, data || {})); } catch { /* 通知失败不影响主流程 */ }
}
function recoverToLatest() {
    return (async () => {
        const list = await api("/api/sessions").catch(() => []);
        if (list.length) await loadSession(list[0].id);
        else await newSession();
    })();
}
if (bc) bc.onmessage = (e) => {
    const d = e.data || {};
    if (d.type === "llm-config-changed") { invalidateLlmConfig(); return; }
    renderSessionList(); // 其余事件都伴随列表变化
    if (d.type === "session-deleted") {
        if (d.sid === currentSid) { localStorage.removeItem("hr_cv.sid"); recoverToLatest(); }
        return;
    }
    if (d.type === "session-switched") {
        if (!busy && d.sid && d.sid !== currentSid) loadSession(d.sid);
        return;
    }
    if (d.type === "messages-changed" && d.sid === currentSid && !busy) loadSession(d.sid);
};

function relTime(ts) {
    const s = Date.now() / 1000 - ts;
    if (s < 60) return "刚刚";
    if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
    if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
    return `${Math.floor(s / 86400)} 天前`;
}

async function renderSessionList() {
    const list = await api("/api/sessions").catch(() => []);
    sessionListEl.innerHTML = "";
    list.forEach((s) => {
        const item = document.createElement("div");
        item.className = "session-item" + (s.id === currentSid ? " active" : "");
        item.innerHTML = `
            <div class="s-title">${escapeHtml(s.title)}</div>
            <div class="s-time">${relTime(s.updated_at)} · ${s.resume_count} 份简历</div>
            <button class="s-delete" title="删除会话">×</button>`;
        item.addEventListener("click", () => {
            if (s.id !== currentSid) {
                loadSession(s.id);
                broadcast("session-switched", { sid: s.id });
            }
            sidebarEl.classList.remove("open");
        });
        item.querySelector(".s-delete").addEventListener("click", async (e) => {
            e.stopPropagation();
            await api(`/api/sessions/${s.id}`, { method: "DELETE" });
            broadcast("session-deleted", { sid: s.id });
            if (s.id === currentSid) await initSessions();
            else await renderSessionList();
        });
        sessionListEl.appendChild(item);
    });
}

function resetChatUI() {
    chatEl.querySelectorAll(".msg").forEach((m) => m.remove());
    chipsEl.innerHTML = "";
    uploadedIds = [];
    refMap = {};
    welcomeEl.style.display = "";
}

async function loadSession(sid) {
    try {
        const d = await api(`/api/sessions/${sid}`);
        currentSid = sid;
        localStorage.setItem("hr_cv.sid", sid);
        resetChatUI();
        // 恢复简历 chips（含解析标签）
        (d.resumes || []).forEach((r) => {
            uploadedIds.push({ id: r.id, filename: r.filename });
            const chip = addLoadingChip(r.filename);
            markChipDone(chip, r.id, r.filename);
            if (r.method) markChipParsed(r.id, r.method);
            else if (r.failed) markChipError(chip, r.failed);
        });
        // 恢复消息
        (d.messages || []).forEach((m) => {
            if (m.role === "user") {
                addMsg("user", m.content);
                return;
            }
            const { bubble, col } = addAssistantBubble();
            attachReasoningPanel(col, bubble, m.reasoning || "", m.reasoning_seconds);
            if (Array.isArray(m.refs) && m.refs.length) {
                refMap = {};
                m.refs.forEach((r) => { refMap[r.index] = r; });
            }
            bubble.innerHTML = renderMarkdown(m.content || "");
        });
        scrollBottom();
        await renderSessionList();
    } catch (err) {
        reportClientError(`loadSession(${sid}): ${err.message}`, err.stack);
        addMsg("ai", `载入会话失败：${err.message}`);
    }
}

async function newSession() {
    const d = await api("/api/sessions", { method: "POST" });
    currentSid = d.id;
    localStorage.setItem("hr_cv.sid", currentSid);
    resetChatUI();
    await renderSessionList();
    broadcast("session-switched", { sid: currentSid });
}

async function initSessions() {
    const list = await api("/api/sessions").catch(() => []);
    const saved = localStorage.getItem("hr_cv.sid");
    if (saved && list.some((s) => s.id === saved)) await loadSession(saved);
    else if (list.length) await loadSession(list[0].id);
    else await newSession();
}

newChatBtn.addEventListener("click", newSession);
sidebarToggle.addEventListener("click", () => sidebarEl.classList.toggle("open"));

/* ---------------- 文件上传（只传文件，解析延迟到发送后） ---------------- */
fileInput.addEventListener("change", async () => {
    const files = Array.from(fileInput.files);
    fileInput.value = ""; // 立即清空，允许重复选择同一文件
    if (!files.length || uploading) return;
    if (!currentSid) await newSession();

    uploading = true;
    sendBtn.disabled = true;
    fileInput.disabled = true;

    // 1) 立即为每个文件创建 loading 态 chip（圆形 spinner）
    const items = files.map((f) => ({ file: f, chip: addLoadingChip(f.name) }));
    let ok = 0;
    let broken = 0;  // 已载入但文件损坏（裂纹图标标出）
    const brokenNames = [];
    let fail = 0;
    let firstErr = "";

    // 2) 顺序逐个上传（轻量校验打开性，不做 OCR，秒级完成）
    for (const it of items) {
        const ext = it.file.name.includes(".") ? it.file.name.split(".").pop().toLowerCase() : "";
        if (ext !== "pdf" && ext !== "md") {
            markChipError(it.chip, "仅支持 PDF 与 MD 格式");
            fail++;
            if (!firstErr) firstErr = "仅支持 PDF 与 MD 格式";
            continue;
        }
        try {
            const form = new FormData();
            form.append("session_id", currentSid);
            form.append("files", it.file);
            const data = await api("/api/upload", { method: "POST", body: form });
            const c = data.created && data.created[0];
            if (!c) {
                const e = data.errors && data.errors[0];
                throw new Error((e && e.error) || "保存失败");
            }
            uploadedIds.push({ id: c.id, filename: c.filename });
            markChipDone(it.chip, c.id, c.filename);
            if (c.failed) {
                // 文件损坏（PDF 打不开等）：立即裂开 + Toast
                markChipError(it.chip, c.failed);
                showToast("文件损坏，无法解析", `${c.filename}：原始文件损坏，无法解析。原始错误：${c.failed}`);
                broken++;
                brokenNames.push(c.filename);
            } else {
                ok++;
            }
        } catch (err) {
            netFail(err);
            const msg = err.message === "Failed to fetch"
                ? "无法连接服务，请确认服务已启动"
                : err.message;
            markChipError(it.chip, msg);
            fail++;
            if (!firstErr) firstErr = msg;
        }
    }

    // 3) 汇总提示
    if (ok || broken) {
        const brokenNote = broken
            ? `，其中 ${broken} 份原始文件损坏，无法解析（${brokenNames.join("、")}，已用裂纹图标标出）`
            : "";
        addMsg("ai", `已载入 ${ok} 份正常简历${brokenNote}${fail ? `，${fail} 份失败` : ""}。发送问题后将自动提取文字（扫描版 PDF 走 OCR）。`);
    } else {
        addMsg("ai", `本次 ${fail} 份文件均未载入：${firstErr}。失败原因见输入框上方红色标注。`);
    }
    renderSessionList(); // resume_count 变化
    broadcast("sessions-changed", {});

    uploading = false;
    fileInput.disabled = false;
    sendBtn.disabled = busy;
});

function addLoadingChip(filename) {
    const chip = document.createElement("span");
    chip.className = "chip loading";
    chip.dataset.origName = filename;
    chip.innerHTML = `<span class="spinner"></span><span class="chip-name">${escapeHtml(filename)}</span>`;
    chipsEl.appendChild(chip);
    return chip;
}

function chipRemoveHandler(chip, filename) {
    chip.querySelector(".remove").addEventListener("click", () => {
        const idx = uploadedIds.findIndex((r) => r.filename === filename);
        if (idx > -1) uploadedIds.splice(idx, 1);
        chip.remove();
    });
}

/** 上传完成态：文件图标 + 文件名（方法标签 OCR/MD 等解析后再补） */
function markChipDone(chip, id, filename) {
    const ext = filename.toLowerCase().endsWith(".md") ? "md" : "pdf";
    chip.className = "chip";
    chip.dataset.id = id;
    chip.dataset.filename = filename;
    chip.innerHTML =
        `<span class="file-icon ${ext}">${ext.toUpperCase()}</span>` +
        `<span class="chip-name">${escapeHtml(filename)}</span>` +
        `<button class="remove" title="移除">×</button>`;
    chipRemoveHandler(chip, filename);
}

/** 解析完成态（发送后收到 parse 事件时）：补上方法标签 */
function markChipParsed(id, method) {
    const chip = chipsEl.querySelector(`.chip[data-id="${id}"]`);
    if (!chip) return;
    chip.classList.remove("loading");
    const spinner = chip.querySelector(".spinner");
    if (spinner) spinner.remove();
    if (!chip.querySelector(".tag")) {
        const tag = document.createElement("span");
        tag.className = "tag";
        tag.textContent = method === "ocr" ? "OCR" : method.toUpperCase();
        chip.insertBefore(tag, chip.querySelector(".remove"));
    }
}

/** 解析进行中（发送后）：chip 回到 loading 态 */
function markChipParsing(id) {
    const chip = chipsEl.querySelector(`.chip[data-id="${id}"]`);
    if (!chip || chip.classList.contains("loading") || chip.querySelector(".tag")) return;
    chip.classList.add("loading");
    chip.insertBefore(Object.assign(document.createElement("span"), { className: "spinner" }),
        chip.firstChild);
}

/** 解析失败态：保留文件类型图标并叠加「裂开」效果；无法识别扩展名时退回 ⚠ 样式 */
function markChipError(chip, message) {
    chip.title = message;
    const name = chip.dataset.filename || chip.dataset.origName || "";
    const lower = name.toLowerCase();
    const ext = lower.endsWith(".md") ? "md" : lower.endsWith(".pdf") ? "pdf" : "";
    chip.className = "chip error";
    if (ext) {
        chip.innerHTML =
            `<span class="file-icon ${ext} cracked">${ext.toUpperCase()}</span>` +
            `<span class="chip-name">${escapeHtml(name)}</span>` +
            `<span class="err-mark">⚠</span>` +
            `<button class="remove" title="移除">×</button>`;
    } else {
        chip.innerHTML =
            `<span class="err-mark">⚠</span>` +
            `<span class="chip-name">${escapeHtml(name)}</span>` +
            `<button class="remove" title="移除">×</button>`;
    }
    chipRemoveHandler(chip, name);
}

/* ---------------- Toast 通知（右上角） ---------------- */
function showToast(title, detail) {
    const wrap = document.getElementById("toasts");
    while (wrap.children.length >= 5) wrap.firstElementChild.remove();
    const t = document.createElement("div");
    t.className = "toast";
    t.innerHTML =
        `<div class="toast-title">${escapeHtml(title)}</div>` +
        `<div class="toast-detail">${escapeHtml(detail)}</div>` +
        `<button class="toast-close" title="关闭">×</button>`;
    const close = () => {
        if (!t.isConnected) return;
        t.classList.add("hide");
        setTimeout(() => t.remove(), 260);
    };
    t.querySelector(".toast-close").addEventListener("click", close);
    wrap.appendChild(t);
    setTimeout(close, 6000);
}

/* ---------------- 消息 ---------------- */
function hideWelcome() { welcomeEl.style.display = "none"; }

function addMsg(role, text) {
    hideWelcome();
    const row = document.createElement("div");
    row.className = `msg ${role}`;
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = text;
    row.appendChild(bubble);
    chatEl.appendChild(row);
    scrollBottom();
    return bubble;
}

function addAssistantBubble() {
    hideWelcome();
    const row = document.createElement("div");
    row.className = "msg ai";
    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.innerHTML = ROBOT_SVG;
    const col = document.createElement("div");
    col.className = "col";
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    const meta = document.createElement("div");
    meta.className = "meta";
    col.appendChild(bubble);
    col.appendChild(meta);
    row.appendChild(avatar);
    row.appendChild(col);
    chatEl.appendChild(row);
    scrollBottom();
    return { row, bubble, meta, col };
}

/** 思维链折叠面板：插入到气泡上方。live=true 时流式追加并保持展开。 */
function attachReasoningPanel(col, bubble, text, seconds, live = false) {
    const panel = document.createElement("div");
    panel.className = "reasoning" + (live ? " open" : "");
    const head = document.createElement("div");
    head.className = "reasoning-head";
    head.textContent = live ? "思考中…" : `已深度思考（${seconds ?? "?"} 秒）`;
    const body = document.createElement("div");
    body.className = "reasoning-body";
    body.textContent = text || "";
    head.addEventListener("click", () => panel.classList.toggle("open"));
    panel.append(head, body);
    col.insertBefore(panel, bubble);
    return {
        panel, head, body,
        append(t) { body.textContent += t; scrollBottom(); },
        finish(secs) {
            head.textContent = `已深度思考（${secs ?? "?"} 秒）`;
            panel.classList.remove("open");
        },
    };
}

function scrollBottom() { chatEl.scrollTop = chatEl.scrollHeight; }

/* ---------------- 发送 ---------------- */
async function send() {
    const message = promptEl.value.trim();
    if (!message || busy || uploading || !currentSid) return;

    // LLM 配置预检：未配置时引导接入（不清空输入框、不产生气泡）
    let cfg = null;
    try { cfg = await getLlmConfig(); } catch (err) { netFail(err); return; }
    if (!cfg || !cfg.has_key || !cfg.base_url || !cfg.model) {
        showToast("尚未配置模型接入", "请先在左下角「模型接入」填写 Base URL、API Key 与模型 ID。");
        openLlmModal();
        return;
    }

    busy = true;
    promptEl.value = "";
    promptEl.style.height = "auto";
    sendBtn.disabled = true;

    const userBubble = addMsg("user", message);
    const aiUI = addAssistantBubble();
    const { bubble, meta, col, row } = aiUI;
    meta.textContent = "…";
    bubble.classList.add("typing");

    // 未解析的 chip 先回到 loading 态（等待 parse 事件逐个点亮）
    uploadedIds.forEach((r) => markChipParsing(r.id));

    const body = JSON.stringify({
        session_id: currentSid,
        message,
        resume_ids: uploadedIds.map((r) => r.id),
    });
    try {
        const res = await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body,
        });
        if (!res.ok) {
            const detail = (await res.json()).detail || res.statusText;
            if (String(detail).startsWith("LLM_NOT_CONFIGURED")) {
                userBubble.closest(".msg").remove();   // 移除刚加的用户气泡
                aiUI.row.remove();                     // 移除助手气泡
                promptEl.value = message; syncInput(); // 恢复输入框
                openLlmModal();                        // 兜底打开接入弹窗
                const e = new Error(detail); e.handled = true; throw e;
            }
            throw new Error(detail);
        }
        if (!res.body) throw new Error("无响应流");
        const reader = res.body.getReader();
        const decoder = new TextDecoder();

        bubble.classList.remove("typing");
        const tStart = performance.now();
        let raw = "";      // 助手回复原文（含 [[序号]] 引用标记）
        let reasoning = "";// 思维链原文
        let reasoningPanel = null;
        let buf = "";
        let gotText = false;

        // rAF 节流：同一帧内多次 delta 只重渲染/滚动一次（长回复 O(n²) → O(n)）
        let renderQueued = false;
        const flushRender = () => {
            renderQueued = false;
            bubble.innerHTML = renderMarkdown(raw);
            scrollBottom();
        };
        const queueRender = () => {
            if (!renderQueued) {
                renderQueued = true;
                requestAnimationFrame(flushRender);
            }
        };

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buf += decoder.decode(value, { stream: true });
            const parts = buf.split("\n\n");
            buf = parts.pop() || "";
            for (const part of parts) {
                const line = part.trim();
                if (!line.startsWith("data:")) continue;
                const payload = line.slice(5).trim();
                if (payload === "[DONE]") continue;
                let ev;
                try { ev = JSON.parse(payload); } catch { continue; }
                if (ev.type === "parse") {
                    markChipParsed(ev.id, ev.method);
                    meta.textContent = `正在提取文字（${ev.done}/${ev.total}）…`;
                } else if (ev.type === "parse_error") {
                    const chip = chipsEl.querySelector(`.chip[data-id="${ev.id}"]`);
                    if (chip) markChipError(chip, ev.error);
                    showToast("简历解析失败", `${ev.filename}：${ev.error}`);
                } else if (ev.type === "meta" && Array.isArray(ev.resumes)) {
                    refMap = {};
                    ev.resumes.forEach((r) => { refMap[r.index] = r; });
                } else if (ev.type === "stage") {
                    meta.textContent = ev.text; // 如「思考中…」
                } else if (ev.type === "reasoning") {
                    if (!reasoningPanel) reasoningPanel = attachReasoningPanel(col, bubble, "", null, true);
                    reasoning += ev.text;
                    reasoningPanel.append(ev.text);
                } else if (ev.type === "delta") {
                    if (!gotText) {
                        meta.textContent = "";
                        if (reasoningPanel) {
                            reasoningPanel.finish(((performance.now() - tStart) / 1000).toFixed(0));
                        }
                    }
                    raw += ev.text;
                    queueRender();
                    gotText = true;
                } else if (ev.type === "error") {
                    if (!gotText) raw = "";
                    const authLike = /401|403|AuthenticationError|invalid[ _-]?(api[ _-])?key|Unauthorized|unauthorized|认证失败|鉴权/i.test(ev.text);
                    raw += `\n**错误**：${ev.text}` + (authLike ? "\n\n> 请检查左下角「模型接入」中的 Base URL / API Key / 模型 ID 配置。" : "");
                    bubble.innerHTML = renderMarkdown(raw);
                }
            }
        }
        if (renderQueued) flushRender(); // 流结束：保证最终内容完整渲染
        if (reasoningPanel && !gotText) reasoningPanel.finish(((performance.now() - tStart) / 1000).toFixed(0));
        if (!raw && !reasoning) meta.textContent = "";
    } catch (err) {
        netFail(err);
        if (!err.handled) { // LLM_NOT_CONFIGURED 已在 throw 前处理（移除气泡、恢复输入框、打开弹窗）
            reportClientError(`send(): ${err.message}`, err.stack);
            bubble.classList.remove("typing");
            bubble.innerHTML = renderMarkdown(`\n**请求失败**：${err.message}`);
            meta.textContent = "";
        }
    } finally {
        busy = false;
        sendBtn.disabled = false;
        syncInput();
        renderSessionList(); // 标题/时间可能变化
        broadcast("messages-changed", { sid: currentSid });
    }
}

sendBtn.addEventListener("click", send);
promptEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        send();
    }
});

/* 输入框自适应高度 */
function syncInput() {
    promptEl.style.height = "auto";
    promptEl.style.height = Math.min(promptEl.scrollHeight, 120) + "px";
}
promptEl.addEventListener("input", syncInput);

/* 清空：清空当前会话的简历与消息（会话保留在侧边栏） */
clearBtn.addEventListener("click", async () => {
    if (!currentSid) return;
    try {
        await api("/api/clear", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: currentSid }),
        });
        await loadSession(currentSid);
        broadcast("messages-changed", { sid: currentSid });
    } catch (err) {
        addMsg("ai", `清空失败：${err.message}`);
    }
});

/* ---------------- Markdown 渲染（含引用链接） ---------------- */
function renderMarkdown(text) {
    let s = escapeHtml(text);

    // 引用标记 [[序号]] -> 可点击、hover 显示简历名的引用
    s = s.replace(/\[\[(\d+)\]\]/g, (m, n) => {
        const r = refMap[n];
        if (r) return `<a class="ref" data-id="${r.id}" title="${escAttr(r.filename)}">${n}</a>`;
        return `<span class="ref" title="简历 ${n}">${n}</span>`;
    });

    // 行内
    s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/([^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");

    // 块级：按行分组
    const lines = s.replace(/\r\n/g, "\n").split("\n");
    const out = [];
    let i = 0;
    while (i < lines.length) {
        const t = lines[i].trim();
        if (!t) { i++; continue; }

        let m;
        if ((m = t.match(/^(#{1,5})\s+(.*)$/))) {
            out.push(`<h${m[1].length + 1}>${m[2]}</h${m[1].length + 1}>`);
            i++; continue;
        }
        if (/^(-{3,}|\*{3,}|_{3,})$/.test(t)) {
            out.push("<hr/>");
            i++; continue;
        }
        if (t.startsWith("&gt; ")) {
            const q = [];
            while (i < lines.length && lines[i].trim().startsWith("&gt; ")) {
                q.push(`<p>${lines[i].trim().slice(6)}</p>`);
                i++;
            }
            out.push(`<blockquote>${q.join("")}</blockquote>`);
            continue;
        }
        if (/^[-*+]\s+/.test(t)) {
            const items = [];
            while (i < lines.length && /^[-*+]\s+/.test(lines[i].trim())) {
                items.push(`<li>${lines[i].trim().replace(/^[-*+]\s+/, "")}</li>`);
                i++;
            }
            out.push(`<ul>${items.join("")}</ul>`);
            continue;
        }
        if (/^\d+\.\s+/.test(t)) {
            const items = [];
            while (i < lines.length && /^\d+\.\s+/.test(lines[i].trim())) {
                items.push(`<li>${lines[i].trim().replace(/^\d+\.\s+/, "")}</li>`);
                i++;
            }
            out.push(`<ol>${items.join("")}</ol>`);
            continue;
        }
        const para = [];
        while (i < lines.length) {
            const tt = lines[i].trim();
            if (!tt || /^(#{1,5}\s|&gt;\s|[-*+]\s|\d+\.\s)/.test(tt)) break;
            para.push(lines[i]);
            i++;
        }
        out.push(`<p>${para.join("<br/>")}</p>`);
    }
    return out.join("\n");
}

/* ---------------- 引用点击 + 简历弹窗 ---------------- */
const modalEl = document.getElementById("modal");
const modalTitle = document.getElementById("modalTitle");
const modalBody = document.getElementById("modalBody");

chatEl.addEventListener("click", (e) => {
    const ref = e.target.closest(".ref");
    if (ref && ref.dataset.id) openResume(ref.dataset.id);
});

async function openResume(id) {
    modalTitle.textContent = "加载中…";
    modalBody.innerHTML = '<div class="badge">加载中…</div>';
    modalEl.classList.remove("hidden");
    try {
        const d = await api(`/api/resume/${id}?session_id=${encodeURIComponent(currentSid)}`);
        modalTitle.textContent = d.filename;
        modalBody.innerHTML =
            `<div class="badge">${escapeHtml(d.method)} · ${escapeHtml(d.format)}</div>` +
            escapeHtml(d.content);
    } catch (err) {
        modalBody.textContent = `加载失败：${err.message}`;
    }
}

modalEl.querySelectorAll("[data-close]").forEach((el) =>
    el.addEventListener("click", () => modalEl.classList.add("hidden"))
);

/* ---------------- 启动：恢复最近会话或新建 ---------------- */
initSessions();