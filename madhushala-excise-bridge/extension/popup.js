const DEFAULT_BRIDGE_URL = "http://13.232.52.191/excise-import";

function normalizeBaseUrl(value) {
  return String(value || DEFAULT_BRIDGE_URL).trim().replace(/\/+$/, "");
}

async function openControlPanel() {
  const data = await chrome.storage.local.get({
    bridgeUrl: DEFAULT_BRIDGE_URL,
    apiBaseUrl: DEFAULT_BRIDGE_URL,
  });
  const url = normalizeBaseUrl(data.bridgeUrl || data.apiBaseUrl);
  await chrome.tabs.create({url: `${url}/`});
}

document.getElementById("open-control-panel").addEventListener("click", openControlPanel);
