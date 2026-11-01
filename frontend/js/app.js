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
  note: '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M4 5h16v11H8l-4 4V5Z M8 9h8 M8 12h5" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round" stroke-linecap="round"/></svg>',
  stop: '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2.5" fill="currentColor"/></svg>',
  send: '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" d="M12 19V5M5 12l7-7 7 7"/></svg>',
  regen: '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  page: '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
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
    const unit = q.match(/(\d+(?:\.\d+)?)\s*(crores?|cr|lakhs?|lacs?|lac)\b/i);
    const amount = unit
      ? Math.round(Number(unit[1]) * (/^c/i.test(unit[2]) ? 1e7 : 1e5))
      : (q.replace(/,/g, "").match(/\b\d{4,}\b/) || [])[0];
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
function inlineFmt(text, citations = 0) {
  let html = escapeHtml(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
    .replace(/(^|[\s(])_([^_\s][^_]*)_(?=$|[\s.,;:!?)])/g, "$1<em>$2</em>");
  if (!citations) {
    // No sources behind this answer: drop any stray [n] markers instead of showing fake citations.
    html = html.replace(/\s?\[\d{1,2}(?:\s*,\s*\d{1,2})*\]/g, "");
  } else {
    // "[1]" or "[1, 2]" -> small clickable numbers that open the matching source.
    html = html.replace(/\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\]/g, (match, nums) => {
      const list = nums.split(",").map((n) => n.trim()).filter((n) => +n >= 1 && +n <= citations);
      return list.length ? list.map((n) => `<sup class="cite" data-cite="${n}" title="Source ${n}">${n}</sup>`).join("") : match;
    });
  }
  return html;
}
function renderMarkdown(source, citations = 0) {
  const inline = (t) => inlineFmt(t, citations);
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
  const foot = document.createElement("div");
  foot.className = "user-foot";
  if (!attachment && msg.id) {
    const edit = iconButton(ICONS.edit, "Edit question", "edit-btn");
    edit.addEventListener("click", () => startEdit(el, msg));
    foot.append(edit);
  }
  if (msg.createdAt) foot.append(timeElement(msg.createdAt));
  wrap.append(foot);
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

// msg: { text, data, id, createdAt, feedback, feedbackComment, error }
function fillAssistant(el, msg, chatId) {
  const data = msg.data || { answer: msg.text };
  const body = el.querySelector(".body");
  body.innerHTML = "";
  if (msg.error) el.classList.add("error");
  if (data.requires_human_review) body.append(reviewBadge());

  const sources = (data.sources || []).filter((s) => s.uri);
  const content = document.createElement("div");
  content.className = "content";
  content.innerHTML = renderMarkdown(data.answer || msg.text || "", sources.length);
  body.append(content);
  if (sources.length) body.append(sourceList(sources, content));
  if (data.stopped) {
    const tag = document.createElement("div");
    tag.className = "stopped-tag";
    tag.textContent = "Stopped";
    body.append(tag);
  }
  if (data.emi && !msg.error) body.append(emiCard(data.emi));

  const meta = document.createElement("div");
  meta.className = "meta";
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
    if (chatId && isLastAssistant(chatId, msg)) {
      const regen = regenerateButton(chatId);
      regen.classList.add("regen-btn");
      meta.append(regen);
    }
  }
  body.append(meta);
  if (!msg.error && msg.id && chatId) {
    const form = feedbackForm(msg, chatId);
    meta.append(feedbackButtons(msg, chatId, form));
    body.append(form.el);
  }
  if (!msg.error && msg.suggestions && msg.suggestions.length && chatId && isLastAssistant(chatId, msg)) {
    body.append(suggestionChips(msg.suggestions));
  }
}

/* ---------- Interactive EMI card: sliders, chart, year-by-year table ---------- */
const rupees = (n) => "₹" + Math.round(n).toLocaleString("en-IN");
function shortRupees(n) {
  if (n >= 1e7) return `₹${+(n / 1e7).toFixed(2)} Cr`;
  if (n >= 1e5) return `₹${+(n / 1e5).toFixed(2)} L`;
  return rupees(n);
}
function emiFor(principal, rate, months) {
  const r = rate / 1200;
  if (!r) return principal / months;
  const g = Math.pow(1 + r, months);
  return (principal * r * g) / (g - 1);
}
// Year-by-year split of what you pay: [{ year, principal, interest, balance }]
function amortizeByYear(principal, rate, months) {
  const emi = emiFor(principal, rate, months);
  const r = rate / 1200;
  const years = [];
  let balance = principal;
  for (let m = 1; m <= months; m++) {
    const interest = balance * r;
    const paid = Math.min(emi - interest, balance);
    balance = Math.max(balance - paid, 0);
    const y = Math.ceil(m / 12) - 1;
    years[y] = years[y] || { year: y + 1, principal: 0, interest: 0, balance: 0 };
    years[y].principal += paid;
    years[y].interest += interest;
    years[y].balance = balance;
  }
  return years;
}

