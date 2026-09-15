const STORAGE_KEYS = {
  bridgeUrl: "bridgeUrl",
  sessionId: "sessionId",
  sessionToken: "sessionToken",
  mappingUrl: "mappingUrl",
  exciseLoginUrl: "exciseLoginUrl",
  exciseUser: "exciseUser",
  excisePassword: "excisePassword",
};

const DEFAULT_BRIDGE_URL = "https://integrations.madhushalasoftware.com/excise-import";
const DEFAULT_EXCISE_LOGIN_URL = "https://excise.wb.gov.in/WBSBCL/Bevco/NIC/UserLogin/Login.aspx";
const ALLOWED_EXCISE_HOST = "excise.wb.gov.in";

function normalizeBaseUrl(value) {
  return String(value || DEFAULT_BRIDGE_URL).trim().replace(/\/+$/, "");
}

function normalizeExciseLoginUrl(value) {
  const raw = String(value || DEFAULT_EXCISE_LOGIN_URL).trim();
  try {
    const url = new URL(raw);
    if (url.protocol !== "https:" || url.hostname.toLowerCase() !== ALLOWED_EXCISE_HOST) return "";
    return url.href;
  } catch {
    return "";
  }
}

function buildUrl(baseUrl, path) {
  return `${normalizeBaseUrl(baseUrl)}${path.startsWith("/") ? path : `/${path}`}`;
}

async function getSettings() {
  const data = await chrome.storage.local.get({
    [STORAGE_KEYS.bridgeUrl]: DEFAULT_BRIDGE_URL,
    [STORAGE_KEYS.sessionId]: "",
    [STORAGE_KEYS.sessionToken]: "",
    [STORAGE_KEYS.mappingUrl]: "",
    [STORAGE_KEYS.exciseLoginUrl]: DEFAULT_EXCISE_LOGIN_URL,
    [STORAGE_KEYS.exciseUser]: "",
    [STORAGE_KEYS.excisePassword]: "",
  });
  return {
    bridgeUrl: normalizeBaseUrl(data[STORAGE_KEYS.bridgeUrl]),
    sessionId: data[STORAGE_KEYS.sessionId] || "",
    sessionToken: data[STORAGE_KEYS.sessionToken] || "",
    mappingUrl: data[STORAGE_KEYS.mappingUrl] || "",
    exciseLoginUrl: normalizeExciseLoginUrl(data[STORAGE_KEYS.exciseLoginUrl]) || DEFAULT_EXCISE_LOGIN_URL,
    exciseUser: data[STORAGE_KEYS.exciseUser] || "",
    excisePassword: data[STORAGE_KEYS.excisePassword] || "",
  };
}

async function setSession(payload = {}) {
  const bridgeUrl = normalizeBaseUrl(payload.bridgeUrl);
  const updates = {
    [STORAGE_KEYS.bridgeUrl]: bridgeUrl,
    [STORAGE_KEYS.sessionId]: String(payload.sessionId || ""),
    [STORAGE_KEYS.sessionToken]: String(payload.sessionToken || ""),
    [STORAGE_KEYS.mappingUrl]: String(payload.mappingUrl || ""),
  };
  await chrome.storage.local.set(updates);
  return {status: "session_ready"};
}

async function saveSettings(payload = {}) {
  const updates = {};
  if ("exciseLoginUrl" in payload) {
    const url = normalizeExciseLoginUrl(payload.exciseLoginUrl);
    if (!url) throw new Error("Excise login URL must be https://excise.wb.gov.in/...");
    updates[STORAGE_KEYS.exciseLoginUrl] = url;
  }
  if ("exciseUser" in payload) updates[STORAGE_KEYS.exciseUser] = String(payload.exciseUser || "").trim();
  if ("excisePassword" in payload) updates[STORAGE_KEYS.excisePassword] = String(payload.excisePassword || "");
  await chrome.storage.local.set(updates);
  return {status: "saved"};
}

function requireSession(settings) {
  if (!settings.sessionToken) {
    throw new Error("Open this from the CRM import button first.");
  }
}

