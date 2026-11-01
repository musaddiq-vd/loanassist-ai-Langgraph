/* =====================================================================
   LoanAssist chat UI: sign-in, sidebar history, rename, search, themes, chat.

   Two modes:
   - Signed-in (window.LOANASSIST_AUTH set, i.e. on AWS): chats live in
     DynamoDB through the API, so they follow the user to every device.
   - Local (no auth config): chats live in this browser's localStorage.
   ===================================================================== */

const AUTH = window.LOANASSIST_AUTH || null;
const REMOTE = Boolean(AUTH);
const API_BASE = window.LOANASSIST_API_URL.replace(/\/chat$/, ""); // ".../api/v1"

const $ = (sel) => document.querySelector(sel);
const app = $("#app");
const form = $("#chatForm");
const input = $("#messageInput");
const messagesEl = $("#chatMessages");
const sendButton = $("#sendButton");
const scroller = $("#scroller");
const thread = $("#thread");
const historyEl = $("#history");
const searchInput = $("#searchInput");
const chatTitleEl = $("#chatTitle");

const STORE_KEY = "loanassist.chats";
const THEME_KEY = "loanassist.theme";
const SIDEBAR_KEY = "loanassist.sidebar";

const AUTH_KEY = "loanassist.auth";

let chats = REMOTE ? [] : loadChats();
let currentId = null;
let busy = false;

/* ---------- Icons ---------- */
const ICONS = {
  avatar: '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path fill="currentColor" d="M12 2 3 7v2h18V7l-9-5Zm-7 9v7h3v-7H5Zm5 0v7h4v-7h-4Zm6 0v7h3v-7h-3ZM3 20v2h18v-2H3Z"/></svg>',
  copy: '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
  check: '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path d="m5 12 5 5 9-10" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  doc: '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z M14 3v5h5" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
  edit: '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
  trash: '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

const TOOL_LABELS = {
  retrieve_loan_documents: "Searched loan documents",
  calculate_emi: "Used EMI calculator",
  check_loan_eligibility: "Checked eligibility",
  get_customer_details: "Looked up customer details",
};

/* =====================================================================
   Storage (wrapped: storage can be blocked in private windows)
   ===================================================================== */
function loadChats() {
  try { return JSON.parse(localStorage.getItem(STORE_KEY)) || []; } catch { return []; }
}
function saveChats() {
  if (REMOTE) return; // the server is the source of truth when signed in
  try { localStorage.setItem(STORE_KEY, JSON.stringify(chats)); } catch { /* ignore */ }
}
function getChat(id) { return chats.find((c) => c.id === id); }

/* =====================================================================
   Sign-in: Cognito managed login, OAuth authorization code + PKCE
   ===================================================================== */
const redirectUri = `${window.location.origin}/`;

function b64url(bytes) {
  return btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function randomString() { return b64url(crypto.getRandomValues(new Uint8Array(32))); }
async function sha256(text) { return b64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text))); }

function readTokens() { try { return JSON.parse(localStorage.getItem(AUTH_KEY)); } catch { return null; } }
function writeTokens(tokens) {
  try { tokens ? localStorage.setItem(AUTH_KEY, JSON.stringify(tokens)) : localStorage.removeItem(AUTH_KEY); } catch { /* ignore */ }
}
function jwtPayload(token) {
  const part = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
  const bytes = Uint8Array.from(atob(part.padEnd(part.length + ((4 - (part.length % 4)) % 4), "=")), (c) => c.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes));
}

async function startLogin(signUp = false) {
  const verifier = randomString();
  const state = randomString();
  try { sessionStorage.setItem("loanassist.pkce", JSON.stringify({ verifier, state })); } catch { /* ignore */ }
  const params = new URLSearchParams({
    client_id: AUTH.clientId,
    response_type: "code",
    scope: "openid email profile",
    redirect_uri: redirectUri,
    state,
    code_challenge: await sha256(verifier),
    code_challenge_method: "S256",
  });
  window.location.assign(`${AUTH.domain}/${signUp ? "signup" : "oauth2/authorize"}?${params}`);
}

