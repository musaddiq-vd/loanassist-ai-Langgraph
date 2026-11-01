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
const attachButton = $("#attachButton");
const fileInput = $("#fileInput");
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
  file: '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z M14 3v5h5 M9 13h6 M9 17h4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/></svg>',
  thumbUp: '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path d="M7 11v9H4v-9h3Zm0 0 4-7a2 2 0 0 1 3 2l-1 4h5a2 2 0 0 1 2 2.3l-1.2 6A2 2 0 0 1 16.8 20H7" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round"/></svg>',
  thumbDown: '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path d="M17 13V4h3v9h-3Zm0 0-4 7a2 2 0 0 1-3-2l1-4H6a2 2 0 0 1-2-2.3l1.2-6A2 2 0 0 1 7.2 4H17" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round"/></svg>',
  trash: '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

const TOOL_LABELS = {
  retrieve_loan_documents: "Searched loan documents",
  calculate_emi: "Used EMI calculator",
  check_loan_eligibility: "Checked eligibility",
  get_customer_details: "Looked up customer details",
  document_question: "Read your document",
  analyze_document: "Analysed your document",
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
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
    .replace(/(^|[\s(])_([^_\s][^_]*)_(?=$|[\s.,;:!?)])/g, "$1<em>$2</em>");
}
function renderMarkdown(source) {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const html = [];
  let list = null;
  let para = [];
  let inCode = false;
  let code = [];
  let table = [];
  const flushPara = () => { if (para.length) { html.push(`<p>${para.map(inline).join("<br>")}</p>`); para = []; } };
  const closeList = () => { if (list) { html.push(`</${list}>`); list = null; } };
  const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  const flushTable = () => {
    if (!table.length) return;
    const isDivider = (row) => /^\|?\s*:?-{2,}/.test(row.trim());
    const header = cells(table[0]);
    const align = table[1] && isDivider(table[1]) ? cells(table[1]).map((c) => (c.endsWith(":") ? "right" : "left")) : [];
    const body = table.slice(align.length ? 2 : 1).filter((r) => !isDivider(r));
    const td = (tag, c, i) => `<${tag}${align[i] === "right" ? ' class="num"' : ""}>${inline(c)}</${tag}>`;
    html.push(`<div class="table-wrap"><table><thead><tr>${header.map((c, i) => td("th", c, i)).join("")}</tr></thead><tbody>${
      body.map((r) => `<tr>${cells(r).map((c, i) => td("td", c, i)).join("")}</tr>`).join("")}</tbody></table></div>`);
    table = [];
  };

  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      if (inCode) { html.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`); code = []; inCode = false; }
      else { flushPara(); closeList(); flushTable(); inCode = true; }
      continue;
    }
    if (inCode) { code.push(line); continue; }
    if (line.trim().startsWith("|")) { flushPara(); closeList(); table.push(line); continue; }
    flushTable();
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
  flushTable();
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
   Chat view: messages with time, day dividers, feedback and attachments
   ===================================================================== */
const TIME_FMT = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
const FULL_FMT = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
let lastDayLabel = null;

function scrollToBottom(smooth = true) {
  scroller.scrollTo({ top: scroller.scrollHeight, behavior: smooth ? "smooth" : "auto" });
}
// While an answer streams in, follow it only if the reader is already near the bottom.
function followStream() {
  if (scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 160) scrollToBottom(false);
}

function dayLabel(ts) {
  const d = new Date(ts);
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === yesterday.toDateString()) return "Yesterday";
  return d.toLocaleDateString(undefined, {
    weekday: "short", day: "numeric", month: "short",
    year: d.getFullYear() === today.getFullYear() ? undefined : "numeric",
  });
}
function addDayDivider(ts) {
  if (!ts) return;
  const label = dayLabel(ts);
  if (label === lastDayLabel) return;
  lastDayLabel = label;
  const el = document.createElement("div");
  el.className = "day-divider";
  const span = document.createElement("span");
  span.textContent = label;
  el.append(span);
  messagesEl.append(el);
}
function timeElement(ts) {
  const t = document.createElement("time");
  t.className = "msg-time";
  t.dateTime = new Date(ts).toISOString();
  t.textContent = TIME_FMT.format(ts);
  t.title = FULL_FMT.format(ts);
  return t;
}

function renderUser(msg) {
  thread.classList.add("has-messages");
  addDayDivider(msg.createdAt);
  const el = document.createElement("article");
  el.className = "msg user";
  const wrap = document.createElement("div");
  wrap.className = "user-wrap";
  const content = document.createElement("div");
  const attachment = msg.data && msg.data.attachment;
  if (attachment) {
    content.className = "content attachment";
    content.innerHTML = ICONS.file;
    const info = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = attachment.filename;
    const kind = document.createElement("span");
    kind.textContent = attachment.content_type === "application/pdf" ? "PDF document" : "Image";
    info.append(name, kind);
    content.append(info);
  } else {
    content.className = "content";
    content.textContent = msg.text;
  }
  wrap.append(content);
  if (msg.createdAt) wrap.append(timeElement(msg.createdAt));
  el.append(wrap);
  messagesEl.append(el);
}

function renderAssistantShell(label = "") {
  const el = document.createElement("article");
  el.className = "msg assistant";
  el.innerHTML = `<div class="avatar">${ICONS.avatar}</div><div class="body"><div class="thinking" aria-label="LoanAssist is thinking"><i></i><i></i><i></i><span class="thinking-label"></span></div></div>`;
  el.querySelector(".thinking-label").textContent = label;
  messagesEl.append(el);
  return el;
}

function startStreamingView(el, meta) {
  const body = el.querySelector(".body");
  body.innerHTML = "";
  if (meta.requires_human_review) body.append(reviewBadge());
  const content = document.createElement("div");
  content.className = "content streaming";
  body.append(content);
  return content;
}

function reviewBadge() {
  const badge = document.createElement("div");
  badge.className = "review-badge";
  badge.textContent = "⚠ Sent for loan officer review";
  return badge;
}

// msg: { text, data, id, createdAt, feedback, error }
function fillAssistant(el, msg, chatId) {
  const data = msg.data || { answer: msg.text };
  const body = el.querySelector(".body");
  body.innerHTML = "";
  if (msg.error) el.classList.add("error");
  if (data.requires_human_review) body.append(reviewBadge());

  const content = document.createElement("div");
  content.className = "content";
  content.innerHTML = renderMarkdown(data.answer || msg.text || "");
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
  if (msg.createdAt) meta.append(timeElement(msg.createdAt));

  if (!msg.error) {
    const copy = iconButton(ICONS.copy, "Copy answer");
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(data.answer || msg.text);
        copy.innerHTML = ICONS.check;
        setTimeout(() => { copy.innerHTML = ICONS.copy; }, 1500);
      } catch { /* clipboard blocked */ }
    });
    meta.append(copy);
  }
  if (!msg.error && msg.id && chatId) meta.append(feedbackButtons(msg, chatId));
  body.append(meta);
}

function feedbackButtons(msg, chatId) {
  const group = document.createElement("span");
  group.className = "feedback";
  const up = iconButton(ICONS.thumbUp, "Good answer", "fb-up");
  const down = iconButton(ICONS.thumbDown, "Bad answer", "fb-down");
  const paint = () => {
    up.classList.toggle("active", msg.feedback === "up");
    down.classList.toggle("active", msg.feedback === "down");
    up.setAttribute("aria-pressed", String(msg.feedback === "up"));
    down.setAttribute("aria-pressed", String(msg.feedback === "down"));
  };
  const rate = async (rating) => {
    const previous = msg.feedback || null;
    const next = previous === rating ? null : rating; // clicking again clears it
    msg.feedback = next || undefined;
    paint();
    saveChats();
    try {
      await apiJson(`/chats/${chatId}/messages/${msg.id}/feedback`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rating: next }),
      });
      if (next) toast(next === "up" ? "Thanks for the feedback!" : "Thanks — we'll use this to improve.");
    } catch {
      msg.feedback = previous || undefined; // roll back
      paint();
      saveChats();
      toast("Couldn't save feedback. Please try again.");
    }
  };
  up.addEventListener("click", () => rate("up"));
  down.addEventListener("click", () => rate("down"));
  paint();
  group.append(up, down);
  return group;
}

function renderAssistant(msg, chatId) {
  addDayDivider(msg.createdAt);
  const el = renderAssistantShell();
  fillAssistant(el, msg, chatId);
}

let toastTimer = null;
function toast(text) {
  const el = $("#toast");
  el.textContent = text;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2200);
}

async function openChat(id) {
  const chat = getChat(id);
  if (!chat) return;
  currentId = id;
  messagesEl.innerHTML = "";
  lastDayLabel = null;
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
    if (m.role === "user") renderUser(m);
    else renderAssistant(m, chat.id);
  }
  if (chat.pending) renderAssistantShell();
  scrollToBottom(false);
  input.focus();
}

function newChat() {
  currentId = null;
  messagesEl.innerHTML = "";
  lastDayLabel = null;
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
   Sending (streamed answers)
   ===================================================================== */
function updateSendState() {
  sendButton.disabled = busy || !input.value.trim();
  attachButton.disabled = busy;
}
function autoGrow() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 200)}px`;
  input.style.overflowY = input.scrollHeight > 200 ? "auto" : "hidden";
}

// Start (or reuse) the chat that a new message belongs to.
function ensureChat(title) {
  let chat = getChat(currentId);
  const isNew = !chat;
  if (!chat) {
    const now = Date.now();
    chat = { id: crypto.randomUUID(), title, messages: [], createdAt: now, updatedAt: now };
    chats.push(chat);
    currentId = chat.id;
    chatTitleEl.textContent = chat.title;
    document.title = `${chat.title} · LoanAssist`;
  }
  return { chat, isNew };
}

function finishReply(chatId, shell, reply) {
  // The chat may have been deleted or switched away from while waiting.
  const target = getChat(chatId);
  if (target && target.messages) {
    target.messages.push(reply);
    target.updatedAt = Date.now();
    delete target.pending;
    saveChats();
  }
  if (currentId === chatId && shell.isConnected) {
    fillAssistant(shell, reply, chatId);
    followStream();
  } else if (currentId === chatId) {
    openChat(chatId);
  }
  busy = false;
  updateSendState();
  renderHistory();
  input.focus();
}

// While text is still arriving, hide a "**" whose closing pair hasn't arrived yet.
function tidyPartial(text) {
  const pairs = (text.match(/\*\*/g) || []).length;
  return pairs % 2 ? text.replace(/\*\*(?![\s\S]*\*\*)/, "") : text;
}

// Reads the NDJSON stream: {"type":"meta"} -> many {"type":"delta"} -> {"type":"done"}.
async function streamReply(body, shell) {
  const response = await apiFetch("/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok || !response.body) {
    const error = new Error(`Request failed (${response.status})`);
    error.fallback = true;
    throw error;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let meta = {};
  let text = "";
  let done = null;
  let contentEl = null;
  let painting = false;
  const paint = () => {
    painting = false;
    if (contentEl && shell.isConnected) { contentEl.innerHTML = renderMarkdown(tidyPartial(text)); followStream(); }
  };
  const handle = (line) => {
    let event;
    try { event = JSON.parse(line); } catch { const e = new Error("Unexpected response"); e.fallback = !text; throw e; }
    if (event.type === "meta") meta = event;
    else if (event.type === "delta") {
      if (!contentEl && shell.isConnected) contentEl = startStreamingView(shell, meta);
      text += event.text;
      if (!painting) { painting = true; requestAnimationFrame(paint); }
    } else if (event.type === "done") done = event;
    else if (event.type === "error") throw new Error(event.detail || "Something went wrong");
  };
  for (;;) {
    const { value, done: ended } = await reader.read();
    if (ended) break;
    buffer += decoder.decode(value, { stream: true });
    let newline;
    while ((newline = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (line) handle(line);
    }
  }
  if (buffer.trim()) handle(buffer.trim());
  if (!text) { const e = new Error("Empty answer"); e.fallback = true; throw e; }
  return {
    role: "assistant",
    text,
    data: { ...meta, answer: text },
    id: done && done.message_id,
    createdAt: (done && done.created_at) || Date.now(),
  };
}

async function send(message) {
  if (busy || !message) return;
  busy = true;
  const { chat, isNew } = ensureChat(makeTitle(message));
  const chatId = chat.id;
  const userMsg = { role: "user", text: message, createdAt: Date.now() };
  chat.messages.push(userMsg);
  chat.updatedAt = Date.now();
  chat.pending = true;
  saveChats();
  renderHistory();

  renderUser(userMsg);
  const shell = renderAssistantShell();
  scrollToBottom();
  input.value = "";
  autoGrow();
  updateSendState();

  // The chat id is also the server-side history key, so follow-ups keep context.
  const body = { message, session_id: chatId, title: isNew ? chat.title : undefined };
  let reply;
  try {
    reply = await streamReply(body, shell);
  } catch (error) {
    if (error.fallback) {
      // Streaming unavailable: fall back to the regular (non-streamed) endpoint.
      try {
        const data = await apiJson("/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        reply = { role: "assistant", text: data.answer, data, id: data.message_id, createdAt: data.created_at || Date.now() };
      } catch (inner) {
        reply = { role: "assistant", text: `Sorry, I couldn't reach LoanAssist. ${inner.message}`, error: true, createdAt: Date.now() };
      }
    } else {
      reply = { role: "assistant", text: `Sorry, I couldn't reach LoanAssist. ${error.message}`, error: true, createdAt: Date.now() };
    }
  }
  finishReply(chatId, shell, reply);
}

/* =====================================================================
   Document upload: salary slip / bank statement -> income + eligibility
   ===================================================================== */
const MAX_UPLOAD = 4 * 1024 * 1024;
const UPLOAD_TYPES = ["application/pdf", "image/png", "image/jpeg"];

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1]);
    reader.onerror = () => reject(new Error("Could not read the file"));
    reader.readAsDataURL(file);
  });
}