async function apiError(response) {
  let body = {};
  try {
    body = await response.json();
  } catch {
    body = {};
  }
  if (response.status === 401 || response.status === 403) {
    return new Error(body.detail || "CRM session expired. Open import from CRM again.");
  }
  return new Error(body.detail || body.error || `Server returned HTTP ${response.status}`);
}

async function postCapture(items, pageUrl, capturedAt) {
  const settings = await getSettings();
  requireSession(settings);
  const response = await fetch(buildUrl(settings.bridgeUrl, "/extension/capture"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${settings.sessionToken}`,
    },
    body: JSON.stringify({
      pageUrl,
      capturedAt: capturedAt || new Date().toISOString(),
      items,
    }),
  });
  if (!response.ok) throw await apiError(response);
  return response.json();
}

function fillExciseLogin(credentials) {
  if (location.hostname.toLowerCase() !== "excise.wb.gov.in") {
    return {userFilled: false, passwordFilled: false, blocked: "unexpected_host"};
  }

  function visibleInput(selectors) {
    for (const selector of selectors) {
      const input = document.querySelector(selector);
      if (input && input.offsetParent !== null) return input;
    }
    return null;
  }

  function setNativeValue(input, value) {
    if (!input || !value) return false;
    const setter = Object.getOwnPropertyDescriptor(input.constructor.prototype, "value")?.set;
    if (setter) setter.call(input, value);
    else input.value = value;
    input.dispatchEvent(new Event("input", {bubbles: true}));
    input.dispatchEvent(new Event("change", {bubbles: true}));
    return true;
  }

  const user = visibleInput([
    'input[type="text"]',
    'input[name*="User" i]',
    'input[id*="User" i]',
    'input[name*="Login" i]',
    'input[id*="Login" i]',
  ]);
  const password = visibleInput([
    'input[type="password"]',
    'input[name*="Password" i]',
    'input[id*="Password" i]',
  ]);

  return {
    userFilled: setNativeValue(user, credentials.exciseUser),
    passwordFilled: setNativeValue(password, credentials.excisePassword),
  };
}

async function waitForTabComplete(tabId) {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const tab = await chrome.tabs.get(tabId);
    if (tab.status === "complete") return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
}

async function tryFillExciseLogin(tabId, credentials) {
  try {
    await waitForTabComplete(tabId);
    const [{result}] = await chrome.scripting.executeScript({
      target: {tabId},
      func: fillExciseLogin,
      args: [credentials],
    });
    return result || {};
  } catch (error) {
    console.warn("Excise login autofill skipped", error);
    return {warning: error?.message || "Excise login autofill skipped"};
  }
}

async function openPortal() {
  const settings = await getSettings();
  requireSession(settings);
  if (!settings.exciseLoginUrl || !settings.exciseUser || !settings.excisePassword) {
    return {status: "needs_credentials"};
  }

  const tab = await chrome.tabs.create({url: settings.exciseLoginUrl, active: true});
  if (tab?.id) {
    tryFillExciseLogin(tab.id, {
      exciseUser: settings.exciseUser,
      excisePassword: settings.excisePassword,
    });
  }
  return {status: "opened", tabId: tab?.id, message: "Excise portal opened. Autofill will run when the login page is ready."};
}

async function handleAutoCapture(payload = {}) {
  const items = Array.isArray(payload.items) ? payload.items : [];
  if (!items.length) return {status: "ignored", reason: "empty"};
  return postCapture(items, payload.pageUrl || "", payload.capturedAt || new Date().toISOString());
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.source !== "madhushala-web" && message?.source !== "madhushala-popup" && message?.source !== "madhushala-excise-page") {
    return false;
  }

  (async () => {
    if (message.type === "SET_SESSION") return setSession(message.payload);
    if (message.type === "GET_SETTINGS") return getSettings();
    if (message.type === "SAVE_SETTINGS") return saveSettings(message.payload);
    if (message.type === "OPEN_PORTAL") return openPortal();
    if (message.type === "AUTO_CAPTURE") return handleAutoCapture(message.payload);
    throw new Error("Unknown extension action.");
  })()
    .then((result) => sendResponse({ok: true, result}))
    .catch((error) => sendResponse({ok: false, error: error.message || "Extension action failed"}));

  return true;
});