async function tokenRequest(body) {
  const response = await fetch(`${AUTH.domain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_id: AUTH.clientId, ...body }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error_description || data.error || "Sign-in failed");
  return data;
}
function storeTokenResponse(data, previousRefresh) {
  writeTokens({
    idToken: data.id_token,
    refreshToken: data.refresh_token || previousRefresh,
    expiresAt: Date.now() + data.expires_in * 1000,
  });
}

// Coming back from the Cognito page with ?code=... -> exchange it for tokens.
async function handleLoginRedirect() {
  const params = new URLSearchParams(window.location.search);
  if (!params.has("code") && !params.has("error")) return;
  let saved = null;
  try { saved = JSON.parse(sessionStorage.getItem("loanassist.pkce")); sessionStorage.removeItem("loanassist.pkce"); } catch { /* ignore */ }
  window.history.replaceState(null, "", "/");
  if (params.has("error")) throw new Error(params.get("error_description") || params.get("error"));
  if (!saved || saved.state !== params.get("state")) throw new Error("Sign-in expired. Please try again.");
  const data = await tokenRequest({
    grant_type: "authorization_code",
    code: params.get("code"),
    redirect_uri: redirectUri,
    code_verifier: saved.verifier,
  });
  storeTokenResponse(data);
}

// A valid ID token, refreshed silently when it is about to expire.
let refreshing = null;
async function getIdToken() {
  const tokens = readTokens();
  if (!tokens) return null;
  if (tokens.expiresAt - 60_000 > Date.now()) return tokens.idToken;
  if (!tokens.refreshToken) return null;
  refreshing ||= tokenRequest({ grant_type: "refresh_token", refresh_token: tokens.refreshToken })
    .then((data) => { storeTokenResponse(data, tokens.refreshToken); return readTokens().idToken; })
    .catch(() => { writeTokens(null); return null; })
    .finally(() => { refreshing = null; });
  return refreshing;
}

function signOut() {
  writeTokens(null);
  const params = new URLSearchParams({ client_id: AUTH.clientId, logout_uri: redirectUri });
  window.location.assign(`${AUTH.domain}/logout?${params}`);
}

function showAuthGate(message = "") {
  $("#authGate").hidden = false;
  const err = $("#authError");
  err.hidden = !message;
  err.textContent = message;
}

function showAccount(idToken) {
  const { email = "Signed in" } = jwtPayload(idToken);
  $("#account").hidden = false;
  $("#accountEmail").textContent = email;
  $("#accountEmail").title = email;
  $("#accountInitial").textContent = email.charAt(0);
  $("#storageNote").textContent = "Your chats are synced to your account.";
}

/* ---------- API calls (adds the sign-in token) ---------- */
async function apiFetch(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (REMOTE) {
    const token = await getIdToken();
    if (!token) { showAuthGate("Your session expired. Please sign in again."); throw new Error("Please sign in."); }
    headers.Authorization = token;
  }
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (response.status === 401 && REMOTE) {
    writeTokens(null);
    showAuthGate("Your session expired. Please sign in again.");
    throw new Error("Please sign in.");
  }
  return response;
}
async function apiJson(path, options = {}) {
  const response = await apiFetch(path, options);
  const data = response.status === 204 ? null : await response.json().catch(() => ({}));
  if (!response.ok) throw new Error((data && data.detail) || `Request failed (${response.status})`);
  return data;
}

/* =====================================================================
   Smart chat titles from the first question
   ===================================================================== */
const inr = (n) => "₹" + Number(n).toLocaleString("en-IN");

function makeTitle(text) {
  const q = text.replace(/\s+/g, " ").trim();
  const lower = q.toLowerCase();
  const loanType = (lower.match(/\b(home|personal|car|auto|education|business|gold|vehicle|two[- ]wheeler)\b/) || [])[1];
  const loanLabel = loanType ? loanType.charAt(0).toUpperCase() + loanType.slice(1) + " loan" : "Loan";

  // EMI questions -> "EMI · ₹50,00,000 at 8.5% for 20 yrs"
  if (/\bemi\b/.test(lower)) {
    const amount = (q.replace(/,/g, "").match(/\b\d{4,}\b/) || [])[0];
    const rate = (q.match(/(\d+(?:\.\d+)?)\s*%/) || [])[1];
    const years = (q.match(/(\d+)\s*(?:years?|yrs?)/i) || [])[1];
    const parts = [amount && inr(amount), rate && `at ${rate}%`, years && `for ${years} yrs`].filter(Boolean);
    return parts.length ? `EMI · ${parts.join(" ")}` : "EMI calculation";
  }
  // CIBIL / eligibility -> "Eligibility · CIBIL 620"
  const cibil = (lower.match(/(?:cibil|credit score)[^\d]{0,15}(\d{3})/) || [])[1];
  if (cibil) return `Eligibility · CIBIL ${cibil}`;
  if (/eligib/.test(lower)) return `${loanLabel} eligibility`;
  // Documents -> "Home loan documents"
  if (/document|papers|kyc/.test(lower)) return `${loanLabel} documents`;
  if (/process|steps|procedure|apply/.test(lower)) return `${loanLabel} application process`;
  if (/interest rate|rate of interest/.test(lower)) return `${loanLabel} interest rates`;

  // Generic: drop greetings/filler words (repeatedly) and shorten.
  const filler = /^(hi|hello|hey|please|pls|kindly|can you|could you|would you|tell me|tell|i want to know|i want|i need|explain|about|what is|what are|what's|how do i|how to|how can i|the)[,!.\s]+/i;
  let t = q.replace(/[?.!]+$/, "");
  while (filler.test(t)) t = t.replace(filler, "");
  t = t.charAt(0).toUpperCase() + t.slice(1);
  if (t.length > 42) t = t.slice(0, 42).replace(/\s+\S*$/, "") + "…";
  return t || "New chat";
}

/* =====================================================================
   Minimal, safe Markdown -> HTML (everything is escaped first)
   ===================================================================== */
function escapeHtml(text) {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
function inline(text) {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
}
function renderMarkdown(source) {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const html = [];
  let list = null;
  let para = [];
  let inCode = false;
  let code = [];
  const flushPara = () => { if (para.length) { html.push(`<p>${para.map(inline).join("<br>")}</p>`); para = []; } };
  const closeList = () => { if (list) { html.push(`</${list}>`); list = null; } };

  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      if (inCode) { html.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`); code = []; inCode = false; }
      else { flushPara(); closeList(); inCode = true; }
      continue;
    }
    if (inCode) { code.push(line); continue; }
    const heading = line.match(/^#{1,6}\s+(.*)$/);
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (!line.trim()) { flushPara(); closeList(); }
    else if (heading) { flushPara(); closeList(); html.push(`<h3>${inline(heading[1])}</h3>`); }
    else if (bullet || numbered) {
      flushPara();
      const type = bullet ? "ul" : "ol";
      if (list !== type) { closeList(); html.push(`<${type}>`); list = type; }
      html.push(`<li>${inline((bullet || numbered)[1])}</li>`);
    } else { closeList(); para.push(line.trim()); }
  }
  if (inCode) html.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
  flushPara();
  closeList();
  return html.join("");
}