function emiCard(initial) {
  const state = {
    principal: Math.min(Math.max(Math.round(initial.principal / 50000) * 50000, 100000), 50000000),
    rate: Math.min(Math.max(initial.annual_rate, 5), 20),
    years: Math.min(Math.max(Math.round(initial.months / 12), 1), 30),
  };
  const card = document.createElement("section");
  card.className = "emi-card";
  card.innerHTML = `
    <div class="emi-head">
      <div><div class="emi-label">Monthly EMI</div><div class="emi-value" data-k="emi"></div></div>
      <div class="emi-stats">
        <div><span>Total interest</span><b data-k="interest"></b></div>
        <div><span>Total payment</span><b data-k="total"></b></div>
      </div>
    </div>
    <div class="emi-sliders">
      <label><span>Loan amount <b data-k="principalLabel"></b></span>
        <input type="range" data-s="principal" min="100000" max="50000000" step="50000" aria-label="Loan amount"></label>
      <label><span>Interest rate <b data-k="rateLabel"></b></span>
        <input type="range" data-s="rate" min="5" max="20" step="0.05" aria-label="Interest rate"></label>
      <label><span>Tenure <b data-k="yearsLabel"></b></span>
        <input type="range" data-s="years" min="1" max="30" step="1" aria-label="Tenure in years"></label>
    </div>
    <div class="emi-chart">
      <div class="emi-chart-head">
        <span>What you pay each year</span>
        <span class="emi-legend"><i class="sw principal"></i>Principal <i class="sw interest"></i>Interest</span>
      </div>
      <div class="emi-plot"><svg viewBox="0 0 600 170" preserveAspectRatio="none" role="img" aria-label="Principal and interest paid per year"></svg><div class="emi-tip" hidden></div></div>
    </div>
    <details class="emi-table"><summary>Year-by-year repayment table</summary><div class="table-wrap"><table>
      <thead><tr><th>Year</th><th class="num">Principal</th><th class="num">Interest</th><th class="num">Balance left</th></tr></thead>
      <tbody></tbody></table></div></details>`;
  const $k = (k) => card.querySelector(`[data-k="${k}"]`);
  const svg = card.querySelector("svg");
  const tip = card.querySelector(".emi-tip");
  card.querySelectorAll("input[type=range]").forEach((input) => {
    input.value = state[input.dataset.s];
    input.addEventListener("input", () => { state[input.dataset.s] = +input.value; draw(); });
  });

  function draw() {
    const months = state.years * 12;
    const emi = emiFor(state.principal, state.rate, months);
    const total = emi * months;
    $k("emi").textContent = rupees(emi);
    $k("interest").textContent = shortRupees(total - state.principal);
    $k("total").textContent = shortRupees(total);
    $k("principalLabel").textContent = shortRupees(state.principal);
    $k("rateLabel").textContent = `${state.rate.toFixed(2)}%`;
    $k("yearsLabel").textContent = `${state.years} yr${state.years > 1 ? "s" : ""}`;

    const rows = amortizeByYear(state.principal, state.rate, months);
    // Stacked bars: principal at the baseline, interest on top, 2px gap between segments.
    const W = 600, H = 170, top = 8, bottom = 18;
    const max = Math.max(...rows.map((r) => r.principal + r.interest));
    const slot = W / rows.length;
    const bw = Math.max(Math.min(slot * 0.68, 26), 3);
    const scale = (v) => (v / max) * (H - top - bottom);
    let html = `<line x1="0" x2="${W}" y1="${H - bottom}" y2="${H - bottom}" class="emi-axis"/>`;
    rows.forEach((r, i) => {
      const x = i * slot + (slot - bw) / 2;
      const hp = scale(r.principal), hi = scale(r.interest);
      const yP = H - bottom - hp, yI = yP - 2 - hi;
      html += `<g class="emi-bar" data-i="${i}">
        <rect class="hit" x="${i * slot}" y="0" width="${slot}" height="${H}"/>
        <rect class="principal" x="${x}" y="${yP}" width="${bw}" height="${Math.max(hp, 0)}" rx="2"/>
        <rect class="interest" x="${x}" y="${yI}" width="${bw}" height="${Math.max(hi, 0)}" rx="${Math.min(4, bw / 2)}"/>
      </g>`;
      const every = rows.length > 20 ? 5 : rows.length > 10 ? 2 : 1;
      if ((i + 1) % every === 0 || i === 0) html += `<text x="${i * slot + slot / 2}" y="${H - 4}" class="emi-tick">${r.year}</text>`;
    });
    svg.innerHTML = html;
    svg.querySelectorAll(".emi-bar").forEach((g) => {
      const r = rows[+g.dataset.i];
      const show = () => {
        tip.hidden = false;
        tip.innerHTML = `<b>Year ${r.year}</b><span><i class="sw principal"></i>Principal ${rupees(r.principal)}</span><span><i class="sw interest"></i>Interest ${rupees(r.interest)}</span><span>Balance ${rupees(r.balance)}</span>`;
        const pct = ((+g.dataset.i + 0.5) / rows.length) * 100;
        tip.style.left = `${Math.min(Math.max(pct, 18), 82)}%`;
        g.classList.add("on");
      };
      const hide = () => { tip.hidden = true; g.classList.remove("on"); };
      g.addEventListener("mouseenter", show);
      g.addEventListener("mouseleave", hide);
      g.addEventListener("touchstart", show, { passive: true });
    });
    card.querySelector("tbody").innerHTML = rows
      .map((r) => `<tr><td>${r.year}</td><td class="num">${rupees(r.principal)}</td><td class="num">${rupees(r.interest)}</td><td class="num">${rupees(r.balance)}</td></tr>`)
      .join("");
  }
  draw();
  return card;
}

