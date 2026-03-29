let ws;
let calls = [];
let sessions = [];
let settings = { max_fail_count: 3 };
let currentPage = 1;

const pageSize = 20;
const basePath = window.location.pathname.endsWith("/")
    ? window.location.pathname
    : window.location.pathname.slice(0, window.location.pathname.lastIndexOf("/") + 1);

function buildHttpUrl(path) {
    return `${basePath}${path.replace(/^\//, "")}`;
}

function buildWsUrl(path) {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${window.location.host}${buildHttpUrl(path)}`;
}

function escapeHtml(text) {
    if (!text) return "";
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

function updateStats(stats) {
    document.getElementById("totalCalls").textContent = stats.total_calls;
    document.getElementById("successCalls").textContent = stats.successful_calls;
    document.getElementById("failedCalls").textContent = stats.failed_calls;

    if (stats.total_calls > 0) {
        const rate = (stats.successful_calls / stats.total_calls * 100).toFixed(1);
        document.getElementById("successRate").textContent = `${rate}%`;
    } else {
        document.getElementById("successRate").textContent = "-";
    }
}

function showModal(src) {
    document.getElementById("modalImage").src = src;
    document.getElementById("imageModal").classList.add("active");
}

function closeModal() {
    document.getElementById("imageModal").classList.remove("active");
}

function showSettings() {
    document.getElementById("settingsMaxFail").value = settings.max_fail_count;
    document.getElementById("settingsJimengUrl").value = settings.jimeng_base_url || "";
    document.getElementById("settingsSessionPrefix").value = settings.session_prefix || "hk-";
    document.getElementById("settingsModal").classList.add("active");
}

function closeSettings() {
    document.getElementById("settingsModal").classList.remove("active");
}

function showUpload() {
    document.getElementById("uploadArea").classList.toggle("is-hidden");
}

function renderCalls(isNew = false) {
    const tbody = document.getElementById("callsBody");

    if (calls.length === 0) {
        tbody.innerHTML = `
            <tr class="empty-row">
                <td colspan="8">
                    <div class="empty-state">
                        <div class="icon">📭</div>
                        <p>暂无调用记录</p>
                    </div>
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = calls.map((call, idx) => `
        <tr class="${isNew && idx === 0 ? "new-call" : ""}">
            <td><span class="call-id" title="${escapeHtml(call.id)}">${call.id}</span></td>
            <td><span class="call-time" title="${escapeHtml(call.timestamp)}">${call.timestamp}</span></td>
            <td><span class="call-model" title="${escapeHtml(call.model)}">${call.model}</span></td>
            <td>${call.session_id ? `<span class="session-id" title="${escapeHtml(call.session_email || "")}">${call.session_id}</span>` : '<span class="muted">-</span>'}</td>
            <td><span class="call-prompt" title="${escapeHtml(call.full_prompt || call.prompt)}">${escapeHtml(call.prompt)}</span></td>
            <td><span class="call-status ${call.status}" title="${call.status === "success" ? "成功" : "失败"}">${call.status === "success" ? "✓ 成功" : "✗ 失败"}</span></td>
            <td><span class="call-duration" title="${call.duration}">${call.duration}</span></td>
            <td class="call-result" title="${call.error ? escapeHtml(call.error) : (call.result_url ? "查看生成结果" : "")}">
                ${call.error ? `<span class="error-text">${escapeHtml(call.error)}</span>` : ""}
                ${call.result_url ? `
                    <a href="${call.result_url}" target="_blank" title="点击查看大图">查看图片</a>
                    <br><img class="result-image" src="${call.result_url}" data-image-url="${escapeHtml(call.result_url)}" alt="生成结果">
                ` : (call.status === "success" && !call.error ? "-" : "")}
            </td>
        </tr>
    `).join("");

    tbody.querySelectorAll(".result-image").forEach((img) => {
        img.addEventListener("error", () => {
            img.classList.add("is-hidden");
        }, { once: true });
    });
}

function renderPagination(totalPages) {
    const paginationContainer = document.getElementById("paginationContainer");
    if (!paginationContainer) return;

    if (totalPages <= 1) {
        paginationContainer.innerHTML = "";
        return;
    }

    const parts = [];
    const maxVisible = 5;
    let startPage = Math.max(1, currentPage - Math.floor(maxVisible / 2));
    let endPage = Math.min(totalPages, startPage + maxVisible - 1);

    if (endPage - startPage < maxVisible - 1) {
        startPage = Math.max(1, endPage - maxVisible + 1);
    }

    parts.push(
        `<button class="page-btn" data-action="change-page" data-page="${currentPage - 1}" ${currentPage === 1 ? "disabled" : ""}>上一页</button>`
    );

    if (startPage > 1) {
        parts.push('<button class="page-btn" data-action="change-page" data-page="1">1</button>');
        if (startPage > 2) {
            parts.push('<span class="page-ellipsis">...</span>');
        }
    }

    for (let i = startPage; i <= endPage; i += 1) {
        parts.push(
            `<button class="page-btn ${i === currentPage ? "active" : ""}" data-action="change-page" data-page="${i}">${i}</button>`
        );
    }

    if (endPage < totalPages) {
        if (endPage < totalPages - 1) {
            parts.push('<span class="page-ellipsis">...</span>');
        }
        parts.push(`<button class="page-btn" data-action="change-page" data-page="${totalPages}">${totalPages}</button>`);
    }

    parts.push(
        `<button class="page-btn" data-action="change-page" data-page="${currentPage + 1}" ${currentPage === totalPages ? "disabled" : ""}>下一页</button>`
    );
    parts.push(`<span class="page-info">第 ${currentPage} / ${totalPages} 页</span>`);

    paginationContainer.innerHTML = parts.join("");
}

