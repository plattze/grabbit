const hostEl = document.getElementById("host");
const keyEl = document.getElementById("apiKey");
const statusEl = document.getElementById("status");

chrome.storage.sync.get(["host", "apiKey"]).then(({ host, apiKey }) => {
  hostEl.value = host || "";
  keyEl.value = apiKey || "";
});

document.getElementById("save").addEventListener("click", async () => {
  await chrome.storage.sync.set({
    host: hostEl.value.trim().replace(/\/+$/, ""),
    apiKey: keyEl.value.trim(),
  });
  statusEl.textContent = "Saved";
  setTimeout(() => (statusEl.textContent = ""), 1500);
});