/* ---------- Sources: numbered list that matches the [n] citations ---------- */
function sourceTitle(s) {
  if (s.title) return s.title;
  const file = decodeURIComponent(s.uri.split("/").pop() || "Document").replace(/\.[^.]+$/, "");
  return file.replace(/[-_]+/g, " ").replace(/^./, (c) => c.toUpperCase());
}

function sourceList(sources, content) {
  const box = document.createElement("div");
  box.className = "sources";
  const head = document.createElement("div");
  head.className = "sources-head";
  head.textContent = `Sources · ${sources.length}`;
  box.append(head);
  const list = document.createElement("ol");
  sources.forEach((s, i) => {
    const item = document.createElement("li");
    item.className = "source-item";
    item.dataset.cite = String(i + 1);
    const top = document.createElement("button");
    top.type = "button";
    top.className = "source-top";
    top.setAttribute("aria-expanded", "false");
    const num = document.createElement("span");
    num.className = "source-num";
    num.textContent = i + 1;
    const title = document.createElement("span");
    title.className = "source-title";
    title.textContent = sourceTitle(s);
    const file = document.createElement("span");
    file.className = "source-file";
    // "Documents required · p. 3 · home-loan.pdf"
    const where = [];
    if (s.sections && s.sections.length) where.push(s.sections[0]);
    if (s.pages && s.pages.length) where.push(`p. ${s.pages.join(", ")}`);
    where.push(decodeURIComponent(s.uri.split("/").pop() || ""));
    file.textContent = where.join(" · ");
    file.title = [...(s.sections || []), ...(s.pages || []).map((p) => `page ${p}`)].join(" · ");
    top.append(num, title, file);
    const excerpt = document.createElement("p");
    excerpt.className = "source-excerpt";
    excerpt.textContent = (s.excerpt || "").replace(/\s+/g, " ").trim();
    top.addEventListener("click", () => {
      const open = item.classList.toggle("open");
      top.setAttribute("aria-expanded", String(open));
    });
    const view = document.createElement("button");
    view.type = "button";
    view.className = "source-view";
    view.innerHTML = `${ICONS.page}<span>${/\.pdf$/i.test(s.uri) ? "View page" : "View"}</span>`;
    view.addEventListener("click", () => openSourcePreview(s, i + 1));
    item.append(top, excerpt, view);
    list.append(item);
  });
  box.append(list);
  // Clicking an inline [n] opens and highlights the matching source.
  content.addEventListener("click", (e) => {
    const cite = e.target.closest(".cite");
    if (!cite) return;
    const item = list.querySelector(`[data-cite="${cite.dataset.cite}"]`);
    if (!item) return;
    item.classList.add("open", "flash");
    item.querySelector(".source-top").setAttribute("aria-expanded", "true");
    setTimeout(() => item.classList.remove("flash"), 1200);
    const n = Number(cite.dataset.cite);
    openSourcePreview(sources[n - 1], n);
  });
  return box;
}

