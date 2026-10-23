/* ============================================================================
   Oryn dashboard — client
   Vanilla JS, no build step. Every string that reaches innerHTML is escaped.
   ========================================================================== */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const THEME_KEY = "oryn.theme";
  const VIEW_META = {
    chat: { title: "Chat", subtitle: "Your agent is online and ready." },
    connections: { title: "Connections", subtitle: "Accounts Oryn can act on." },
    tools: { title: "Tools", subtitle: "Capabilities the agent can call." },
    system: { title: "System", subtitle: "Live telemetry from this laptop." },
    settings: { title: "Settings", subtitle: "Model, memory, voice and credentials." },
  };

  const state = {
    view: "chat",
    status: null,
    settings: null,
    tools: null,
    historyLoaded: false,
    chatBusy: false,
    dirty: new Map(),
    fieldErrors: new Map(),
    settingsTab: "ai",
    tool: { q: "", group: "all", scope: "all" },
    toolTimer: null,
    statusTimer: null,
    offline: false,
  };

  /* ---------------------------------------------------------------------- */
  /* Utilities                                                              */
  /* ---------------------------------------------------------------------- */
  const icon = (name, cls = "icon") => `<svg class="${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;

  const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ESCAPES[c]);

  const num = (value, digits = 0) =>
    Number(value || 0).toLocaleString(undefined, { maximumFractionDigits: digits });

  const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

  function formatUptime(seconds) {
    const total = Math.max(0, Math.floor(seconds || 0));
    if (total < 60) return "just started";
    if (total < 3600) return `${Math.floor(total / 60)}m uptime`;
    if (total < 86400) return `${Math.floor(total / 3600)}h ${Math.floor((total % 3600) / 60)}m`;
    return `${Math.floor(total / 86400)}d ${Math.floor((total % 86400) / 3600)}h`;
  }

  function formatPercent(value) {
    const pct = clamp(Number(value) || 0, 0, 100);
    return pct >= 100 ? "100" : pct.toFixed(pct >= 10 ? 0 : 1);
  }

  function capitalise(text) {
    const clean = String(text || "").trim();
    if (!clean) return "there";
    return clean.charAt(0).toUpperCase() + clean.slice(1);
  }

  async function api(path, { method = "GET", body } = {}) {
    const options = { method, headers: {} };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    let response;
    try {
      response = await fetch(path, options);
    } catch (error) {
      throw new Error("Cannot reach the Oryn server. Is the dashboard still running?");
    }
    let data = {};
    try {
      data = await response.json();
    } catch (error) {
      data = {};
    }
    if (!response.ok) {
      const err = new Error(data.error || `Request failed (${response.status})`);
      err.errors = data.errors;
      err.status = response.status;
      throw err;
    }
    return data;
  }

  /* ---------------------------------------------------------------------- */
  /* Theme                                                                  */
  /* ---------------------------------------------------------------------- */
  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch (error) {
      /* private mode: theme just won't persist */
    }
    const meta = $('meta[name="theme-color"]');
    if (meta) meta.content = theme === "light" ? "#f4f6f9" : "#080b10";
  }

  function initTheme() {
    let saved = null;
    try {
      saved = localStorage.getItem(THEME_KEY);
    } catch (error) {
      saved = null;
    }
    applyTheme(saved === "light" ? "light" : "dark");
  }

  /* ---------------------------------------------------------------------- */
  /* Toasts & modals                                                        */
  /* ---------------------------------------------------------------------- */
  function toast(title, type = "info", detail = "") {
    const stack = $("#toast-stack");
    if (!stack) return;
    const iconName = type === "success" ? "check" : type === "error" ? "alert" : type === "warn" ? "alert" : "info";
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    el.innerHTML = `${icon(iconName)}<div><strong>${esc(title)}</strong>${detail ? esc(detail) : ""}</div>`;
    stack.appendChild(el);
    const delay = detail ? 5600 : 3600;
    setTimeout(() => {
      el.classList.add("leaving");
      setTimeout(() => el.remove(), 220);
    }, delay);
  }

  let activeModal = null;

  function closeModal() {
    const root = $("#modal-root");
    if (!root) return;
    root.innerHTML = "";
    if (activeModal && activeModal.keyHandler) {
      document.removeEventListener("keydown", activeModal.keyHandler);
    }
    activeModal = null;
  }

  function openModal({
    title,
    description = "",
    bodyHTML = "",
    confirmLabel = "Confirm",
    confirmVariant = "primary",
    cancelLabel = "Cancel",
    onConfirm = null,
    onMount = null,
    showCancel = true,
  }) {
    const root = $("#modal-root");
    if (!root) return;
    closeModal();
    root.innerHTML = `
      <div class="modal" role="dialog" aria-modal="true" aria-label="${esc(title)}">
        <div class="modal-head">
          <div>
            <h3>${esc(title)}</h3>
            ${description ? `<p>${esc(description)}</p>` : ""}
          </div>
          <button class="icon-button" type="button" data-modal-close aria-label="Close">${icon("x")}</button>
        </div>
        ${bodyHTML ? `<div class="modal-body">${bodyHTML}</div>` : ""}
        <div class="modal-foot">
          ${showCancel ? `<button class="button ghost" type="button" data-modal-close>${esc(cancelLabel)}</button>` : ""}
          ${
            onConfirm
              ? `<button class="button ${confirmVariant}" type="button" data-modal-confirm>${esc(confirmLabel)}</button>`
              : ""
          }
        </div>
      </div>`;
    root.scrollTop = 0;

    const keyHandler = (event) => {
      if (event.key === "Escape") closeModal();
    };
    document.addEventListener("keydown", keyHandler);
    activeModal = { keyHandler };

    root.onclick = (event) => {
      if (event.target === root || event.target.closest("[data-modal-close]")) {
        closeModal();
        return;
      }
      const confirmBtn = event.target.closest("[data-modal-confirm]");
      if (confirmBtn && onConfirm) {
        const run = async () => {
          confirmBtn.disabled = true;
          try {
            const keepOpen = await onConfirm();
            if (keepOpen !== false) closeModal();
          } catch (error) {
            toast("Something went wrong", "error", error.message);
          } finally {
            confirmBtn.disabled = false;
          }
        };
        run();
      }
    };

    if (onMount) onMount(root);
    const focusTarget = root.querySelector("input, textarea, select") || root.querySelector("[data-modal-confirm]");
    if (focusTarget) setTimeout(() => focusTarget.focus(), 40);
  }

  function confirmDialog({ title, message, confirmLabel = "Confirm", danger = false }) {
    return new Promise((resolve) => {
      let settled = false;
      const finish = (value) => {
        if (settled) return;
        settled = true;
        resolve(value);
      };
      openModal({
        title,
        confirmLabel,
        confirmVariant: danger ? "danger" : "primary",
        bodyHTML: `<p>${esc(message)}</p>`,
        onConfirm: async () => {
          finish(true);
        },
      });
      const root = $("#modal-root");
      const observer = new MutationObserver(() => {
        if (!root.innerHTML.trim()) {
          observer.disconnect();
          finish(false);
        }
      });
      observer.observe(root, { childList: true, subtree: true });
      setTimeout(() => {
        if (settled) observer.disconnect();
      }, 120000);
    });
  }

  /* ---------------------------------------------------------------------- */
  /* Navigation                                                             */
  /* ---------------------------------------------------------------------- */
  function switchView(view) {
    if (!VIEW_META[view]) view = "chat";
    state.view = view;
    $$(".nav-item").forEach((button) => {
      const active = button.dataset.view === view;
      button.classList.toggle("active", active);
      if (active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    $$(".view").forEach((panel) => panel.classList.toggle("active", panel.dataset.viewPanel === view));

    const meta = VIEW_META[view];
    $("#view-title").textContent = meta.title;
    $("#view-subtitle").textContent = meta.subtitle;
    renderViewActions();

    if (view === "settings") {
      if (!state.settings) loadSettings();
      else renderSettingsPanel();
    }
    if (view === "tools" && !state.tools) loadTools();
    if (view === "connections") renderConnections();
    if (view === "system") renderSystem();
    if (view === "chat") {
      requestAnimationFrame(() => {
        const scroller = $("#chat-scroll");
        if (scroller) scroller.scrollTop = scroller.scrollHeight;
      });
    }
  }

  function renderViewActions() {
    const host = $("#view-actions");
    if (!host) return;
    if (state.view === "chat") {
      host.innerHTML = `<button class="button ghost" type="button" data-action="clear-chat" title="Clear conversation">${icon("trash")}<span>Clear</span></button>`;
    } else {
      host.innerHTML = "";
    }
  }

  /* ---------------------------------------------------------------------- */
  /* Status                                                                 */
  /* ---------------------------------------------------------------------- */
  function applyStatus(data) {
    state.status = data;
    const app = data.app || {};
    const ai = data.ai || {};

    const modelEl = $("#engine-model");
    if (modelEl) modelEl.textContent = ai.model || "no model";
    const providerEl = $("#engine-provider");
    if (providerEl) providerEl.textContent = ai.provider || "no provider";
    const versionEl = $("#rail-version");
    if (versionEl) versionEl.textContent = app.version || "—";
    const uptimeEl = $("#rail-uptime");
    if (uptimeEl) uptimeEl.textContent = formatUptime(app.uptime_sec);

    const engineState = $("#engine-state");
    if (engineState) {
      engineState.innerHTML = `<span class="dot"></span>${ai.available && ai.available.length ? "online" : "no key"}`;
      engineState.style.color = ai.available && ai.available.length ? "" : "var(--warn)";
    }

    if (state.offline) {
      state.offline = false;
      toast("Back online", "success", "Oryn reconnected to the dashboard server.");
    }
  }

  async function refreshStatus() {
    try {
      const data = await api("/api/status");
      applyStatus(data);
      if (state.view === "system") renderSystem();
      if (state.view === "connections") renderConnections();
    } catch (error) {
      if (!state.offline) {
        state.offline = true;
        const engineState = $("#engine-state");
        if (engineState) {
          engineState.innerHTML = `<span class="dot"></span>offline`;
          engineState.style.color = "var(--danger)";
        }
      }
    }
  }

  /* ---------------------------------------------------------------------- */
  /* Chat                                                                   */
  /* ---------------------------------------------------------------------- */
  function greeting() {
    const hour = new Date().getHours();
    if (hour < 5) return "Working late";
    if (hour < 12) return "Good morning";
    if (hour < 18) return "Good afternoon";
    return "Good evening";
  }

  const SUGGESTIONS = [
    {
      icon: "cpu",
      title: "What's using my memory?",
      desc: "Inspect the top processes and what to close",
      prompt: "Check my live system status and tell me what is using the most memory.",
    },
    {
      icon: "clock",
      title: "Set a timer",
      desc: "Start a ten minute countdown",
      prompt: "Set a timer for 10 minutes and tell me when it ends.",
    },
    {
      icon: "folder",
      title: "Find a Drive file",
      desc: "Search recent files in your Drive",
      prompt: "List my recent Google Drive files.",
    },
    {
      icon: "globe",
      title: "Search the web",
      desc: "Look something up in real time",
      prompt: "Search the web for the latest AI agent news and summarise it.",
    },
  ];

  function emptyStateHTML() {
    const name = capitalise(state.status?.app?.user);
    const suggestions = SUGGESTIONS.map(
      (item) => `
        <button class="suggestion" type="button" data-prompt="${esc(item.prompt)}">
          <span class="suggestion-icon">${icon(item.icon)}</span>
          <span>
            <span class="suggestion-title">${esc(item.title)}</span>
            <span class="suggestion-desc">${esc(item.desc)}</span>
          </span>
        </button>`
    ).join("");
    return `
      <div class="empty-state">
        <span class="eyebrow"><span class="dot"></span>Agent ready</span>
        <h2>${esc(greeting())}, ${esc(name)}.</h2>
        <p class="greeting-sub">I can read your system, run commands, search the web and work across your connected accounts. What should I take care of?</p>
        <div class="suggestion-grid">${suggestions}</div>
      </div>`;
  }

  function showEmptyState() {
    const thread = $("#chat-thread");
    if (thread) thread.innerHTML = emptyStateHTML();
  }

  function removeEmptyState() {
    const empty = $(".empty-state", $("#chat-thread"));
    if (empty) empty.remove();
  }

  function renderMarkdown(text) {
    const escaped = esc(text);
    let html = "";
    let last = 0;
    const fence = /```([\w+.-]*)\n([\s\S]*?)```/g;
    let match;
    while ((match = fence.exec(escaped))) {
      html += renderInline(escaped.slice(last, match.index));
      html += `<pre><code>${match[2].replace(/\n$/, "")}</code></pre>`;
      last = fence.lastIndex;
    }
    html += renderInline(escaped.slice(last));
    return html;
  }

  function renderInline(segment) {
    return segment
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`\n]+)`/g, "<code>$1</code>")
      .replace(/^[-*] /gm, "• ")
      .replace(
        /(https?:\/\/[^\s<"]+)/g,
        '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>'
      );
  }

  function scrollChat() {
    const scroller = $("#chat-scroll");
    if (!scroller) return;
    requestAnimationFrame(() => {
      scroller.scrollTop = scroller.scrollHeight;
    });
  }

  function addMessage(role, text, actions = [], meta = null) {
    const thread = $("#chat-thread");
    if (!thread) return;
    removeEmptyState();

    const msg = document.createElement("div");
    msg.className = `msg ${role}`;

    if (role === "user") {
      msg.innerHTML = `
        <div class="msg-body"><div class="bubble"></div></div>
        <div class="avatar user" aria-hidden="true">${esc(capitalise(state.status?.app?.user).charAt(0))}</div>`;
      $(".bubble", msg).textContent = text;
    } else {
      msg.innerHTML = `
        <div class="avatar oryn" aria-hidden="true"><img src="/assets/06-app-icon.svg" alt="Oryn" width="22" height="22" style="border-radius: 5px; display: block;"></div>
        <div class="msg-body"></div>`;
      const body = $(".msg-body", msg);

      if (actions && actions.length) {
        const runs = document.createElement("div");
        runs.className = "tool-runs";
        actions.forEach((action) => {
          const ok = action.ok !== false;
          runs.innerHTML += `
            <details class="tool-run" data-ok="${ok}" ${ok ? "" : "open"}>
              <summary>
                ${icon(ok ? "check" : "alert")}
                <span class="tool-name">${esc(action.label || "tool")}</span>
                <span class="tool-state">${ok ? "completed" : "failed"}</span>
              </summary>
              <pre>${esc(action.result || "No output")}</pre>
            </details>`;
        });
        body.appendChild(runs);
      }

      const textEl = document.createElement("div");
      textEl.className = "msg-text";
      textEl.innerHTML = renderMarkdown(text || "");
      body.appendChild(textEl);

      if (meta && meta.provider) {
        const metaEl = document.createElement("div");
        metaEl.className = "msg-meta";
        const parts = [
          meta.provider,
          meta.model,
          meta.latency_ms ? `${Math.round(meta.latency_ms)} ms` : "",
          meta.tools_run ? `${meta.tools_run} tool${meta.tools_run === 1 ? "" : "s"}` : "",
        ].filter(Boolean);
        metaEl.innerHTML = parts.map((part) => `<span>${esc(part)}</span>`).join('<span class="sep">·</span>');
        body.appendChild(metaEl);
      }
    }

    thread.appendChild(msg);
    scrollChat();
  }

  function addTyping() {
    const thread = $("#chat-thread");
    if (!thread || $("#typing")) return;
    const el = document.createElement("div");
    el.className = "msg assistant";
    el.id = "typing";
    el.innerHTML = `
      <div class="avatar oryn" aria-hidden="true"><img src="/assets/06-app-icon.svg" alt="Oryn" width="22" height="22" style="border-radius: 5px; display: block;"></div>
      <div class="typing"><span class="typing-dots"><span></span><span></span><span></span></span></div>`;
    thread.appendChild(el);
    scrollChat();
  }

  function removeTyping() {
    const el = $("#typing");
    if (el) el.remove();
  }

  function setComposerBusy(busy) {
    state.chatBusy = busy;
    const send = $("#send-btn");
    if (send) send.disabled = busy;
    const input = $("#composer-input");
    if (input) input.setAttribute("aria-busy", busy ? "true" : "false");
    const hint = $("#composer-hint");
    if (hint) hint.textContent = busy ? "Oryn is working…" : "Enter to send · Shift+Enter for a new line";
  }

  function autoGrow(textarea) {
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(200, textarea.scrollHeight)}px`;
  }

  async function sendMessage(rawText) {
    const input = $("#composer-input");
    const message = String(rawText ?? input?.value ?? "").trim();
    if (!message || state.chatBusy) return;

    addMessage("user", message);
    if (input) {
      input.value = "";
      autoGrow(input);
    }
    setComposerBusy(true);
    addTyping();

    try {
      const data = await api("/api/chat", { method: "POST", body: { message } });
      removeTyping();
      addMessage("assistant", data.reply || "I don't have a reply for that.", data.actions || [], data.meta);
      state.status = state.status || {};
      if (state.status.ai) state.status.ai.last = data.meta;
      refreshStatus();
    } catch (error) {
      removeTyping();
      addMessage("assistant", `I couldn't complete that request.\n\n${error.message}`);
      toast("Request failed", "error", error.message);
    } finally {
      setComposerBusy(false);
    }
  }

  async function loadHistory() {
    try {
      const data = await api("/api/history");
      const history = data.history || [];
      state.historyLoaded = true;
      if (!history.length) {
        showEmptyState();
        return;
      }
      const thread = $("#chat-thread");
      thread.innerHTML = "";
      history.forEach((entry) => addMessage(entry.role === "user" ? "user" : "assistant", entry.content));
      scrollChat();
    } catch (error) {
      state.historyLoaded = true;
      showEmptyState();
    }
  }

  async function clearConversation() {
    const ok = await confirmDialog({
      title: "Clear this conversation?",
      message: "The dashboard transcript is removed. Long-term memory and files are untouched.",
      confirmLabel: "Clear conversation",
      danger: true,
    });
    if (!ok) return;
    try {
      await api("/api/clear", { method: "POST" });
      const thread = $("#chat-thread");
      if (thread) thread.innerHTML = "";
      showEmptyState();
      toast("Conversation cleared", "success");
      refreshStatus();
    } catch (error) {
      toast("Could not clear", "error", error.message);
    }
  }

  function initVoice() {
    const button = $("#mic-btn");
    if (!button) return;
    button.addEventListener("click", () => {
      const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SpeechRecognition) {
        toast("Voice input unavailable", "warn", "This browser does not expose the Web Speech API.");
        return;
      }
      const recognition = new SpeechRecognition();
      recognition.lang = navigator.language || "en-US";
      recognition.interimResults = false;
      recognition.maxAlternatives = 1;

      recognition.onstart = () => {
        button.style.color = "var(--danger)";
        $("#composer-hint").textContent = "Listening…";
      };
      recognition.onresult = (event) => {
        const transcript = event.results?.[0]?.[0]?.transcript || "";
        const input = $("#composer-input");
        if (input) input.value = transcript;
        if (transcript) sendMessage(transcript);
      };
      recognition.onerror = (event) => {
        toast("Voice input failed", "warn", event.error || "Unknown speech error");
      };
      recognition.onend = () => {
        button.style.color = "";
        const busy = state.chatBusy;
        $("#composer-hint").textContent = busy
          ? "Oryn is working…"
          : "Enter to send · Shift+Enter for a new line";
      };
      recognition.start();
    });
  }

  /* ---------------------------------------------------------------------- */
  /* Connections                                                            */
  /* ---------------------------------------------------------------------- */
  function statusPill(kind) {
    const map = {
      connected_permanent: ["ok", "Connected"],
      connected_temporary: ["warn", "Temporary"],
      connected: ["ok", "Connected"],
      configured: ["ok", "Configured"],
      active: ["ok", "Active"],
      disconnected: ["muted", "Not connected"],
      unconfigured: ["muted", "Not configured"],
      missing: ["err", "No key"],
    };
    const [tone, label] = map[kind] || ["muted", "Unknown"];
    return `<span class="pill ${tone}"><span class="dot"></span>${esc(label)}</span>`;
  }

  function connectorCard({ name, sub, iconName, tile, detail, pill, actions }) {
    const buttons = actions
      .map(
        (action) =>
          `<button class="button ${action.variant || "ghost"} small" type="button" data-connector-action="${esc(
            action.action
          )}">${action.icon ? icon(action.icon) : ""}${esc(action.label)}</button>`
      )
      .join("");
    return `
      <article class="connector">
        <div class="connector-top">
          <span class="tile ${tile}">${icon(iconName)}</span>
          <div>
            <div class="connector-name">${esc(name)}</div>
            <div class="connector-sub">${esc(sub)}</div>
          </div>
          ${pill}
        </div>
        <p class="connector-detail">${esc(detail)}</p>
        <div class="connector-actions">${buttons}</div>
      </article>`;
  }

  function renderConnections() {
    const host = $("#connector-grid");
    if (!host) return;
    if (!state.status) {
      host.innerHTML = `
        <div class="card"><div class="skeleton" style="height:120px"></div></div>
        <div class="card"><div class="skeleton" style="height:120px"></div></div>`;
      return;
    }

    const auth = state.status.auth || {};
    const app = state.status.app || {};
    const ai = state.status.ai || {};
    const cards = [];

    const drive = auth.drive || {};
    cards.push(
      connectorCard({
        name: "Google Drive",
        sub: "Files, docs and folders",
        iconName: "folder",
        tile: "drive",
        detail: drive.detail || "Sign in once to give Oryn Drive access.",
        pill: statusPill(drive.status),
        actions: [
          { action: "drive-connect", label: drive.status === "disconnected" ? "Connect" : "Reconnect", variant: "primary" },
          { action: "drive-test", label: "Test", variant: "ghost" },
          ...(drive.status && drive.status !== "disconnected"
            ? [{ action: "drive-disconnect", label: "Disconnect", variant: "danger-ghost" }]
            : []),
        ],
      })
    );

    const notion = auth.notion || {};
    cards.push(
      connectorCard({
        name: "Notion",
        sub: "Pages and databases",
        iconName: "doc",
        tile: "notion",
        detail: notion.detail || "Connect a workspace or paste an integration token.",
        pill: statusPill(notion.status),
        actions: [
          { action: "notion-connect", label: notion.status === "connected" ? "Reconnect" : "Connect", variant: "primary" },
          { action: "notion-token", label: "Paste token", variant: "ghost" },
          ...(notion.status === "connected"
            ? [{ action: "notion-disconnect", label: "Disconnect", variant: "danger-ghost" }]
            : []),
        ],
      })
    );

    const smtp = auth.smtp || {};
    cards.push(
      connectorCard({
        name: "Email (SMTP)",
        sub: "Send mail from Oryn",
        iconName: "send",
        tile: "smtp",
        detail: smtp.detail || "Add SMTP details in Settings → Credentials.",
        pill: statusPill(smtp.status),
        actions: [
          ...(smtp.status === "configured"
            ? [{ action: "smtp-test", label: "Send test", variant: "ghost" }]
            : []),
          { action: "smtp-config", label: smtp.status === "configured" ? "Configure" : "Set up", variant: smtp.status === "configured" ? "ghost" : "primary" },
        ],
      })
    );

    const gemini = auth.gemini || {};
    const ready = (ai.available || []).length;
    cards.push(
      connectorCard({
        name: "AI engine",
        sub: `${ai.provider || "gemini"} · ${app.tools || 0} tools`,
        iconName: "spark",
        tile: "gemini",
        detail: ai.model
          ? `Running ${ai.model} with ${ready} provider${ready === 1 ? "" : "s"} ready.`
          : gemini.detail || "Configure a provider key to start chatting.",
        pill: statusPill(gemini.status),
        actions: [{ action: "gemini-config", label: "Model settings", variant: "ghost", icon: "sliders" }],
      })
    );

    host.innerHTML = cards.join("");
  }

  async function runConnectorAction(action) {
    const waitAndRefresh = (delay = 4000) => setTimeout(refreshStatus, delay);

    if (action === "drive-connect") {
      try {
        const data = await api("/api/auth/drive", { method: "POST" });
        toast("Browser opened", "info", data.message || "Approve access in your browser.");
        waitAndRefresh(5000);
      } catch (error) {
        toast("Could not start Drive login", "error", error.message);
      }
      return;
    }

    if (action === "drive-test") {
      try {
        const data = await api("/api/test/drive", { method: "POST" });
        if (data.ok) toast("Drive is working", "success", "Recent files were listed successfully.");
        else {
          openModal({
            title: "Drive test failed",
            description: "Oryn could not read your Drive with the stored credentials.",
            bodyHTML: `<div class="tool-run" data-ok="false"><pre>${esc(data.details || "No details returned")}</pre></div>`,
            confirmLabel: "Close",
            showCancel: false,
          });
        }
      } catch (error) {
        toast("Drive test failed", "error", error.message);
      }
      return;
    }

    if (action === "drive-disconnect") {
      const ok = await confirmDialog({
        title: "Disconnect Google Drive?",
        message: "Oryn will forget the stored Drive tokens. You can reconnect at any time.",
        confirmLabel: "Disconnect",
        danger: true,
      });
      if (!ok) return;
      try {
        await api("/api/auth/disconnect", { method: "POST", body: { service: "drive" } });
        toast("Drive disconnected", "success");
        refreshStatus();
      } catch (error) {
        toast("Could not disconnect", "error", error.message);
      }
      return;
    }

    if (action === "notion-connect") {
      try {
        const data = await api("/api/auth/notion", { method: "POST" });
        if (data.status === "missing_credentials") {
          openNotionTokenModal(data.message);
        } else {
          toast("Browser opened", "info", data.message || "Approve access in your browser.");
          waitAndRefresh(5000);
        }
      } catch (error) {
        toast("Could not start Notion login", "error", error.message);
      }
      return;
    }

    if (action === "notion-token") {
      openNotionTokenModal();
      return;
    }

    if (action === "notion-disconnect") {
      const ok = await confirmDialog({
        title: "Disconnect Notion?",
        message: "Oryn will forget the stored Notion token and workspace details.",
        confirmLabel: "Disconnect",
        danger: true,
      });
      if (!ok) return;
      try {
        await api("/api/auth/disconnect", { method: "POST", body: { service: "notion" } });
        toast("Notion disconnected", "success");
        refreshStatus();
      } catch (error) {
        toast("Could not disconnect", "error", error.message);
      }
      return;
    }

    if (action === "smtp-test") {
      try {
        const data = await api("/api/test/smtp", { method: "POST" });
        if (data.ok) toast("Test email sent", "success", "Check your inbox.");
        else {
          openModal({
            title: "Test email failed",
            description: "The SMTP server rejected the message.",
            bodyHTML: `<div class="tool-run" data-ok="false"><pre>${esc(data.details || "No details returned")}</pre></div>`,
            confirmLabel: "Close",
            showCancel: false,
          });
        }
      } catch (error) {
        toast("Test email failed", "error", error.message);
      }
      return;
    }

    if (action === "smtp-config" || action === "gemini-config") {
      switchView("settings");
      selectSettingsTab(action === "smtp-config" ? "credentials" : "ai");
    }
  }

  function openNotionTokenModal(message = "") {
    openModal({
      title: "Connect Notion with a token",
      description:
        message || "Paste an internal integration token. Oryn stores it in .env and never sends it anywhere else.",
      bodyHTML: `
        <label class="field">
          <span class="field-label">Integration token</span>
          <input id="notion-token" type="password" placeholder="secret_… or ntn_…" autocomplete="off" spellcheck="false">
          <div class="modal-note">Create an internal integration at notion.so/my-integrations, then share the pages you want Oryn to read with it.</div>
        </label>`,
      confirmLabel: "Save token",
      onConfirm: async () => {
        const input = $("#notion-token");
        const value = input ? input.value.trim() : "";
        if (!value) {
          if (input) {
            input.classList.add("invalid");
            input.focus();
          }
          return false;
        }
        await api("/api/settings/secret", { method: "POST", body: { key: "NOTION_TOKEN", value } });
        toast("Notion connected", "success", "Token saved to .env.");
        if (state.settings) {
          try {
            state.settings = await api("/api/settings");
            if (state.view === "settings") renderSettingsPanel();
          } catch (error) {
            /* the token still saved */
          }
        }
        refreshStatus();
        return true;
      },
    });
  }

  /* ---------------------------------------------------------------------- */
  /* Tools                                                                  */
  /* ---------------------------------------------------------------------- */
  async function loadTools() {
    try {
      const data = await api("/api/tools");
      state.tools = data;
      renderToolStats();
      populateToolGroups();
      renderToolRows();
      const native = $("#native-pill");
      if (native) {
        native.innerHTML = `<span class="dot"></span>${num((data.native || []).length)} native in chat`;
      }
      const source = $("#tools-source");
      if (source && data.source) source.textContent = data.source;
    } catch (error) {
      const count = $("#tool-count");
      if (count) count.textContent = "Could not load the catalog.";
      const rows = $("#tool-rows");
      if (rows) rows.innerHTML = `<div class="empty-row"><strong>Catalog unavailable</strong>${esc(error.message)}</div>`;
    }
  }

  function renderToolStats() {
    const data = state.tools || {};
    const totals = data.totals || {};
    const tools = data.tools || [];
    const groups = Object.keys(totals.byGroup || {});
    const core = totals.core ?? tools.filter((tool) => !tool.deferred).length;
    const deferred = totals.deferred ?? tools.length - core;
    const host = $("#tool-stats");
    if (!host) return;
    const stats = [
      ["Total tools", totals.grandTotal ?? tools.length, "Available to the agent"],
      ["Core", core, "Always in context"],
      ["Deferred", deferred, "Loaded via tool search"],
      ["Groups", groups.length || "—", "Catalogs and integrations"],
    ];
    host.innerHTML = stats
      .map(
        ([label, value, sub]) => `
        <div class="stat">
          <div class="stat-value">${esc(value)}</div>
          <div class="stat-label">${esc(label)}</div>
          <div class="stat-sub">${esc(sub)}</div>
        </div>`
      )
      .join("");
  }

  function populateToolGroups() {
    const select = $("#tool-group");
    if (!select || !state.tools) return;
    const totals = state.tools.totals || {};
    const groups = Object.keys(totals.byGroup || {}).sort((a, b) => a.localeCompare(b));
    if (!groups.length) {
      const seen = new Set(state.tools.tools.map((tool) => tool.group));
      groups.push(...Array.from(seen).sort());
    }
    select.innerHTML =
      `<option value="all">All groups</option>` +
      groups.map((group) => `<option value="${esc(group)}">${esc(group)}</option>`).join("");
    select.value = state.tool.group;
  }

  function filteredTools() {
    if (!state.tools) return [];
    const query = state.tool.q.trim().toLowerCase();
    return (state.tools.tools || []).filter((tool) => {
      if (state.tool.scope === "core" && tool.deferred) return false;
      if (state.tool.scope === "deferred" && !tool.deferred) return false;
      if (state.tool.group !== "all" && tool.group !== state.tool.group) return false;
      if (!query) return true;
      return (
        String(tool.name || "").toLowerCase().includes(query) ||
        String(tool.invocation_name || "").toLowerCase().includes(query) ||
        String(tool.group || "").toLowerCase().includes(query)
      );
    });
  }

  function renderToolRows() {
    const host = $("#tool-rows");
    if (!host) return;
    const tools = filteredTools();
    if (!tools.length) {
      host.innerHTML = `<div class="empty-row"><strong>No tools match</strong>Try a different search or clear the filters.</div>`;
    } else {
      const fragment = document.createDocumentFragment();
      tools.forEach((tool) => {
        const row = document.createElement("div");
        row.className = "tool-row";
        row.innerHTML = `
          <span class="tool-index">${String(tool.index ?? "").padStart(3, "0")}</span>
          <span class="tool-name" title="${esc(tool.invocation_name || tool.name)}">${esc(tool.name)}</span>
          <span class="tool-group">${esc(tool.group || "—")}</span>
          <span class="badge ${tool.deferred ? "" : "core"}">${tool.deferred ? "Deferred" : "Core"}</span>`;
        fragment.appendChild(row);
      });
      host.innerHTML = "";
      host.appendChild(fragment);
    }
    const count = $("#tool-count");
    if (count) {
      const total = (state.tools?.tools || []).length;
      count.innerHTML = `<span>Showing ${num(tools.length)} of ${num(total)} tools</span><span>${
        state.tool.scope === "all" ? "All scopes" : state.tool.scope === "core" ? "Core only" : "Deferred only"
      }</span>`;
    }
  }

  /* ---------------------------------------------------------------------- */
  /* System                                                                 */
  /* ---------------------------------------------------------------------- */
  const GAUGE_CIRCUMFERENCE = 2 * Math.PI * 40;

  function ensureGauges() {
    const host = $("#gauge-grid");
    if (!host || host.dataset.ready === "1") return;
    host.dataset.ready = "1";
    host.innerHTML = [
      { key: "cpu", label: "Processor", detail: "CPU load across all cores" },
      { key: "ram", label: "Memory", detail: "Physical memory in use" },
      { key: "disk", label: "Storage", detail: "Primary volume usage" },
    ]
      .map(
        (gauge) => `
        <div class="gauge" data-gauge="${gauge.key}">
          <div class="gauge-ring">
            <svg viewBox="0 0 96 96" aria-hidden="true">
              <circle class="gauge-track" cx="48" cy="48" r="40"/>
              <circle class="gauge-fill" cx="48" cy="48" r="40"
                stroke-dasharray="${GAUGE_CIRCUMFERENCE.toFixed(2)}"
                stroke-dashoffset="${GAUGE_CIRCUMFERENCE.toFixed(2)}"/>
            </svg>
            <div class="gauge-value"><span data-gauge-value>0</span><small>%</small></div>
          </div>
          <div class="gauge-info">
            <div class="gauge-label">${gauge.label}</div>
            <div class="gauge-detail" data-gauge-detail>${gauge.detail}</div>
          </div>
        </div>`
      )
      .join("");
  }

  function setGauge(key, percent, detail) {
    const card = $(`[data-gauge="${key}"]`);
    if (!card) return;
    const pct = clamp(Number(percent) || 0, 0, 100);
    const fill = $(".gauge-fill", card);
    if (fill) fill.style.strokeDashoffset = String(GAUGE_CIRCUMFERENCE * (1 - pct / 100));
    const value = $("[data-gauge-value]", card);
    if (value) value.textContent = formatPercent(pct);
    const detailEl = $("[data-gauge-detail]", card);
    if (detailEl) detailEl.textContent = detail;
    card.classList.toggle("warn", pct >= 70 && pct < 88);
    card.classList.toggle("crit", pct >= 88);
  }

  function renderSystem() {
    ensureGauges();
    const system = state.status?.system || {};
    const app = state.status?.app || {};
    const ai = state.status?.ai || {};
    const processes = system.processes || [];
    const hasTelemetry = Number(system.ram_total_mb) > 0 || processes.length > 0;

    setGauge(
      "cpu",
      system.cpu_usage,
      hasTelemetry ? `${num(system.cpu_usage, 1)}% load now` : "No telemetry bridge on this platform"
    );
    setGauge(
      "ram",
      system.ram_percent,
      hasTelemetry
        ? `${num(system.ram_used_mb)} MB of ${num(system.ram_total_mb)} MB`
        : "Waiting for sysinfo"
    );
    setGauge(
      "disk",
      system.disk_percent,
      hasTelemetry
        ? `${num(system.disk_used_gb, 1)} GB of ${num(system.disk_total_gb, 1)} GB`
        : "Waiting for sysinfo"
    );

    const list = $("#process-list");
    if (list) {
      if (!processes.length) {
        list.innerHTML = `<div class="empty-row"><strong>No process data</strong>${
          hasTelemetry ? "Nothing reported by the telemetry bridge." : "The native sysinfo bridge is not available here."
        }</div>`;
      } else {
        const maxRam = Math.max(...processes.map((proc) => Number(proc.ram_mb) || 0), 1);
        list.innerHTML = processes
          .map((proc) => {
            const ram = Number(proc.ram_mb) || 0;
            return `
              <div class="process-row">
                <span class="process-name" title="${esc(proc.name)}">${esc(proc.name || "unknown")}</span>
                <span class="process-bar"><span style="width:${(ram / maxRam) * 100}%"></span></span>
                <span class="process-mb">${num(ram)} MB</span>
              </div>`;
          })
          .join("");
      }
    }
    const processCount = $("#process-count");
    if (processCount) processCount.textContent = processes.length ? `${processes.length} processes` : "no data";

    const runtime = $("#runtime-kv");
    if (runtime) {
      runtime.innerHTML = [
        ["Version", app.version || "—"],
        ["Python", app.python || "—"],
        ["Platform", app.platform || "—"],
        ["Uptime", formatUptime(app.uptime_sec)],
        ["Dashboard", `localhost:${app.port || location.port || 4242}`],
      ]
        .map(
          ([key, value]) => `<div class="kv-row"><dt>${esc(key)}</dt><dd>${esc(value)}</dd></div>`
        )
        .join("");
    }

    const agent = $("#agent-kv");
    if (agent) {
      const last = ai.last || {};
      agent.innerHTML = [
        ["Provider", ai.provider || "—"],
        ["Model", ai.model || "—"],
        ["Messages", num(app.messages)],
        ["Tool catalog", num(app.tools)],
        ["Native tools", num(app.native_tools)],
        ["Last latency", last.latency_ms ? `${Math.round(last.latency_ms)} ms` : "—"],
      ]
        .map(
          ([key, value]) => `<div class="kv-row"><dt>${esc(key)}</dt><dd>${esc(value)}</dd></div>`
        )
        .join("");
    }
  }

  /* ---------------------------------------------------------------------- */
  /* Settings                                                               */
  /* ---------------------------------------------------------------------- */
  function currentValue(path) {
    if (state.dirty.has(path)) return state.dirty.get(path);
    return state.settings?.values?.[path];
  }

  function markDirty(path, value) {
    const original = state.settings?.values?.[path];
    const same = JSON.stringify(original ?? null) === JSON.stringify(value ?? null);
    if (same) state.dirty.delete(path);
    else state.dirty.set(path, value);
    state.fieldErrors.delete(path);
    updateSavebar();
  }

  function updateSavebar() {
    const bar = $("#savebar");
    if (!bar) return;
    const count = state.dirty.size;
    bar.hidden = count === 0;
    $("#savebar-text").textContent =
      count === 1 ? "1 unsaved change" : `${count} unsaved changes`;
    const save = $("#save-btn");
    if (save) save.disabled = count === 0;
  }

  function errorFor(path) {
    return state.fieldErrors.get(path);
  }

  function fieldErrorHTML(path) {
    const message = errorFor(path);
    return message ? `<div class="field-error">${esc(message)}</div>` : "";
  }

  function fieldHTML(field) {
    const path = field.path;
    const value = currentValue(path);
    const id = `set-${path.replace(/\./g, "-")}`;
    const invalid = errorFor(path) ? "invalid" : "";
    const help = field.help ? `<div class="field-help">${esc(field.help)}</div>` : "";
    const label = `<label for="${id}">${esc(field.label)}</label>`;

    if (field.type === "boolean") {
      return `
        <div class="field span-2">
          <div class="switch-field">
            <div class="switch-copy">
              <div class="switch-label">${esc(field.label)}</div>
              ${field.help ? `<div class="switch-help">${esc(field.help)}</div>` : ""}
            </div>
            <label class="switch">
              <input type="checkbox" id="${id}" data-path="${path}" ${value ? "checked" : ""}>
              <span class="track"></span><span class="thumb"></span>
            </label>
          </div>
          ${fieldErrorHTML(path)}
        </div>`;
    }

    if (field.type === "range") {
      const display = Number(value ?? 0).toFixed(2);
      return `
        <div class="field span-2">
          <div class="field-label">${esc(field.label)}</div>
          <div class="range-row">
            <input type="range" id="${id}" data-path="${path}" min="${field.min}" max="${field.max}" step="${field.step}"
              value="${value ?? field.min}" ${invalid}>
            <span class="range-value" data-range-value="${path}">${display}</span>
          </div>
          ${help}
          ${fieldErrorHTML(path)}
        </div>`;
    }

    if (field.type === "select") {
      const options = (field.options || [])
        .map(
          (option) =>
            `<option value="${esc(option)}" ${String(value ?? "") === String(option) ? "selected" : ""}>${
              option === "" ? "Inherit primary" : esc(option)
            }</option>`
        )
        .join("");
      return `
        <div class="field">
          ${label}
          <div class="select-wrap">
            <select id="${id}" data-path="${path}" class="${invalid}">${options}</select>
            ${icon("chevron")}
          </div>
          ${help}
          ${fieldErrorHTML(path)}
        </div>`;
    }

    if (field.type === "number") {
      const suffix = field.suffix ? `<span>${esc(field.suffix)}</span>` : "";
      return `
        <div class="field">
          ${label}
          <div class="${field.suffix ? "input-suffix" : ""}">
            <input type="number" id="${id}" data-path="${path}" value="${value ?? ""}"
              min="${field.min ?? ""}" max="${field.max ?? ""}" step="${field.step ?? 1}" class="${invalid}">
            ${suffix}
          </div>
          ${help}
          ${fieldErrorHTML(path)}
        </div>`;
    }

    if (field.type === "fallback") {
      return `
        <div class="field span-2">
          <div class="field-label">${esc(field.label)}</div>
          <div class="fallback-editor" id="fallback-editor"></div>
          ${help}
          ${fieldErrorHTML(path)}
        </div>`;
    }

    return `
      <div class="field">
        ${label}
        <input type="text" id="${id}" data-path="${path}" value="${esc(value ?? "")}"
          placeholder="${esc(field.placeholder || "")}" class="${field.mono ? "mono" : ""} ${invalid}">
        ${help}
        ${fieldErrorHTML(path)}
      </div>`;
  }

  function panelHead(iconName, title, description) {
    return `
      <div class="panel-head">
        <span class="panel-icon">${icon(iconName)}</span>
        <div>
          <h2>${esc(title)}</h2>
          <p>${esc(description || "")}</p>
        </div>
      </div>`;
  }

  function renderSettingsNav() {
    const host = $("#settings-nav");
    if (!host || !state.settings) return;
    const items = state.settings.schema.map((section) => ({
      id: section.id,
      label: section.label,
      icon: section.icon,
    }));
    const html =
      items
        .map(
          (item) => `
          <button type="button" data-settings-tab="${esc(item.id)}" class="${
            state.settingsTab === item.id ? "active" : ""
          }">${icon(item.icon)}<span>${esc(item.label)}</span></button>`
        )
        .join("") +
      `<div class="nav-divider"></div>
       <button type="button" data-settings-tab="credentials" class="${
         state.settingsTab === "credentials" ? "active" : ""
       }">${icon("key")}<span>Credentials</span></button>
       <button type="button" data-settings-tab="about" class="${
         state.settingsTab === "about" ? "active" : ""
       }">${icon("info")}<span>About</span></button>`;
    host.innerHTML = html;
  }

  function selectSettingsTab(tab) {
    state.settingsTab = tab;
    $$("#settings-nav button[data-settings-tab]").forEach((button) => {
      button.classList.toggle("active", button.dataset.settingsTab === tab);
    });
    renderSettingsPanel();
  }

  function renderSettingsPanel() {
    const host = $("#settings-content");
    if (!host) return;
    if (!state.settings) return;

    if (state.settingsTab === "credentials") {
      renderCredentialsPanel(host);
      updateSavebar();
      return;
    }
    if (state.settingsTab === "about") {
      renderAboutPanel(host);
      updateSavebar();
      return;
    }

    const section = state.settings.schema.find((item) => item.id === state.settingsTab) || state.settings.schema[0];
    const fields = section.fields.map(fieldHTML).join("");
    host.innerHTML = `
      <section class="settings-panel" data-panel="${esc(section.id)}">
        ${panelHead(section.icon, section.label, section.description)}
        <div class="settings-card">
          <div class="field-grid">${fields}</div>
        </div>
      </section>`;

    renderFallbackEditor();
    updateSavebar();
  }

  function fallbackValues() {
    const value = currentValue("ai.fallback");
    return Array.isArray(value) ? value.map((row) => ({ ...row })) : [];
  }

  function renderFallbackEditor() {
    const host = $("#fallback-editor");
    if (!host || !state.settings) return;
    const providers = (state.settings.providers || []).map((provider) => provider.name);
    const rows = fallbackValues();
    const rowHTML = rows
      .map((row, index) => {
        const provider = row.provider || providers[0] || "groq";
        return `
          <div class="fallback-row" data-row="${index}">
            <div class="select-wrap">
              <select data-fallback-provider="${index}">
                ${providers
                  .map(
                    (name) =>
                      `<option value="${esc(name)}" ${name === provider ? "selected" : ""}>${esc(name)}</option>`
                  )
                  .join("")}
              </select>
              ${icon("chevron")}
            </div>
            <input type="text" class="mono" data-fallback-model="${index}" list="models-${esc(provider)}"
              value="${esc(row.model || "")}" placeholder="model id">
            <button type="button" class="icon-button" data-fallback-remove="${index}" aria-label="Remove fallback">
              ${icon("trash")}
            </button>
          </div>`;
      })
      .join("");

    const datalists = (state.settings.providers || [])
      .map(
        (provider) =>
          `<datalist id="models-${esc(provider.name)}">${(provider.models || [])
            .map((model) => `<option value="${esc(model)}"></option>`)
            .join("")}</datalist>`
      )
      .join("");

    const addButton =
      rows.length < 5
        ? `<button type="button" class="add-fallback" data-fallback-add>${icon("plus")}Add fallback</button>`
        : `<div class="field-help">Five fallbacks is the maximum.</div>`;

    host.innerHTML = (rows.length ? rowHTML : "") + addButton + datalists;

    const add = $("[data-fallback-add]", host);
    if (add) {
      add.addEventListener("click", () => {
        const list = fallbackValues();
        if (list.length >= 5) return;
        list.push({ provider: providers[0] || "groq", model: "" });
        markDirty("ai.fallback", list);
        renderFallbackEditor();
      });
    }

    $$("[data-fallback-remove]", host).forEach((button) => {
      button.addEventListener("click", () => {
        const index = Number(button.dataset.fallbackRemove);
        const list = fallbackValues();
        list.splice(index, 1);
        markDirty("ai.fallback", list);
        renderFallbackEditor();
      });
    });

    $$("[data-fallback-provider]", host).forEach((select) => {
      select.addEventListener("change", () => {
        const index = Number(select.dataset.fallbackProvider);
        const list = fallbackValues();
        list[index] = { ...list[index], provider: select.value };
        markDirty("ai.fallback", list);
        renderFallbackEditor();
      });
    });

    $$("[data-fallback-model]", host).forEach((input) => {
      input.addEventListener("input", () => {
        const index = Number(input.dataset.fallbackModel);
        const list = fallbackValues();
        list[index] = { ...list[index], model: input.value };
        markDirty("ai.fallback", list);
      });
    });
  }

  function renderCredentialsPanel(host) {
    const groups = state.settings.secrets || [];
    const cards = groups
      .map((group) => {
        const configured = group.items.filter((item) => item.configured).length;
        const rows = group.items
          .map((item) => {
            const inputId = `secret-${item.key}`;
            const placeholder = item.secret
              ? item.configured
                ? `Configured · ${item.preview}`
                : "Not set"
              : item.placeholder || "Not set";
            const value = item.secret ? "" : item.preview || "";
            return `
              <div class="secret-row">
                <div>
                  <div class="secret-label">
                    <span class="credential-status ${item.configured ? "on" : ""}"></span>${esc(item.label)}
                  </div>
                  <div class="secret-env">${esc(item.key)}</div>
                </div>
                <div class="secret-controls">
                  <input id="${inputId}" type="${item.secret ? "password" : "text"}"
                    data-secret-key="${esc(item.key)}" value="${esc(value)}"
                    placeholder="${esc(placeholder)}" autocomplete="off" spellcheck="false"
                    class="${item.secret ? "mono" : "mono"}">
                </div>
                <div class="secret-controls">
                  ${
                    item.configured
                      ? `<button class="button ghost small" type="button" data-secret-remove="${esc(item.key)}">Remove</button>`
                      : ""
                  }
                  <button class="button ghost small" type="button" data-secret-save="${esc(item.key)}" disabled>Save</button>
                </div>
              </div>`;
          })
          .join("");
        return `
          <div class="settings-card secret-group">
            <div class="group-head">
              <div>
                <h3>${esc(group.label)}</h3>
                <p>${esc(group.description || "")}</p>
              </div>
              <span class="pill muted">${configured}/${group.items.length} set</span>
            </div>
            ${rows}
          </div>`;
      })
      .join("");

    host.innerHTML = `
      <section class="settings-panel" data-panel="credentials">
        ${panelHead("key", "Credentials", "Stored locally in .env. Values are masked here and never leave your machine except to the provider you configured.")}
        ${cards}
      </section>`;
  }

  function renderAboutPanel(host) {
    const app = state.status?.app || {};
    const ai = state.status?.ai || {};
    const items = [
      ["Version", app.version || "—"],
      ["Python", app.python || "—"],
      ["Platform", app.platform || "—"],
      ["Provider", ai.provider || "—"],
      ["Dashboard", `http://localhost:${app.port || location.port || 4242}`],
      ["Tool catalog", num(app.tools)],
      ["Native tools", num(app.native_tools)],
      ["Conversation", `${num(app.messages)} messages`],
    ];
    host.innerHTML = `
      <section class="settings-panel" data-panel="about">
        ${panelHead("info", "About Oryn", "An autonomous system agent for this laptop.")}
        <div class="settings-card">
          <div class="about-grid">
            ${items
              .map(
                ([label, value]) => `
                <div class="about-item">
                  <div class="about-label">${esc(label)}</div>
                  <div class="about-value">${esc(value)}</div>
                </div>`
              )
              .join("")}
          </div>
          <p class="about-copy">
            Settings live in <code>config.yaml</code>; credentials live in <code>.env</code>. Both are plain files you can edit by hand.
            The terminal agent is still available with <code>oryn</code>, and the login shortcuts are
            <code>oryn auth drive</code> and <code>oryn auth notion</code>.
          </p>
        </div>
      </section>`;
  }

  async function loadSettings() {
    try {
      const data = await api("/api/settings");
      state.settings = data;
      state.dirty.clear();
      state.fieldErrors.clear();
      renderSettingsNav();
      selectSettingsTab(state.settingsTab);
    } catch (error) {
      const host = $("#settings-content");
      if (host) {
        host.innerHTML = `
          <div class="settings-card">
            <div class="empty-row">
              <strong>Could not load settings</strong>
              ${esc(error.message)}
              <div style="margin-top:14px"><button class="button" data-retry-settings>Retry</button></div>
            </div>
          </div>`;
      }
    }
  }

  async function saveSettings() {
    if (!state.dirty.size || !state.settings) return;
    const saveBtn = $("#save-btn");
    if (saveBtn) {
      saveBtn.disabled = true;
      saveBtn.textContent = "Saving…";
    }
    try {
      const values = Object.fromEntries(state.dirty);
      const result = await api("/api/settings", { method: "POST", body: { values } });
      state.settings = { ...state.settings, ...result };
      state.dirty.clear();
      state.fieldErrors.clear();
      renderSettingsPanel();
      updateSavebar();
      const savedCount = (result.saved || []).length;
      toast("Settings saved", "success", `${savedCount} value${savedCount === 1 ? "" : "s"} written to config.yaml.`);
      refreshStatus();
    } catch (error) {
      if (error.errors) {
        state.fieldErrors = new Map(Object.entries(error.errors));
        renderSettingsPanel();
      }
      const first = error.errors ? Object.values(error.errors)[0] : error.message;
      toast("Could not save settings", "error", first || "Check the highlighted fields.");
    } finally {
      if (saveBtn) {
        saveBtn.disabled = !state.dirty.size;
        saveBtn.textContent = "Save changes";
      }
    }
  }

  async function saveSecret(key) {
    const input = $(`[data-secret-key="${key}"]`);
    if (!input) return;
    const value = input.value.trim();
    if (!value) {
      input.classList.add("invalid");
      input.focus();
      toast("Value required", "warn", `Enter a value for ${key} before saving.`);
      return;
    }
    try {
      const result = await api("/api/settings/secret", { method: "POST", body: { key, value } });
      if (result.secrets) state.settings.secrets = result.secrets;
      toast("Credential saved", "success", `${key} written to .env`);
      renderSettingsPanel();
      refreshStatus();
    } catch (error) {
      toast("Could not save credential", "error", error.message);
    }
  }

  async function removeSecret(key) {
    const ok = await confirmDialog({
      title: `Remove ${key}?`,
      message: "The value is deleted from .env. Tools that depend on it will stop working until you add it again.",
      confirmLabel: "Remove",
      danger: true,
    });
    if (!ok) return;
    try {
      const result = await api("/api/settings/secret/clear", { method: "POST", body: { key } });
      if (result.secrets) state.settings.secrets = result.secrets;
      toast("Credential removed", "success", `${key} deleted from .env`);
      renderSettingsPanel();
      refreshStatus();
    } catch (error) {
      toast("Could not remove credential", "error", error.message);
    }
  }

  /* ---------------------------------------------------------------------- */
  /* Settings interactions                                                  */
  /* ---------------------------------------------------------------------- */
  function bindSettingsInteractions() {
    const nav = $("#settings-nav");
    if (nav) {
      nav.addEventListener("click", (event) => {
        const button = event.target.closest("[data-settings-tab]");
        if (!button) return;
        selectSettingsTab(button.dataset.settingsTab);
        const content = $("#settings-content");
        if (content) content.scrollIntoView({ block: "start", behavior: "smooth" });
      });
    }

    const content = $("#settings-content");
    if (!content) return;

    content.addEventListener("input", (event) => {
      const el = event.target;
      if (el.matches("[data-secret-key]")) {
        const row = el.closest(".secret-row");
        const saveButton = row ? row.querySelector("[data-secret-save]") : null;
        if (saveButton) {
          saveButton.disabled = false;
          saveButton.classList.remove("ghost");
          saveButton.classList.add("primary");
        }
        el.classList.remove("invalid");
      }
      if (el.matches("[data-path]")) {
        const path = el.dataset.path;
        let value = el.value;
        if (el.type === "range" || el.type === "number") value = el.value === "" ? "" : Number(el.value);
        if (el.type === "range") {
          const display = $(`[data-range-value="${path}"]`);
          if (display) display.textContent = Number(el.value).toFixed(2);
        }
        el.classList.remove("invalid");
        markDirty(path, value);
      }
    });

    content.addEventListener("change", (event) => {
      const el = event.target;
      if (el.matches("[data-path]")) {
        const path = el.dataset.path;
        let value = el.value;
        if (el.type === "checkbox") value = el.checked;
        if (el.type === "number") value = el.value === "" ? "" : Number(el.value);
        markDirty(path, value);
      }
    });

    content.addEventListener("click", (event) => {
      const save = event.target.closest("[data-secret-save]");
      if (save) {
        saveSecret(save.dataset.secretSave);
        return;
      }
      const remove = event.target.closest("[data-secret-remove]");
      if (remove) {
        removeSecret(remove.dataset.secretRemove);
        return;
      }
      if (event.target.closest("[data-retry-settings]")) {
        loadSettings();
      }
    });
  }

  /* ---------------------------------------------------------------------- */
  /* Wiring                                                                 */
  /* ---------------------------------------------------------------------- */
  function bindChat() {
    const form = $("#composer");
    const input = $("#composer-input");
    if (form) {
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        sendMessage();
      });
    }
    if (input) {
      input.addEventListener("input", () => autoGrow(input));
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
          event.preventDefault();
          sendMessage();
        }
      });
    }

    const thread = $("#chat-thread");
    if (thread) {
      thread.addEventListener("click", (event) => {
        const suggestion = event.target.closest("[data-prompt]");
        if (!suggestion) return;
        sendMessage(suggestion.dataset.prompt);
      });
    }

    const actions = $("#view-actions");
    if (actions) {
      actions.addEventListener("click", (event) => {
        const button = event.target.closest("[data-action]");
        if (!button) return;
        if (button.dataset.action === "clear-chat") clearConversation();
        if (button.dataset.action === "refresh-status") {
          refreshStatus();
          renderSystem();
        }
      });
    }
    initVoice();
  }

  function bindConnections() {
    const grid = $("#connector-grid");
    if (grid) {
      grid.addEventListener("click", (event) => {
        const button = event.target.closest("[data-connector-action]");
        if (!button) return;
        runConnectorAction(button.dataset.connectorAction);
      });
    }
    const refresh = $("#refresh-connections");
    if (refresh) refresh.addEventListener("click", () => refreshStatus());
  }

  function bindTools() {
    const search = $("#tool-search");
    if (search) {
      search.addEventListener("input", () => {
        clearTimeout(state.toolTimer);
        state.toolTimer = setTimeout(() => {
          state.tool.q = search.value;
          renderToolRows();
        }, 110);
      });
    }
    const group = $("#tool-group");
    if (group) {
      group.addEventListener("change", () => {
        state.tool.group = group.value;
        renderToolRows();
      });
    }
    const scope = $("#tool-scope");
    if (scope) {
      scope.addEventListener("click", (event) => {
        const button = event.target.closest("[data-scope]");
        if (!button) return;
        state.tool.scope = button.dataset.scope;
        $$("#tool-scope button").forEach((item) => item.classList.toggle("active", item === button));
        renderToolRows();
      });
    }
  }

  function bindSystem() {
    const refresh = $("#refresh-system");
    if (refresh) {
      refresh.addEventListener("click", async () => {
        refresh.disabled = true;
        await refreshStatus();
        renderSystem();
        refresh.disabled = false;
      });
    }
  }

  function bindTheme() {
    const button = $("#theme-toggle");
    if (!button) return;
    button.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
      applyTheme(next);
    });
  }

  function bindGlobal() {
    const engine = $("#engine-card");
    if (engine) {
      engine.addEventListener("click", () => {
        switchView("settings");
        selectSettingsTab("ai");
      });
    }
    $$(".nav-item").forEach((button) => {
      button.addEventListener("click", () => switchView(button.dataset.view));
    });

    const discard = $("#discard-btn");
    if (discard) {
      discard.addEventListener("click", () => {
        state.dirty.clear();
        state.fieldErrors.clear();
        renderSettingsPanel();
        updateSavebar();
        toast("Changes discarded", "info");
      });
    }
    const save = $("#save-btn");
    if (save) save.addEventListener("click", saveSettings);

    document.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        if (state.view === "settings") {
          event.preventDefault();
          saveSettings();
        }
      }
    });
  }

  async function init() {
    initTheme();
    bindTheme();
    bindGlobal();
    bindChat();
    bindConnections();
    bindTools();
    bindSystem();
    bindSettingsInteractions();

    switchView("chat");
    showEmptyState();
    await refreshStatus();
    showEmptyState(); // refresh the greeting with the real user name
    await loadHistory();
    updateSavebar();

    state.statusTimer = setInterval(() => {
      if (document.hidden) return;
      refreshStatus();
    }, 5000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
