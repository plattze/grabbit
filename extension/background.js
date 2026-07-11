// Service worker: context menus + submission to the Grabbit API.

const MENU_LINK = "grabbit-send-link";
const MENU_PAGE = "grabbit-send-page";

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: MENU_LINK,
    title: "Send link to Grabbit",
    contexts: ["link"],
  });
  chrome.contextMenus.create({
    id: MENU_PAGE,
    title: "Send page to Grabbit",
    contexts: ["page"],
  });
});

async function getSettings() {
  const { host, apiKey } = await chrome.storage.sync.get(["host", "apiKey"]);
  return { host: (host || "").replace(/\/+$/, ""), apiKey: apiKey || "" };
}

function notify(title, message) {
  chrome.notifications.create({
    type: "basic",
    iconUrl: "icons/icon128.png",
    title,
    message,
  });
}

async function submit(url) {
  const { host, apiKey } = await getSettings();
  if (!host || !apiKey) {
    notify("Grabbit not configured", "Set the server URL and API key in the extension options.");
    chrome.runtime.openOptionsPage();
    return;
  }
  try {
    const res = await fetch(`${host}/api/downloads`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ urls: [url] }),
    });
    if (res.status === 401) {
      notify("Grabbit: invalid API key", "Update the key in the extension options.");
      return;
    }
    if (!res.ok) {
      notify("Grabbit error", `Server responded ${res.status}`);
      return;
    }
    const [result] = await res.json();
    if (result.accepted) {
      notify("Queued in Grabbit", url);
    } else {
      notify("Grabbit rejected the URL", result.reason || "unsupported URL");
    }
  } catch (e) {
    notify("Grabbit unreachable", String(e));
  }
}

chrome.contextMenus.onClicked.addListener((info) => {
  const url = info.menuItemId === MENU_LINK ? info.linkUrl : info.pageUrl;
  if (url) submit(url);
});