/* ---------- Feedback: 👍 / 👎 plus a written comment for the team ---------- */
function sendFeedback(msg, chatId, body) {
  return apiJson(`/chats/${chatId}/messages/${msg.id}/feedback`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function feedbackForm(msg, chatId) {
  const el = document.createElement("form");
  el.className = "feedback-form";
  el.hidden = true;
  el.innerHTML = `
    <label class="feedback-label"></label>
    <textarea rows="2" maxlength="1000" placeholder="What was wrong or missing? Your note goes to the LoanAssist team."></textarea>
    <div class="feedback-actions">
      <span class="feedback-hint">Don't include account numbers, PAN or other personal IDs.</span>
      <button type="button" class="link-btn cancel">Cancel</button>
      <button type="submit" class="small-btn">Send feedback</button>
    </div>`;
  const label = el.querySelector(".feedback-label");
  const box = el.querySelector("textarea");
  const submit = el.querySelector('button[type="submit"]');
  const open = (reason) => {
    label.textContent = reason === "down" ? "Sorry about that — what went wrong?" : "Share feedback on this answer";
    el.hidden = false;
    box.focus();
    followStream();
  };
  const close = () => { el.hidden = true; box.value = ""; };
  el.querySelector(".cancel").addEventListener("click", close);
  el.addEventListener("submit", async (e) => {
    e.preventDefault();
    const comment = box.value.trim();
    if (!comment) { box.focus(); return; }
    submit.disabled = true;
    try {
      await sendFeedback(msg, chatId, { rating: msg.feedback || null, comment });
      msg.feedbackComment = comment;
      saveChats();
      close();
      el.dispatchEvent(new CustomEvent("sent"));
      toast("Feedback sent to the LoanAssist team. Thank you!");
    } catch {
      toast("Couldn't send feedback. Please try again.");
    } finally {
      submit.disabled = false;
    }
  });
  return { el, open };
}

function feedbackButtons(msg, chatId, form) {
  const group = document.createElement("span");
  group.className = "feedback";
  const up = iconButton(ICONS.thumbUp, "Good answer", "fb-up");
  const down = iconButton(ICONS.thumbDown, "Bad answer", "fb-down");
  const write = document.createElement("button");
  write.type = "button";
  write.className = "feedback-write";
  const paint = () => {
    up.classList.toggle("active", msg.feedback === "up");
    down.classList.toggle("active", msg.feedback === "down");
    up.setAttribute("aria-pressed", String(msg.feedback === "up"));
    down.setAttribute("aria-pressed", String(msg.feedback === "down"));
    write.innerHTML = `${ICONS.note}<span>${msg.feedbackComment ? "Feedback sent" : "Give feedback"}</span>`;
    write.classList.toggle("sent", Boolean(msg.feedbackComment));
  };
  const rate = async (rating) => {
    const previous = msg.feedback || null;
    const next = previous === rating ? null : rating; // clicking again clears it
    msg.feedback = next || undefined;
    paint();
    saveChats();
    if (next === "down") form.open("down"); // ask what went wrong
    try {
      await sendFeedback(msg, chatId, { rating: next });
      if (next === "up") toast("Thanks for the feedback!");
    } catch {
      msg.feedback = previous || undefined; // roll back
      paint();
      saveChats();
      toast("Couldn't save feedback. Please try again.");
    }
  };
  up.addEventListener("click", () => rate("up"));
  down.addEventListener("click", () => rate("down"));
  write.addEventListener("click", () => form.open("note"));
  form.el.addEventListener("sent", paint);
  paint();
  group.append(up, down, write);
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
   Sending (streamed answers, stop, edit/regenerate)
   ===================================================================== */
let streamController = null;   // set while an answer streams, so Stop can abort it

function updateSendState() {
  const streaming = Boolean(streamController);
  sendButton.classList.toggle("stop", streaming);
  sendButton.innerHTML = streaming ? ICONS.stop : ICONS.send;
  sendButton.setAttribute("aria-label", streaming ? "Stop answering" : "Send message");
  sendButton.title = streaming ? "Stop" : "Send";
  sendButton.disabled = streaming ? false : (busy || !input.value.trim());
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
  streamController = null;
  busy = false;
  if (currentId === chatId) openChat(chatId);   // re-render so only the newest answer has Regenerate/suggestions
  updateSendState();
  renderHistory();
  input.focus();
}

// While text is still arriving, hide a "**" whose closing pair hasn't arrived yet.
function tidyPartial(text) {
  const pairs = (text.match(/\*\*/g) || []).length;
  return pairs % 2 ? text.replace(/\*\*(?![\s\S]*\*\*)/, "") : text;
}

// Reads the NDJSON stream: meta -> many delta -> done -> (suggestions).
async function streamReply(body, shell, signal, userMsg) {
  const response = await apiFetch("/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
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
  let suggestions = [];
  let contentEl = null;
  let painting = false;
  const paint = () => {
    painting = false;
    if (contentEl && shell.isConnected) { contentEl.innerHTML = renderMarkdown(tidyPartial(text), (meta.sources || []).length); followStream(); }
  };
  const handle = (line) => {
    let event;
    try { event = JSON.parse(line); } catch { const e = new Error("Unexpected response"); e.fallback = !text; throw e; }
    if (event.type === "meta") meta = event;
    else if (event.type === "delta") {
      if (!contentEl && shell.isConnected) contentEl = startStreamingView(shell, meta);
      text += event.text;
      if (!painting) { painting = true; requestAnimationFrame(paint); }
    } else if (event.type === "done") {
      done = event;
      if (userMsg && event.user_message_id) userMsg.id = event.user_message_id;
      streamController = null;   // answer complete: Stop turns back into Send while suggestions load
      updateSendState();
    } else if (event.type === "suggestions") suggestions = event.items || [];
    else if (event.type === "error") throw new Error(event.detail || "Something went wrong");
  };
  const result = () => ({
    role: "assistant",
    text,
    data: { ...meta, answer: text },
    id: done && done.message_id,
    createdAt: (done && done.created_at) || Date.now(),
    suggestions,
  });
  try {
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
  } catch (error) {
    if (error.name === "AbortError") {
      // Stopped by the user: keep what arrived so far.
      const partial = result();
      partial.data.stopped = true;
      if (!text) partial.text = partial.data.answer = "_Stopped before the answer started._";
      return partial;
    }
    throw error;
  }
  if (buffer.trim()) handle(buffer.trim());
  if (!text) { const e = new Error("Empty answer"); e.fallback = true; throw e; }
  return result();
}

async function send(message, { replaceFrom } = {}) {
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

  // Hide earlier follow-up chips / regenerate buttons while the new answer comes in.
  messagesEl.querySelectorAll(".followups, .regen-btn").forEach((n) => n.remove());
  renderUser(userMsg);
  const shell = renderAssistantShell();
  scrollToBottom();
  input.value = "";
  autoGrow();
  streamController = new AbortController();
  updateSendState();

  // The chat id is also the server-side history key, so follow-ups keep context.
  const body = { message, session_id: chatId, title: isNew ? chat.title : undefined, replace_from: replaceFrom, suggest: true };
  let reply;
  try {
    reply = await streamReply(body, shell, streamController.signal, userMsg);
  } catch (error) {
    if (error.fallback) {
      // Streaming unavailable: fall back to the regular (non-streamed) endpoint.
      try {
        const data = await apiJson("/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        if (data.user_message_id) userMsg.id = data.user_message_id;
        reply = { role: "assistant", text: data.answer, data, id: data.message_id, createdAt: data.created_at || Date.now(), suggestions: data.suggestions || [] };
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
   Document upload: small files inline, big files via S3 + SQS (background)
   ===================================================================== */
const MAX_INLINE_UPLOAD = 3 * 1024 * 1024;   // above this, the background pipeline is used
const MAX_UPLOAD = 20 * 1024 * 1024;
const UPLOAD_TYPES = ["application/pdf", "image/png", "image/jpeg"];

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1]);
    reader.onerror = () => reject(new Error("Could not read the file"));
    reader.readAsDataURL(file);
  });
}

function uploadTitle(file) {
  return /statement/i.test(file.name) ? "Bank statement check" : "Document check";
}

async function uploadDocument(file) {
  if (busy || !file) return;
  if (!UPLOAD_TYPES.includes(file.type)) { toast("Please upload a PDF, PNG or JPG file."); return; }
  const big = file.size > MAX_INLINE_UPLOAD;
  if (file.size > MAX_UPLOAD) { toast("Please upload a file smaller than 20 MB."); return; }
  if (big && !REMOTE) { toast("Local mode supports files up to 3 MB."); return; }
  if (big) { uploadLargeDocument(file); return; }

  busy = true;
  const title = uploadTitle(file);
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
      body: JSON.stringify({ session_id: chatId, title: isNew ? title : undefined, filename: file.name, content_type: file.type, data_base64: await fileToBase64(file) }),
    });
    const [savedUser, savedReply] = data.messages;
    Object.assign(userMsg, { id: savedUser.id, createdAt: savedUser.createdAt });
    reply = savedReply;
  } catch (error) {
    reply = { role: "assistant", text: `Sorry, I couldn't read that document. ${error.message}`, error: true, createdAt: Date.now() };
  }
  finishReply(chatId, shell, reply);
  if (panelMode === "checklist") openChecklist();
}

// Big file: presigned PUT to S3 -> queue -> poll until the worker is done (or the file lands in the DLQ).
async function uploadLargeDocument(file) {
  busy = true;
  const title = uploadTitle(file);
  const { chat, isNew } = ensureChat(title);
  const chatId = chat.id;
  const userMsg = { role: "user", text: `📎 ${file.name}`, createdAt: Date.now(), data: { attachment: { filename: file.name, content_type: file.type } } };
  chat.messages.push(userMsg);
  chat.pending = true;
  saveChats();
  renderHistory();
  renderUser(userMsg);
  const shell = renderAssistantShell(`Uploading ${(file.size / 1048576).toFixed(1)} MB…`);
  const label = () => shell.querySelector(".thinking-label");
  scrollToBottom();
  updateSendState();
  try {
    const up = await apiJson("/uploads", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: chatId, title: isNew ? title : undefined, filename: file.name, content_type: file.type, size: file.size }),
    });
    const put = await fetch(up.upload_url, { method: "PUT", headers: { "Content-Type": up.content_type }, body: file });
    if (!put.ok) throw new Error(`Upload failed (${put.status})`);
    const queued = await apiJson(`/chats/${chatId}/documents/${up.doc_id}/process`, { method: "POST" });
    Object.assign(userMsg, { id: queued.message.id, createdAt: queued.message.createdAt });
    busy = false;   // the user can keep chatting while the worker reads the file
    updateSendState();
    if (label()) label().textContent = "Queued — reading in the background. You can keep chatting.";
    toast("Big file queued. It's processed in the background.");
    const started = Date.now();
    for (;;) {
      await new Promise((r) => setTimeout(r, 4000));
      const docs = await apiJson(`/chats/${chatId}/documents`);
      const doc = docs.find((d) => d.doc_id === up.doc_id);
      if (!doc) break;
      if (label()) label().textContent = `${STATUS_TEXT[doc.status] || doc.status}${doc.attempts > 1 ? ` (attempt ${doc.attempts} of 3)` : ""} — you can keep chatting.`;
      if (doc.status === "done" || doc.status === "failed" || Date.now() - started > 20 * 60 * 1000) break;
    }
    const target = getChat(chatId);
    if (target) { target.messages = undefined; delete target.pending; }   // reload this chat from the server
    if (currentId === chatId) await openChat(chatId);
  } catch (error) {
    finishReply(chatId, shell, { role: "assistant", text: `Sorry, the upload failed. ${error.message}`, error: true, createdAt: Date.now() });
    return;
  } finally {
    busy = false;
    updateSendState();
  }
  if (panelMode === "checklist") openChecklist();
}

