const msg = document.getElementById("msg");
const recentEl = document.getElementById("recent");
const sendBtn = document.getElementById("send");

document.getElementById("options").addEventListener("click", (e) => {
  e.preventDefault();
  chrome.runtime.openOptionsPage();
});

async function getSettings() {
  const { host, apiKey } = await chrome.storage.sync.get(["host", "apiKey"]);
  return { host: (host || "").replace(/\/+$/, ""), apiKey: apiKey || "" };
}

function setMsg(text, cls) {
  msg.textContent = text;
  msg.className = cls || "";
}

async function loadRecent() {
  const { host, apiKey } = await getSettings();
  if (!host || !apiKey) {
    setMsg("Configure the server in Options first.", "err");
    sendBtn.disabled = true;
    return;
  }
  try {
    const res = await fetch(`${host}/api/downloads?limit=8`, {
      headers: { Authorization: `Bearer ${apiKey}` },
    });
    if (!res.ok) throw new Error(res.status);
    const jobs = await res.json();
    recentEl.replaceChildren(
      ...jobs.map((j) => {
        const li = document.createElement("li");
        const state = document.createElement("span");
        state.className = `state ${j.state}`;
        state.textContent = j.state;
        li.append(state, ` ${j.url}`);
        li.title = j.url;
        return li;
      }),
    );
  } catch (e) {
    setMsg(`Cannot reach Grabbit (${e.message})`, "err");
  }
}

sendBtn.addEventListener("click", async () => {
  const { host, apiKey } = await getSettings();
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.url) return;
  sendBtn.disabled = true;
  try {
    const res = await fetch(`${host}/api/downloads`, {
      method: "POST",
      headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
      body: JSON.stringify({ urls: [tab.url] }),
    });
    const [result] = await res.json();
    if (result.accepted) {
      setMsg("Queued ✓", "ok");
    } else {
      setMsg(`Rejected: ${result.reason}`, "err");
    }
    loadRecent();
  } catch (e) {
    setMsg(`Failed: ${e}`, "err");
  } finally {
    sendBtn.disabled = false;
  }
});

loadRecent();
