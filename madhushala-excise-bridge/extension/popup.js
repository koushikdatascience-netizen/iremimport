const status = document.getElementById("status");
const exciseLoginUrl = document.getElementById("excise-login-url");
const exciseUser = document.getElementById("excise-user");
const excisePassword = document.getElementById("excise-password");

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

async function loadSettings() {
  try {
    const settings = await send("GET_SETTINGS");
    exciseLoginUrl.value = settings.exciseLoginUrl || "";
    exciseUser.value = settings.exciseUser || "";
    excisePassword.value = settings.excisePassword || "";
    setStatus(settings.sessionToken ? "Ready for current CRM session." : "Save portal details, then open import from CRM.", "success");
  } catch (error) {
    setStatus(error.message || "Could not load settings.", "error");
  }
}

async function saveCredentials() {
  try {
    await send("SAVE_SETTINGS", {
      exciseLoginUrl: exciseLoginUrl.value.trim(),
      exciseUser: exciseUser.value.trim(),
      excisePassword: excisePassword.value,
    });
    setStatus("Portal details saved in this Chrome profile.", "success");
  } catch (error) {
    setStatus(error.message || "Could not save portal details.", "error");
  }
}

document.getElementById("save-settings").addEventListener("click", saveCredentials);
loadSettings();