/* =====================================================================
   Edit a question, regenerate an answer, follow-up suggestions
   ===================================================================== */
function isLastAssistant(chatId, msg) {
  const chat = getChat(chatId);
  if (!chat || !chat.messages || !chat.messages.length) return false;
  return chat.messages[chat.messages.length - 1] === msg;
}

function lastUserMessage(chat) {
  for (let i = chat.messages.length - 1; i >= 0; i--) if (chat.messages[i].role === "user") return chat.messages[i];
  return null;
}

// Drop `msg` and everything after it (locally), then ask `text` again with replace_from on the server.
function resend(chatId, msg, text) {
  const chat = getChat(chatId);
  if (!chat || busy) return;
  const index = chat.messages.indexOf(msg);
  if (index < 0) return;
  chat.messages = chat.messages.slice(0, index);
  saveChats();
  openChat(chatId).then(() => send(text, { replaceFrom: msg.id || undefined }));
}

function regenerateButton(chatId) {
  const btn = iconButton(ICONS.regen, "Regenerate answer");
  btn.addEventListener("click", () => {
    const chat = getChat(chatId);
    const question = chat && lastUserMessage(chat);
    if (!question) { toast("This answer can't be regenerated."); return; }
    // No id: the answer was stopped before the server saved the turn, so just ask again.
    resend(chatId, question, question.text);
  });
  return btn;
}