async function uploadDocument(file) {
  if (busy || !file) return;
  if (!UPLOAD_TYPES.includes(file.type)) { toast("Please upload a PDF, PNG or JPG file."); return; }
  if (file.size > MAX_UPLOAD) { toast("Please upload a file smaller than 4 MB."); return; }
  busy = true;
  const title = /statement/i.test(file.name) ? "Bank statement check" : "Salary slip check";
  const { chat, isNew } = ensureChat(title);
  const chatId = chat.id;
  const userMsg = { role: "user", text: `📎 ${file.name}`, createdAt: Date.now(), data: { attachment: { filename: file.name, content_type: file.type } } };
  chat.messages.push(userMsg);
  chat.updatedAt = Date.now();
  chat.pending = true;
  saveChats();
  renderHistory();
  renderUser(userMsg);
  const shell = renderAssistantShell("Reading your document…");
  scrollToBottom();
  updateSendState();

  let reply;
  try {
    const data = await apiJson("/documents", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: chatId,
        title: isNew ? title : undefined,
        filename: file.name,
        content_type: file.type,
        data_base64: await fileToBase64(file),
      }),
    });
    const [savedUser, savedReply] = data.messages;
    Object.assign(userMsg, { id: savedUser.id, createdAt: savedUser.createdAt });
    reply = savedReply;
  } catch (error) {
    reply = { role: "assistant", text: `Sorry, I couldn't read that document. ${error.message}`, error: true, createdAt: Date.now() };
  }
  finishReply(chatId, shell, reply);
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
document.querySelectorAll(".chip").forEach((chip) => chip.addEventListener("click", () => {
  if (chip.dataset.action === "upload") fileInput.click();
  else send(chip.dataset.prompt);
}));
$("#newChat").addEventListener("click", () => { if (!busy) newChat(); });
$("#topNewChat").addEventListener("click", () => { if (!busy) newChat(); });
searchInput.addEventListener("input", renderHistory);
document.addEventListener("keydown", (e) => {
  // Ctrl/Cmd + K focuses search, Ctrl/Cmd + Shift + O starts a new chat.
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openSidebar(); searchInput.focus(); }
  if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === "o") { e.preventDefault(); if (!busy) newChat(); }
});

attachButton.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => { const file = fileInput.files[0]; fileInput.value = ""; uploadDocument(file); });
// Drag a file onto the message box to upload it.
const composerWrap = $(".composer-wrap");
composerWrap.addEventListener("dragover", (e) => { e.preventDefault(); composerWrap.classList.add("dragging"); });
composerWrap.addEventListener("dragleave", () => composerWrap.classList.remove("dragging"));
composerWrap.addEventListener("drop", (e) => { e.preventDefault(); composerWrap.classList.remove("dragging"); uploadDocument(e.dataTransfer.files[0]); });

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