function renderSessions() {
    const tbody = document.getElementById("sessionsBody");
    const total = sessions.length;
    const disabled = sessions.filter((s) => !s.enabled).length;
    const available = sessions.filter((s) => s.enabled && s.fail_count < settings.max_fail_count).length;

    document.getElementById("sessionTotal").textContent = total;
    document.getElementById("sessionAvailable").textContent = available;
    document.getElementById("sessionDisabled").textContent = disabled;
    document.getElementById("maxFailCount").textContent = settings.max_fail_count;

    if (sessions.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="6" class="empty-cell">
                    暂无Session，点击"导入Session"添加
                </td>
            </tr>
        `;
        renderPagination(0);
        return;
    }

    const totalPages = Math.ceil(sessions.length / pageSize);
    if (currentPage > totalPages) {
        currentPage = totalPages;
    }

    const start = (currentPage - 1) * pageSize;
    const end = start + pageSize;
    const pageSessions = sessions.slice(start, end);

    tbody.innerHTML = pageSessions.map((session) => {
        const failClass = session.fail_count >= settings.max_fail_count ? "danger" : (session.fail_count > 0 ? "warning" : "");
        const statusClass = session.enabled ? "enabled" : "disabled";
        const statusText = session.enabled ? "启用" : "禁用";
        const btnClass = session.enabled ? "disable" : "enable";
        const btnText = session.enabled ? "禁用" : "启用";

        return `
            <tr>
                <td><span class="session-email">${escapeHtml(session.email)}</span></td>
                <td><span class="session-id">${session.session_id.substring(0, 12)}...</span></td>
                <td><span class="session-fail ${failClass}">${session.fail_count} / ${settings.max_fail_count}</span></td>
                <td><span class="session-status ${statusClass}">${statusText}</span></td>
                <td><span class="last-used">${session.last_used || "-"}</span></td>
                <td>
                    <button class="toggle-btn ${btnClass}" data-action="toggle-session" data-session-id="${escapeHtml(session.session_id)}">${btnText}</button>
                </td>
            </tr>
        `;
    }).join("");

    renderPagination(totalPages);
}

function changePage(page) {
    const totalPages = Math.ceil(sessions.length / pageSize);
    if (page < 1 || page > totalPages) return;
    currentPage = page;
    renderSessions();
}

async function importSessions(content) {
    try {
        const response = await fetch(buildHttpUrl("api/sessions/import"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content }),
        });
        const result = await response.json();
        alert(`导入完成！新增: ${result.imported}, 跳过: ${result.skipped}, 总计: ${result.total}`);
        document.getElementById("uploadArea").classList.add("is-hidden");
        document.getElementById("fileInput").value = "";
    } catch (err) {
        alert(`导入失败: ${err.message}`);
    }
}

function handleFileSelect(event) {
    const file = event.target.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (loadEvent) => {
        importSessions(loadEvent.target.result);
    };
    reader.readAsText(file);
}

async function toggleSession(sessionId) {
    try {
        await fetch(buildHttpUrl(`api/sessions/${sessionId}/toggle`), { method: "POST" });
    } catch (err) {
        alert(`操作失败: ${err.message}`);
    }
}

async function enableAllSessions() {
    if (!confirm("确定要解禁所有禁用的账户吗？")) return;

    try {
        await fetch(buildHttpUrl("api/sessions/enable-all"), { method: "POST" });
    } catch (err) {
        alert(`解禁失败：${err.message}`);
    }
}

async function clearSessions() {
    if (!confirm("确定要清空所有Session吗？")) return;

    try {
        await fetch(buildHttpUrl("api/sessions/clear"), { method: "POST" });
    } catch (err) {
        alert(`清空失败: ${err.message}`);
    }
}

async function saveSettings() {
    const maxFail = Number.parseInt(document.getElementById("settingsMaxFail").value, 10);
    const jimengUrl = document.getElementById("settingsJimengUrl").value.trim();
    const sessionPrefix = document.getElementById("settingsSessionPrefix").value;

    if (maxFail < 1) {
        alert("最大失败次数必须大于0");
        return;
    }
    if (!jimengUrl) {
        alert("请输入即梦API服务地址");
        return;
    }

    try {
        await fetch(buildHttpUrl("api/settings"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                max_fail_count: maxFail,
                jimeng_base_url: jimengUrl,
                session_prefix: sessionPrefix,
            }),
        });
        document.getElementById("backendUrl").textContent = jimengUrl;
        closeSettings();
    } catch (err) {
        alert(`保存失败: ${err.message}`);
    }
}

function clearCalls() {
    if (!confirm("确定要清空所有记录吗？")) return;

    fetch(buildHttpUrl("stats/clear"), { method: "POST" }).then(() => {
        calls = [];
        updateStats({ total_calls: 0, successful_calls: 0, failed_calls: 0 });
        renderCalls();
    });
}

async function loadSessions() {
    try {
        const response = await fetch(buildHttpUrl("api/sessions"));
        const data = await response.json();
        sessions = data.sessions || [];
        settings = data.settings || settings;
        renderSessions();
    } catch (err) {
        console.error("Failed to load sessions:", err);
    }
}

function bindStaticEvents() {
    const uploadArea = document.getElementById("uploadArea");
    const fileInput = document.getElementById("fileInput");
    const imageModal = document.getElementById("imageModal");
    const callsBody = document.getElementById("callsBody");
    const sessionsBody = document.getElementById("sessionsBody");
    const paginationContainer = document.getElementById("paginationContainer");

    document.querySelector('[data-action="enable-all-sessions"]').addEventListener("click", enableAllSessions);
    document.querySelector('[data-action="toggle-upload"]').addEventListener("click", showUpload);
    document.querySelector('[data-action="show-settings"]').addEventListener("click", showSettings);
    document.querySelector('[data-action="clear-sessions"]').addEventListener("click", clearSessions);
    document.querySelector('[data-action="close-settings"]').addEventListener("click", closeSettings);
    document.querySelector('[data-action="save-settings"]').addEventListener("click", saveSettings);
    document.querySelector('[data-action="clear-calls"]').addEventListener("click", clearCalls);
    document.querySelector('[data-action="close-modal"]').addEventListener("click", closeModal);

    fileInput.addEventListener("change", handleFileSelect);

    uploadArea.addEventListener("click", (event) => {
        if (event.target === uploadArea) {
            fileInput.click();
        }
    });
    uploadArea.addEventListener("dragover", (event) => {
        event.preventDefault();
        uploadArea.classList.add("dragover");
    });
    uploadArea.addEventListener("dragleave", () => {
        uploadArea.classList.remove("dragover");
    });
    uploadArea.addEventListener("drop", (event) => {
        event.preventDefault();
        uploadArea.classList.remove("dragover");
        const file = event.dataTransfer.files[0];
        if (file && file.name.endsWith(".txt")) {
            const reader = new FileReader();
            reader.onload = (loadEvent) => importSessions(loadEvent.target.result);
            reader.readAsText(file);
        }
    });

    imageModal.addEventListener("click", (event) => {
        if (event.target === imageModal) {
            closeModal();
        }
    });

    callsBody.addEventListener("click", (event) => {
        const image = event.target.closest(".result-image");
        if (!image) return;
        event.preventDefault();
        showModal(image.dataset.imageUrl);
    });

    sessionsBody.addEventListener("click", (event) => {
        const button = event.target.closest('[data-action="toggle-session"]');
        if (!button) return;
        toggleSession(button.dataset.sessionId);
    });

    paginationContainer.addEventListener("click", (event) => {
        const button = event.target.closest('[data-action="change-page"]');
        if (!button || button.disabled) return;
        changePage(Number.parseInt(button.dataset.page, 10));
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            closeModal();
            closeSettings();
        }
    });
}

function connect() {
    ws = new WebSocket(buildWsUrl("ws"));

    ws.onopen = () => {
        document.getElementById("connectionStatus").className = "connection-status connected";
        document.getElementById("statusText").textContent = "已连接";
    };

    ws.onclose = () => {
        document.getElementById("connectionStatus").className = "connection-status disconnected";
        document.getElementById("statusText").textContent = "已断开";
        setTimeout(connect, 3000);
    };

    ws.onerror = () => {
        ws.close();
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);

        if (data.type === "init") {
            document.getElementById("backendUrl").textContent = data.backend_url;
            calls = data.history || [];
            updateStats(data.stats);
            renderCalls();
            if (data.sessions) {
                sessions = data.sessions;
                settings = data.settings || settings;
                renderSessions();
            }
        } else if (data.type === "new_call") {
            calls.unshift(data.record);
            if (calls.length > 100) calls.pop();
            updateStats(data.stats);
            renderCalls(true);
        } else if (data.type === "session_update") {
            sessions = data.sessions || [];
            settings = data.settings || settings;
            renderSessions();
            if (settings.jimeng_base_url) {
                document.getElementById("backendUrl").textContent = settings.jimeng_base_url;
            }
        }
    };
}

bindStaticEvents();
connect();
loadSessions();