function startEdit(el, msg) {
  if (busy) return;
  const chatId = currentId;
  const wrap = el.querySelector(".user-wrap");
  const editor = document.createElement("form");
  editor.className = "edit-form";
  editor.innerHTML = `<textarea rows="2" maxlength="4000"></textarea>
    <div class="feedback-actions"><span class="feedback-hint">Answers after this question will be replaced.</span>
    <button type="button" class="link-btn cancel">Cancel</button><button type="submit" class="small-btn">Send</button></div>`;
  const box = editor.querySelector("textarea");
  box.value = msg.text;
  wrap.replaceWith(editor);
  box.focus();
  box.setSelectionRange(box.value.length, box.value.length);
  editor.querySelector(".cancel").addEventListener("click", () => editor.replaceWith(wrap));
  editor.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = box.value.trim();
    if (!text) return;
    resend(chatId, msg, text);
  });
  box.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); editor.requestSubmit(); }
    if (e.key === "Escape") editor.replaceWith(wrap);
  });
}

function suggestionChips(items) {
  const row = document.createElement("div");
  row.className = "followups";
  for (const text of items) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "followup";
    chip.textContent = text;
    chip.addEventListener("click", () => send(text));
    row.append(chip);
  }
  return row;
}

/* =====================================================================
   Side panel: source preview (PDF page + highlight) and application checklist
   ===================================================================== */
const sidePanel = $("#sidePanel");
const panelTitle = $("#panelTitle");
const panelBody = $("#panelBody");
let panelMode = null;

function openPanel(mode, title) {
  panelMode = mode;
  panelTitle.textContent = title;
  panelBody.innerHTML = "";
  app.classList.add("panel-open");
  sidePanel.setAttribute("aria-hidden", "false");
  return panelBody;
}
function closePanel() {
  panelMode = null;
  app.classList.remove("panel-open");
  sidePanel.setAttribute("aria-hidden", "true");
}
$("#closePanel").addEventListener("click", closePanel);
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && panelMode) closePanel(); });

let pdfjsReady = null;
function loadPdfJs() {
  pdfjsReady ||= new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js";
    s.onload = () => {
      window.pdfjsLib.GlobalWorkerOptions.workerSrc = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
      resolve(window.pdfjsLib);
    };
    s.onerror = () => { pdfjsReady = null; reject(new Error("Could not load the PDF viewer")); };
    document.head.append(s);
  });
  return pdfjsReady;
}

const pdfCache = new Map();
async function loadSourcePdf(uri) {
  if (!pdfCache.has(uri)) {
    pdfCache.set(uri, (async () => {
      const [pdfjsLib, response] = await Promise.all([loadPdfJs(), apiFetch(`/sources/file?uri=${encodeURIComponent(uri)}`)]);
      if (!response.ok) throw new Error(`Could not open the document (${response.status})`);
      return pdfjsLib.getDocument({ data: new Uint8Array(await response.arrayBuffer()) }).promise;
    })().catch((e) => { pdfCache.delete(uri); throw e; }));
  }
  return pdfCache.get(uri);
}

const norm = (t) => t.toLowerCase().replace(/[^a-z0-9%₹.]+/g, " ").replace(/\s+/g, " ").trim();