/* =====================================================================
   Sidebar: history list, search, rename, delete
   ===================================================================== */
function dayGroup(ts) {
  const startOfToday = new Date().setHours(0, 0, 0, 0);
  const day = 86400000;
  if (ts >= startOfToday) return "Today";
  if (ts >= startOfToday - day) return "Yesterday";
  if (ts >= startOfToday - 7 * day) return "Previous 7 days";
  if (ts >= startOfToday - 30 * day) return "Previous 30 days";
  return "Older";
}

function renderHistory() {
  const q = searchInput.value.trim().toLowerCase();
  const list = [...chats]
    .sort((a, b) => b.updatedAt - a.updatedAt)
    .filter((c) => !q || c.title.toLowerCase().includes(q) || (c.messages || []).some((m) => (m.text || "").toLowerCase().includes(q)));

  historyEl.innerHTML = "";
  if (!list.length) {
    const empty = document.createElement("div");
    empty.className = "history-empty";
    empty.textContent = q ? "No chats match your search." : "Your chats will appear here.";
    historyEl.append(empty);
    return;
  }

  let lastGroup = null;
  for (const chat of list) {
    const group = dayGroup(chat.updatedAt);
    if (group !== lastGroup) {
      const heading = document.createElement("div");
      heading.className = "history-group";
      heading.textContent = group;
      historyEl.append(heading);
      lastGroup = group;
    }
    historyEl.append(historyItem(chat));
  }
}

