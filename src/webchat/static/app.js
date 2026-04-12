const timeline = document.getElementById("timeline");
const composer = document.getElementById("composer");
const promptInput = document.getElementById("prompt");
const sendButton = document.getElementById("send");
const statusEl = document.getElementById("status");
const statusDot = document.getElementById("status-dot");
const sessionListEl = document.getElementById("session-list");
const titleEl = document.getElementById("title");
const newSessionButton = document.getElementById("new-session");
const clearHistoryButton = document.getElementById("clear-history");
const messageTemplate = document.getElementById("message-template");
const progressTemplate = document.getElementById("progress-template");

const STORAGE_KEY = "creative_claw_webchat_session_id";
const SESSION_INDEX_KEY = "creative_claw_webchat_sessions";
const HISTORY_KEY_PREFIX = "creative_claw_webchat_history:";
const HIDDEN_PROGRESS_TITLES = new Set(["Starting", "Finalize Result"]);

let sessionId = ensureSessionId();
let socket = null;
let activeProgressCard = null;

connect();
restoreHistory();
renderSessionList();

function ensureSessionId() {
  const existing = window.localStorage.getItem(STORAGE_KEY);
  if (existing) {
    return existing;
  }
  const created = `web-${crypto.randomUUID()}`;
  window.localStorage.setItem(STORAGE_KEY, created);
  return created;
}

function historyKey(currentSessionId = sessionId) {
  return `${HISTORY_KEY_PREFIX}${currentSessionId}`;
}