async function openSourcePreview(source, number) {
  const body = openPanel("source", `[${number}] ${sourceTitle(source)}`);
  const isPdf = /\.pdf$/i.test(source.uri);
  const pages = (source.pages && source.pages.length) ? source.pages : [1];
  const info = document.createElement("div");
  info.className = "preview-info";
  info.textContent = isPdf ? `${decodeURIComponent(source.uri.split("/").pop())} · highlighted: the passage used for this answer`
    : decodeURIComponent(source.uri.split("/").pop());
  body.append(info);
  if (!isPdf) {
    const p = document.createElement("p");
    p.className = "preview-text";
    p.textContent = source.excerpt || "";
    body.append(p);
    return;
  }
  const nav = document.createElement("div");
  nav.className = "preview-nav";
  nav.innerHTML = `<button type="button" class="link-btn prev">‹ Prev</button><span class="page-label"></span><button type="button" class="link-btn next">Next ›</button>`;
  const stage = document.createElement("div");
  stage.className = "preview-stage";
  stage.innerHTML = '<div class="loading-line">Opening document…</div>';
  body.append(nav, stage);
  const passage = norm(source.excerpt || "");
  let pdf;
  try { pdf = await loadSourcePdf(source.uri); } catch (e) { stage.innerHTML = ""; stage.append(Object.assign(document.createElement("p"), { className: "preview-text", textContent: e.message })); return; }
  let current = Math.min(Math.max(pages[0], 1), pdf.numPages);
  const render = async () => {
    if (panelMode !== "source") return;
    nav.querySelector(".page-label").textContent = `Page ${current} of ${pdf.numPages}${pages.includes(current) ? " · cited" : ""}`;
    nav.querySelector(".prev").disabled = current <= 1;
    nav.querySelector(".next").disabled = current >= pdf.numPages;
    const page = await pdf.getPage(current);
    // Render sharp at a fixed size; CSS scales it to the panel width (the panel may still be opening).
    const viewport = page.getViewport({ scale: 900 / page.getViewport({ scale: 1 }).width });
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const canvas = document.createElement("canvas");
    canvas.width = viewport.width * ratio;
    canvas.height = viewport.height * ratio;
    const holder = document.createElement("div");
    holder.className = "preview-page";
    holder.style.aspectRatio = `${viewport.width} / ${viewport.height}`;
    holder.append(canvas);
    await page.render({ canvasContext: canvas.getContext("2d"), viewport, transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : null }).promise;
    // Highlight the lines whose text is part of the cited passage.
    const text = await page.getTextContent();
    let first = null;
    for (const item of text.items) {
      const t = norm(item.str || "");
      if (t.length < 4 || !passage.includes(t)) continue;
      const [a, b, , , e, f] = window.pdfjsLib.Util.transform(viewport.transform, item.transform);
      const h = Math.hypot(a, b) || 10;
      const mark = document.createElement("span");
      mark.className = "hl";
      const pct = (v, total) => `${(v / total) * 100}%`;
      mark.style.left = pct(e, viewport.width);
      mark.style.top = pct(f - h, viewport.height);
      mark.style.width = pct(item.width * viewport.scale, viewport.width);
      mark.style.height = pct(h * 1.15, viewport.height);
      holder.append(mark);
      first ||= mark;
    }
    stage.innerHTML = "";
    stage.append(holder);
    if (first) first.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
  };
  nav.querySelector(".prev").addEventListener("click", () => { current -= 1; render(); });
  nav.querySelector(".next").addEventListener("click", () => { current += 1; render(); });
  render();
}

/* ---------- Application checklist ---------- */
const CHECKLISTS = {
  home: [
    { label: "PAN card", types: ["pan_card"] },
    { label: "Aadhaar card", types: ["aadhaar_card"] },
    { label: "Address proof", types: ["aadhaar_card", "passport", "utility_bill"], hint: "Aadhaar, passport or utility bill" },
    { label: "Salary slips (last 3 months)", types: ["salary_slip"], hint: "or ITR if self-employed", alt: ["itr"] },
    { label: "Form 16 / ITR", types: ["form_16", "itr"] },
    { label: "Bank statements (last 6 months)", types: ["bank_statement"] },
    { label: "Property documents", types: ["property_document"], hint: "sale agreement, title deed, approved plan" },
  ],
  personal: [
    { label: "PAN card", types: ["pan_card"] },
    { label: "Aadhaar card", types: ["aadhaar_card"] },
    { label: "Salary slips (last 3 months)", types: ["salary_slip"] },
    { label: "Bank statements (last 3 months)", types: ["bank_statement"] },
  ],
};
const LOAN_KEY = "loanassist.loanType";
const STATUS_TEXT = { uploading: "Uploading…", queued: "Queued", processing: "Reading…", retrying: "Retrying…", done: "Received", failed: "Failed" };
let checklistTimer = null;

function guessLoanType(chat) {
  const text = (chat && chat.messages ? chat.messages.map((m) => m.text).join(" ") : "").toLowerCase();
  return /personal loan/.test(text) && !/home loan|housing/.test(text) ? "personal" : "home";
}

