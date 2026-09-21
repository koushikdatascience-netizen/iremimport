const status = document.getElementById("status");

function setStatus(message, type = "") {
  status.textContent = message;
  status.className = type ? `status ${type}` : "status";
}

async function send(type, payload = {}) {
  const response = await chrome.runtime.sendMessage({
    source: "madhushala-popup",
    type,
    payload,
  });
  if (!response?.ok) throw new Error(response?.error || "Extension action failed");
  return response.result || {};
}

async function loadStatus() {
  try {
    const settings = await send("GET_SETTINGS");
    setStatus(
      settings.sessionToken
        ? "Ready. State and Excise login are loaded automatically from Madhushala Company Master."
        : "Open Excise Import from Madhushala CRM first.",
      settings.sessionToken ? "success" : "",
    );
  } catch (error) {
    setStatus(error.message || "Could not load extension status.", "error");
  }
}

loadStatus();