function loadHistory(currentSessionId = sessionId) {
  try {
    const raw = window.localStorage.getItem(historyKey(currentSessionId));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveHistory(items, currentSessionId = sessionId) {
  window.localStorage.setItem(historyKey(currentSessionId), JSON.stringify(items.slice(-120)));
}

function appendHistory(entry, currentSessionId = sessionId) {
  const items = loadHistory(currentSessionId);
  items.push(entry);
  saveHistory(items, currentSessionId);
  recordSessionActivity(currentSessionId);
  renderSessionList();
}

function loadSessions() {
  try {
    const raw = window.localStorage.getItem(SESSION_INDEX_KEY);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function recordSessionActivity(currentSessionId) {
  let sessions = loadSessions();
  sessions = sessions.filter((value) => value !== currentSessionId);
  sessions.unshift(currentSessionId);
  window.localStorage.setItem(SESSION_INDEX_KEY, JSON.stringify(sessions.slice(0, 20)));
}

function removeSessionActivity(currentSessionId) {
  const sessions = loadSessions().filter((value) => value !== currentSessionId);
  window.localStorage.setItem(SESSION_INDEX_KEY, JSON.stringify(sessions));
}

function renderSessionList() {
  const sessions = loadSessions();
  if (!sessions.includes(sessionId)) {
    sessions.unshift(sessionId);
  }
  sessionListEl.innerHTML = "";

  for (const item of sessions) {
    const li = document.createElement("li");
    li.className = `session-item${item === sessionId ? " active" : ""}`;

    const title = document.createElement("div");
    title.className = "session-item-title";
    title.textContent = item === sessionId ? "Current Session" : "Saved Session";

    const meta = document.createElement("div");
    meta.className = "session-item-meta";
    meta.textContent = item.replace("web-", "").slice(0, 8);

    li.appendChild(title);
    li.appendChild(meta);
    li.addEventListener("click", () => {
      if (item !== sessionId) {
        switchSession(item);
      }
    });

    sessionListEl.appendChild(li);
  }
}

function switchSession(nextSessionId) {
  sessionId = nextSessionId;
  window.localStorage.setItem(STORAGE_KEY, sessionId);
  disconnect();
  clearTimeline();
  restoreHistory();
  renderSessionList();
  connect();
}

function createNewSession() {
  const nextSessionId = `web-${crypto.randomUUID()}`;
  sessionId = nextSessionId;
  window.localStorage.setItem(STORAGE_KEY, nextSessionId);
  disconnect();
  clearTimeline();
  renderEmptyState();
  renderSessionList();
  connect();
}

function clearCurrentSession() {
  window.localStorage.removeItem(historyKey());
  removeSessionActivity(sessionId);
  clearTimeline();
  renderEmptyState();
  renderSessionList();
}

function wsUrl() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws?session_id=${encodeURIComponent(sessionId)}`;
}

function connect() {
  setStatus("connecting");
  socket = new WebSocket(wsUrl());

  socket.addEventListener("open", () => {
    setStatus("connected");
  });

  socket.addEventListener("close", () => {
    setStatus("disconnected");
  });

  socket.addEventListener("error", () => {
    setStatus("error");
  });

  socket.addEventListener("message", (event) => {
    let payload = {};
    try {
      payload = JSON.parse(event.data);
    } catch {
      addMessageCard("error", "CreativeClaw", "Received an invalid response payload.");
      return;
    }
    handleEvent(payload);
  });
}

function disconnect() {
  if (socket) {
    socket.close();
    socket = null;
  }
  activeProgressCard = null;
}

function setStatus(status) {
  statusEl.textContent = status;
  statusDot.className = "status-dot";
  if (status === "connected" || status === "ready") {
    statusDot.classList.add("connected");
  }
  if (status === "error" || status === "disconnected") {
    statusDot.classList.add("error");
  }
}

function handleEvent(payload) {
  if (payload.type === "ready") {
    setStatus("ready");
    if (payload.title) {
      titleEl.textContent = payload.title;
      document.title = payload.title;
    }
    renderEmptyStateIfNeeded();
    return;
  }

  if (payload.type === "progress") {
    upsertProgressCard(payload.content || "", payload.metadata || {});
    return;
  }

  activeProgressCard = null;

  if (payload.type === "assistant_message") {
    addMessageCard("assistant", "CreativeClaw", payload.content || "", payload.artifacts || []);
    appendHistory({
      type: "assistant",
      role: "CreativeClaw",
      content: payload.content || "",
      artifacts: payload.artifacts || [],
    });
    return;
  }

  if (payload.type === "error") {
    addMessageCard("error", "CreativeClaw", payload.content || payload.message || "Unknown error.");
    appendHistory({
      type: "error",
      role: "CreativeClaw",
      content: payload.content || payload.message || "Unknown error.",
      artifacts: [],
    });
  }
}

function renderEmptyStateIfNeeded() {
  if (timeline.children.length === 0) {
    renderEmptyState();
  }
}

function renderEmptyState() {
  const block = document.createElement("article");
  block.className = "empty-state";
  block.textContent = "Start with a prompt such as: “Create a cinematic travel poster”, “Describe this image idea”, or “Rewrite this prompt for cleaner composition.”";
  timeline.appendChild(block);
}

function clearTimeline() {
  timeline.innerHTML = "";
  activeProgressCard = null;
}

function restoreHistory() {
  clearTimeline();
  const items = loadHistory();
  if (items.length === 0) {
    renderEmptyState();
    return;
  }

  for (const item of items) {
    addMessageCard(item.type || "assistant", item.role || "CreativeClaw", item.content || "", item.artifacts || [], false);
  }
  scrollToBottom();
}

function removeEmptyState() {
  const empty = timeline.querySelector(".empty-state");
  if (empty) {
    empty.remove();
  }
}

function addMessageCard(type, role, content, artifacts = [], scroll = true) {
  removeEmptyState();
  const fragment = messageTemplate.content.cloneNode(true);
  const root = fragment.querySelector(".message-card");
  root.classList.add(type);
  fragment.querySelector(".message-role").textContent = role;
  fragment.querySelector(".message-body").innerHTML = renderMarkdown(content || "");
  const artifactGrid = fragment.querySelector(".artifact-grid");
  renderArtifacts(artifactGrid, artifacts);
  timeline.appendChild(fragment);
  if (scroll) {
    scrollToBottom();
  }
}

function upsertProgressCard(content, metadata) {
  removeEmptyState();
  if (!activeProgressCard) {
    const fragment = progressTemplate.content.cloneNode(true);
    timeline.appendChild(fragment);
    activeProgressCard = timeline.lastElementChild;
  }
  const rawTitle = String(metadata.stage_title || "").trim();
  const titleEl = activeProgressCard.querySelector(".progress-title");
  if (HIDDEN_PROGRESS_TITLES.has(rawTitle)) {
    titleEl.hidden = true;
    titleEl.textContent = "";
  } else {
    titleEl.hidden = false;
    titleEl.textContent = rawTitle || "Working";
  }
  activeProgressCard.querySelector(".progress-body").innerHTML = renderMarkdown(content);
  scrollToBottom();
}

function renderArtifacts(container, artifacts) {
  container.innerHTML = "";
  if (!artifacts || artifacts.length === 0) {
    container.style.display = "none";
    return;
  }
  container.style.display = "grid";
  for (const artifact of artifacts) {
    const anchor = document.createElement("a");
    anchor.className = "artifact-card";
    anchor.href = artifact.url;
    anchor.target = "_blank";
    anchor.rel = "noreferrer";

    if (artifact.isImage) {
      const image = document.createElement("img");
      image.src = artifact.url;
      image.alt = artifact.name || "artifact";
      image.addEventListener("load", scrollToBottom, { once: true });
      anchor.appendChild(image);
    }

    const name = document.createElement("div");
    name.className = "artifact-name";
    name.textContent = artifact.name || "artifact";
    anchor.appendChild(name);

    const meta = document.createElement("div");
    meta.className = "artifact-meta";
    meta.textContent = artifact.path || artifact.mimeType || "";
    anchor.appendChild(meta);

    container.appendChild(anchor);
  }
}

function renderMarkdown(text) {
  let html = escapeHtml(text || "");
  html = html.replace(/```([\s\S]*?)```/g, (_, code) => `<pre><code>${escapeHtml(code.trim())}</code></pre>`);
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');

  const lines = html.split("\n");
  const rendered = [];
  let inList = false;
  for (const line of lines) {
    if (/^\s*[-*]\s+/.test(line)) {
      if (!inList) {
        rendered.push("<ul>");
        inList = true;
      }
      rendered.push(`<li>${line.replace(/^\s*[-*]\s+/, "")}</li>`);
      continue;
    }
    if (inList) {
      rendered.push("</ul>");
      inList = false;
    }
    if (line.trim()) {
      rendered.push(`<p>${line}</p>`);
    }
  }
  if (inList) {
    rendered.push("</ul>");
  }
  return rendered.join("");
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function scrollToBottom() {
  window.requestAnimationFrame(() => {
    timeline.scrollTop = timeline.scrollHeight;
  });
}

function sendPrompt() {
  const content = promptInput.value.trim();
  if (!content || !socket || socket.readyState !== WebSocket.OPEN) {
    return;
  }
  socket.send(JSON.stringify({ type: "chat", content }));
  addMessageCard("user", "You", content);
  appendHistory({ type: "user", role: "You", content, artifacts: [] });
  promptInput.value = "";
  promptInput.style.height = "";
  activeProgressCard = null;
}

composer.addEventListener("submit", (event) => {
  event.preventDefault();
  sendPrompt();
});

promptInput.addEventListener("input", () => {
  promptInput.style.height = "";
  promptInput.style.height = `${Math.min(promptInput.scrollHeight, 220)}px`;
});

promptInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    sendPrompt();
  }
});

newSessionButton.addEventListener("click", () => {
  createNewSession();
});

clearHistoryButton.addEventListener("click", () => {
  clearCurrentSession();
});

window.addEventListener("beforeunload", () => {
  disconnect();
});