async function openChecklist() {
  const chat = getChat(currentId);
  const body = openPanel("checklist", "Application checklist");
  let loanType;
  try { loanType = JSON.parse(localStorage.getItem(LOAN_KEY) || "{}")[currentId]; } catch { loanType = null; }
  loanType ||= guessLoanType(chat);
  const head = document.createElement("div");
  head.className = "checklist-head";
  head.innerHTML = `<label>Loan type <select><option value="home">Home loan</option><option value="personal">Personal loan</option></select></label>
    <span class="checklist-progress"></span>`;
  const select = head.querySelector("select");
  select.value = loanType;
  const list = document.createElement("ul");
  list.className = "checklist";
  const note = document.createElement("p");
  note.className = "storage-note";
  note.textContent = "Upload each document with 📎; LoanAssist recognises the type automatically. Files up to 20 MB; big files are processed in the background.";
  body.append(head, list, note);
  select.addEventListener("change", () => {
    try { const all = JSON.parse(localStorage.getItem(LOAN_KEY) || "{}"); all[currentId] = select.value; localStorage.setItem(LOAN_KEY, JSON.stringify(all)); } catch {}
    refresh();
  });

  const refresh = async () => {
    clearTimeout(checklistTimer);
    if (panelMode !== "checklist") return;
    let docs = [];
    if (currentId && getChat(currentId) && getChat(currentId).messages && getChat(currentId).messages.length) {
      try { docs = await apiJson(`/chats/${currentId}/documents`); } catch { docs = []; }
    }
    const items = CHECKLISTS[select.value];
    let doneCount = 0;
    list.innerHTML = "";
    for (const item of items) {
      const matches = docs.filter((d) => item.types.includes(d.document_type));
      const done = matches.find((d) => d.status === "done");
      if (done) doneCount += 1;
      const li = document.createElement("li");
      li.className = `check-item ${done ? "ok" : ""}`;
      const mark = document.createElement("span");
      mark.className = "check-mark";
      mark.textContent = done ? "✓" : "";
      const text = document.createElement("div");
      const name = document.createElement("strong");
      name.textContent = item.label;
      const sub = document.createElement("span");
      sub.textContent = done ? done.filename : (item.hint || "Not uploaded yet");
      text.append(name, sub);
      li.append(mark, text);
      if (!done) {
        const up = document.createElement("button");
        up.type = "button";
        up.className = "link-btn";
        up.textContent = "Upload";
        up.addEventListener("click", () => fileInput.click());
        li.append(up);
      }
      list.append(li);
    }
    // In-flight and failed uploads (type not known yet, or failed).
    const pending = docs.filter((d) => d.status && d.status !== "done");
    for (const d of pending) {
      const li = document.createElement("li");
      li.className = `check-item pending ${d.status}`;
      li.innerHTML = '<span class="check-mark">⏳</span><div><strong></strong><span></span></div>';
      li.querySelector("strong").textContent = d.filename || "Document";
      li.querySelector("div span").textContent = `${STATUS_TEXT[d.status] || d.status}${d.attempts > 1 ? ` (attempt ${d.attempts})` : ""}${d.error ? ` — ${d.error}` : ""}`;
      if (d.status === "failed") {
        li.querySelector(".check-mark").textContent = "!";
        const retry = document.createElement("button");
        retry.type = "button";
        retry.className = "link-btn";
        retry.textContent = "Retry";
        retry.addEventListener("click", async () => {
          try { await apiJson(`/chats/${currentId}/documents/${d.doc_id}/retry`, { method: "POST" }); toast("Queued again."); refresh(); }
          catch (e) { toast(e.message); }
        });
        li.append(retry);
      }
      list.append(li);
    }
    head.querySelector(".checklist-progress").textContent = `${doneCount} of ${items.length} received`;
    if (pending.some((d) => d.status !== "failed")) checklistTimer = setTimeout(refresh, 4000);
  };
  refresh();
}

/* =====================================================================
   Export the chat (browser print -> Save as PDF)
   ===================================================================== */
function exportChat() {
  const chat = getChat(currentId);
  if (!chat || !chat.messages || !chat.messages.length) { toast("Open a chat to export it."); return; }
  $("#printTitle").textContent = chat.title;
  $("#printMeta").textContent = `Exported ${FULL_FMT.format(Date.now())} · LoanAssist gives information only; a loan officer makes the final decision.`;
  closePanel();
  window.print();
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
form.addEventListener("submit", (e) => {
  e.preventDefault();
  if (streamController) { streamController.abort(); return; }   // Stop
  send(input.value.trim());
});
// While streaming the button is Stop. Handle the click directly: the empty, required
// textarea would otherwise block the form's submit event.
sendButton.addEventListener("click", (e) => {
  if (!streamController) return;
  e.preventDefault();
  streamController.abort();
});
$("#exportChat").addEventListener("click", exportChat);
$("#openChecklist").addEventListener("click", () => (panelMode === "checklist" ? closePanel() : openChecklist()));
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
