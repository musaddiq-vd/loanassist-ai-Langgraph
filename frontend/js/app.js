const form = document.querySelector("#chatForm");
const input = document.querySelector("#messageInput");
const messages = document.querySelector("#chatMessages");
const sendButton = document.querySelector("#sendButton");
const sessionId = crypto.randomUUID();

function addMessage(text, role, sources = []) {
  const article = document.createElement("article"); article.className = `message ${role}`;
  const bubble = document.createElement("div"); bubble.className = "bubble";
  bubble.textContent = text; // Safe: renders LLM/user response as text, never HTML.
  article.append(bubble); messages.append(article);
  const validSources = [...new Map(sources.filter((source) => source.uri).map((source) => [source.uri, source])).values()];
  if (validSources.length) {
    const sourceBox = document.createElement("div"); sourceBox.className = "sources"; sourceBox.textContent = "Sources:";
    const list = document.createElement("ul");
    validSources.forEach((source) => { const item = document.createElement("li"); const link = document.createElement("a"); link.href = source.uri; link.target = "_blank"; link.rel = "noopener"; link.textContent = source.uri.split("/").pop() || "Knowledge base document"; item.append(link); list.append(item); });
    sourceBox.append(list); messages.append(sourceBox);
  }
  messages.scrollTop = messages.scrollHeight;
}
function autoGrow() { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 110)}px`; }
input.addEventListener("input", autoGrow);
input.addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); form.requestSubmit(); } });
form.addEventListener("submit", async (event) => {
  event.preventDefault(); const message = input.value.trim(); if (!message) return;
  addMessage(message, "user"); input.value = ""; autoGrow(); input.focus(); sendButton.disabled = true;
  const typing = document.createElement("article"); typing.className = "message assistant typing"; typing.innerHTML = '<div class="bubble">LoanAssist is thinking…</div>'; messages.append(typing);
  try {
    const response = await fetch(window.LOANASSIST_API_URL, { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({message, session_id:sessionId}) });
    const data = await response.json(); if (!response.ok) throw new Error(data.detail || "Request failed");
    typing.remove(); addMessage(data.answer, "assistant", data.sources || []);
  } catch (error) { typing.remove(); addMessage(`Sorry, I could not connect to LoanAssist. ${error.message}`, "assistant"); }
  finally { sendButton.disabled = false; }
});