function historyItem(chat) {
  const item = document.createElement("div");
  item.className = "history-item" + (chat.id === currentId ? " active" : "");
  item.tabIndex = 0;
  item.title = chat.title;

  const title = document.createElement("span");
  title.className = "title";
  title.textContent = chat.title;

  const actions = document.createElement("div");
  actions.className = "actions";
  const renameBtn = iconButton(ICONS.edit, "Rename chat", "rename-btn");
  const deleteBtn = iconButton(ICONS.trash, "Delete chat", "delete");
  actions.append(renameBtn, deleteBtn);
  item.append(title, actions);

  item.addEventListener("click", () => openChat(chat.id));
  item.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target === item) openChat(chat.id); });
  title.addEventListener("dblclick", (e) => { e.stopPropagation(); startRename(item, chat); });
  renameBtn.addEventListener("click", (e) => { e.stopPropagation(); startRename(item, chat); });
  deleteBtn.addEventListener("click", (e) => { e.stopPropagation(); deleteChat(chat.id); });
  return item;
}

function iconButton(svg, label, extraClass = "") {
  const b = document.createElement("button");
  b.type = "button";
  b.className = `icon-btn ${extraClass}`.trim();
  b.title = label;
  b.setAttribute("aria-label", label);
  b.innerHTML = svg;
  return b;
}

function startRename(item, chat) {
  const field = document.createElement("input");
  field.className = "rename";
  field.value = chat.title;
  field.maxLength = 80;
  field.setAttribute("aria-label", "Chat name");
  item.replaceChildren(field);
  field.focus();
  field.select();

  let done = false;
  const finish = (save) => {
    if (done) return;
    done = true;
    const value = field.value.trim();
    if (save && value && value !== chat.title) {
      const previous = chat.title;
      chat.title = value;
      saveChats();
      if (chat.id === currentId) chatTitleEl.textContent = chat.title;
      if (REMOTE) {
        apiJson(`/chats/${chat.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: value }),
        }).catch((error) => {
          chat.title = previous; // roll back if the server refused
          if (chat.id === currentId) chatTitleEl.textContent = previous;
          renderHistory();
          alert(`Could not rename the chat. ${error.message}`);
        });
      }
    }
    renderHistory();
  };
  field.addEventListener("click", (e) => e.stopPropagation());
  field.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); finish(true); }
    if (e.key === "Escape") finish(false);
  });
  field.addEventListener("blur", () => finish(true));
}

async function deleteChat(id) {
  const chat = getChat(id);
  if (!chat || !confirm(`Delete "${chat.title}"?`)) return;
  if (REMOTE) {
    try { await apiJson(`/chats/${id}`, { method: "DELETE" }); }
    catch (error) { alert(`Could not delete the chat. ${error.message}`); return; }
  }
  chats = chats.filter((c) => c.id !== id);
  saveChats();
  if (id === currentId) newChat();
  else renderHistory();
}

/* =====================================================================
   Chat view
   ===================================================================== */
function scrollToBottom(smooth = true) {
  scroller.scrollTo({ top: scroller.scrollHeight, behavior: smooth ? "smooth" : "auto" });
}

function renderUser(text) {
  thread.classList.add("has-messages");
  const el = document.createElement("article");
  el.className = "msg user";
  const content = document.createElement("div");
  content.className = "content";
  content.textContent = text;
  el.append(content);
  messagesEl.append(el);
}

function renderAssistantShell() {
  const el = document.createElement("article");
  el.className = "msg assistant";
  el.innerHTML = `<div class="avatar">${ICONS.avatar}</div><div class="body"><div class="thinking" aria-label="LoanAssist is thinking"><i></i><i></i><i></i></div></div>`;
  messagesEl.append(el);
  return el;
}

function fillAssistant(el, data, isError = false) {
  const body = el.querySelector(".body");
  body.innerHTML = "";
  if (isError) el.classList.add("error");

  if (data.requires_human_review) {
    const badge = document.createElement("div");
    badge.className = "review-badge";
    badge.textContent = "⚠ Sent for loan officer review";
    body.append(badge);
  }

  const content = document.createElement("div");
  content.className = "content";
  content.innerHTML = renderMarkdown(data.answer || "");
  body.append(content);

  const meta = document.createElement("div");
  meta.className = "meta";
  const sources = [...new Map((data.sources || []).filter((s) => s.uri).map((s) => [s.uri, s])).values()];
  for (const s of sources) {
    const chip = document.createElement("span");
    chip.className = "source";
    chip.title = s.excerpt || s.uri;
    chip.innerHTML = ICONS.doc;
    const name = document.createElement("span");
    name.textContent = decodeURIComponent(s.uri.split("/").pop() || "Document");
    chip.append(name);
    meta.append(chip);
  }
  if (TOOL_LABELS[data.tool_used]) {
    const tag = document.createElement("span");
    tag.className = "tool-tag";
    tag.textContent = TOOL_LABELS[data.tool_used];
    meta.append(tag);
  }
  if (!isError) {
    const copy = iconButton(ICONS.copy, "Copy answer");
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(data.answer);
        copy.innerHTML = ICONS.check;
        setTimeout(() => { copy.innerHTML = ICONS.copy; }, 1500);
      } catch { /* clipboard blocked */ }
    });
    meta.append(copy);
  }
  body.append(meta);
}

function renderAssistant(msg) {
  const el = renderAssistantShell();
  fillAssistant(el, msg.data || { answer: msg.text }, msg.error);
}

async function openChat(id) {
  const chat = getChat(id);
  if (!chat) return;
  currentId = id;
  messagesEl.innerHTML = "";
  chatTitleEl.textContent = chat.title;
  document.title = `${chat.title} · LoanAssist`;
  renderHistory();
  closeSidebarOnMobile();

  // Signed in: messages are fetched from the server the first time a chat opens.
  if (!chat.messages) {
    thread.classList.add("has-messages");
    messagesEl.innerHTML = '<div class="loading-line">Loading chat…</div>';
    try {
      const full = await apiJson(`/chats/${id}`);
      chat.messages = full.messages;
    } catch (error) {
      if (currentId === id) messagesEl.innerHTML = "";
      if (currentId === id) renderAssistant({ text: `Could not load this chat. ${error.message}`, error: true });
      return;
    }
    if (currentId !== id) return; // user switched to another chat meanwhile
    messagesEl.innerHTML = "";
  }

  thread.classList.toggle("has-messages", chat.messages.length > 0);
  for (const m of chat.messages) {
    if (m.role === "user") renderUser(m.text);
    else renderAssistant(m);
  }
  if (chat.pending) renderAssistantShell();
  scrollToBottom(false);
  input.focus();
}

function newChat() {
  currentId = null;
  messagesEl.innerHTML = "";
  thread.classList.remove("has-messages");
  chatTitleEl.textContent = "New chat";
  document.title = "LoanAssist AI";
  input.value = "";
  autoGrow();
  updateSendState();
  renderHistory();
  closeSidebarOnMobile();
  input.focus();
}

/* =====================================================================
   Sending
   ===================================================================== */
function updateSendState() { sendButton.disabled = busy || !input.value.trim(); }
function autoGrow() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 200)}px`;
  input.style.overflowY = input.scrollHeight > 200 ? "auto" : "hidden";
}

async function send(message) {
  if (busy || !message) return;
  busy = true;

  // First message creates the chat and gives it a smart title.
  let chat = getChat(currentId);
  const isNew = !chat;
  if (!chat) {
    chat = { id: crypto.randomUUID(), title: makeTitle(message), messages: [], createdAt: Date.now(), updatedAt: Date.now() };
    chats.push(chat);
    currentId = chat.id;
    chatTitleEl.textContent = chat.title;
    document.title = `${chat.title} · LoanAssist`;
  }
  const chatId = chat.id;
  chat.messages.push({ role: "user", text: message });
  chat.updatedAt = Date.now();
  chat.pending = true;
  saveChats();
  renderHistory();

  renderUser(message);
  const shell = renderAssistantShell();
  scrollToBottom();
  input.value = "";
  autoGrow();
  updateSendState();

  let reply;
  try {
    // The chat id is also the server-side history key, so follow-ups keep context.
    // The server saves both messages; the title is only used for a brand-new chat.
    const data = await apiJson("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id: chatId, title: isNew ? chat.title : undefined }),
    });
    reply = { role: "assistant", text: data.answer, data };
  } catch (error) {
    reply = { role: "assistant", text: `Sorry, I couldn't reach LoanAssist. ${error.message}`, error: true };
  }

  // The chat may have been deleted or switched away from while waiting.
  const target = getChat(chatId);
  if (target && target.messages) {
    target.messages.push(reply);
    target.updatedAt = Date.now();
    delete target.pending;
    saveChats();
  }
  if (currentId === chatId && shell.isConnected) {
    fillAssistant(shell, reply.data || { answer: reply.text }, reply.error);
    scrollToBottom();
  } else if (currentId === chatId) {
    openChat(chatId);
  }
  busy = false;
  updateSendState();
  renderHistory();
  input.focus();
}

/* =====================================================================
   Theme (light / dark) and sidebar toggles
   ===================================================================== */
function systemDark() { return window.matchMedia("(prefers-color-scheme: dark)").matches; }
function currentTheme() { return document.documentElement.dataset.theme || (systemDark() ? "dark" : "light"); }
function applyThemeUI() {
  const dark = currentTheme() === "dark";
  app.classList.toggle("is-dark", dark);
  $("#themeLabel").textContent = dark ? "Light mode" : "Dark mode";
}
$("#themeToggle").addEventListener("click", () => {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem(THEME_KEY, next); } catch { /* ignore */ }
  applyThemeUI();
});
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyThemeUI);

const isMobile = () => window.matchMedia("(max-width: 800px)").matches;
function openSidebar() {
  if (isMobile()) app.classList.add("sidebar-open");
  else { app.classList.remove("sidebar-collapsed"); try { localStorage.setItem(SIDEBAR_KEY, "open"); } catch {} }
}
function closeSidebar() {
  if (isMobile()) app.classList.remove("sidebar-open");
  else { app.classList.add("sidebar-collapsed"); try { localStorage.setItem(SIDEBAR_KEY, "collapsed"); } catch {} }
}
function closeSidebarOnMobile() { if (isMobile()) app.classList.remove("sidebar-open"); }

$("#openSidebar").addEventListener("click", openSidebar);
$("#closeSidebar").addEventListener("click", closeSidebar);
$("#backdrop").addEventListener("click", closeSidebarOnMobile);

/* =====================================================================
   Wiring
   ===================================================================== */
input.addEventListener("input", () => { autoGrow(); updateSendState(); });
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(input.value.trim()); }
});
form.addEventListener("submit", (e) => { e.preventDefault(); send(input.value.trim()); });
document.querySelectorAll(".chip").forEach((chip) => chip.addEventListener("click", () => send(chip.dataset.prompt)));
$("#newChat").addEventListener("click", () => { if (!busy) newChat(); });
$("#topNewChat").addEventListener("click", () => { if (!busy) newChat(); });
searchInput.addEventListener("input", renderHistory);
document.addEventListener("keydown", (e) => {
  // Ctrl/Cmd + K focuses search, Ctrl/Cmd + Shift + O starts a new chat.
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openSidebar(); searchInput.focus(); }
  if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === "o") { e.preventDefault(); if (!busy) newChat(); }
});

$("#signIn").addEventListener("click", () => startLogin(false));
$("#signUp").addEventListener("click", () => startLogin(true));
$("#signOut").addEventListener("click", signOut);

/* =====================================================================
   Start-up
   ===================================================================== */
async function init() {
  try { if (localStorage.getItem(SIDEBAR_KEY) === "collapsed" && !isMobile()) app.classList.add("sidebar-collapsed"); } catch {}
  applyThemeUI();
  newChat();

  if (!REMOTE) {
    // Chats left "pending" by a closed tab never got a reply; clear the flag.
    chats.forEach((c) => delete c.pending);
    return;
  }

  try { await handleLoginRedirect(); }
  catch (error) { showAuthGate(error.message); return; }

  const token = await getIdToken();
  if (!token) { showAuthGate(); return; }
  showAccount(token);

  historyEl.innerHTML = '<div class="history-empty">Loading chats…</div>';
  try {
    chats = await apiJson("/chats"); // titles only; messages load when a chat opens
    renderHistory();
  } catch (error) {
    historyEl.innerHTML = "";
    const msg = document.createElement("div");
    msg.className = "history-empty";
    msg.textContent = `Could not load chats. ${error.message}`;
    historyEl.append(msg);
  }
}

init();
