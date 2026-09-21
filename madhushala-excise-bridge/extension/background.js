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
const ALLOWED_EXCISE_HOSTS = new Set(["excise.wb.gov.in", "eaabkari.mp.gov.in"]);

function normalizeBaseUrl(value) {
  return String(value || DEFAULT_BRIDGE_URL).trim().replace(/\/+$/, "");
}

function normalizeExciseLoginUrl(value) {
  const raw = String(value || DEFAULT_EXCISE_LOGIN_URL).trim();
  try {
    const url = new URL(raw);
    if (url.protocol !== "https:" || !ALLOWED_EXCISE_HOSTS.has(url.hostname.toLowerCase())) return "";
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
    if (!url) throw new Error("Excise login URL must be a configured WB or MP Excise portal.");
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
  const host = location.hostname.toLowerCase();
  if (!ALLOWED_EXCISE_HOSTS.has(host)) {
    return {userFilled: false, passwordFilled: false, submitted: false, blocked: "unexpected_host"};
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
    'input[name*="UserName" i]',
    'input[id*="UserName" i]',
    'input[name*="User" i]',
    'input[id*="User" i]',
    'input[name*="Login" i]',
    'input[id*="Login" i]',
    'input[name*="userid" i]',
    'input[id*="userid" i]',
    'input[type="text"]',
  ]);
  const password = visibleInput([
    'input[type="password"]',
    'input[name*="Password" i]',
    'input[id*="Password" i]',
    'input[name*="pwd" i]',
    'input[id*="pwd" i]',
  ]);

  const userFilled = setNativeValue(user, credentials.exciseUser);
  const passwordFilled = setNativeValue(password, credentials.excisePassword);

  const captcha = visibleInput([
    'input[name*="captcha" i]',
    'input[id*="captcha" i]',
    'input[name*="capcha" i]',
    'input[id*="capcha" i]',
    'input[name*="verification" i]',
    'input[id*="verification" i]',
  ]);

  let submitted = false;
  if (userFilled && passwordFilled && !captcha) {
    const candidates = Array.from(document.querySelectorAll(
      'button[type="submit"], input[type="submit"], button, input[type="button"]'
    ));
    const loginButton = candidates.find((element) => {
      if (element.offsetParent === null) return false;
      const text = String(element.textContent || element.value || "").trim().toLowerCase();
      return text === "login" || text === "log in" || text === "sign in" || text.includes("login");
    });
    if (loginButton) {
      loginButton.click();
      submitted = true;
    }
  }

  return {
    state: credentials.state || "",
    userFilled,
    passwordFilled,
    captchaRequired: Boolean(captcha),
    submitted,
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

async function loadPortalBootstrap(settings) {
  const response = await fetch(buildUrl(settings.bridgeUrl, "/portal/bootstrap"), {
    method: "GET",
    headers: {
      "Authorization": `Bearer ${settings.sessionToken}`,
      "Accept": "application/json",
    },
  });
  if (!response.ok) throw await apiError(response);
  return response.json();
}

async function openPortal() {
  const settings = await getSettings();
  requireSession(settings);

  const portal = await loadPortalBootstrap(settings);
  const exciseLoginUrl = normalizeExciseLoginUrl(portal.exciseLoginUrl);
  const exciseUser = String(portal.exciseUserId || "").trim();
  const excisePassword = String(portal.excisePassword || "");
  const state = String(portal.state || "").trim();

  if (!exciseLoginUrl) {
    throw new Error(`No supported Excise portal is configured for ${state || "this state"}.`);
  }
  if (!exciseUser || !excisePassword) {
    throw new Error("Excise User ID or password is missing in Madhushala Company Master.");
  }

  const tab = await chrome.tabs.create({url: exciseLoginUrl, active: true});
  let login = {};
  if (tab?.id) {
    login = await tryFillExciseLogin(tab.id, {
      state,
      exciseUser,
      excisePassword,
    });
  }
  return {
    status: "opened",
    state,
    tabId: tab?.id,
    login,
    message: login?.captchaRequired
      ? `${state} Excise opened and credentials were filled. Complete CAPTCHA to continue.`
      : (login?.submitted
          ? `${state} Excise login was submitted automatically.`
          : `${state} Excise opened and credentials were filled.`),
  };
}

async function focusMappingWorkspace(settings) {
  const mappingUrl = String(settings?.mappingUrl || "").trim();
  if (!mappingUrl) return {opened: false, reason: "missing_mapping_url"};

  const tabs = await chrome.tabs.query({});
  const bridgeBase = normalizeBaseUrl(settings.bridgeUrl);
  const existing = tabs.find((tab) => {
    const url = String(tab.url || "");
    return url.startsWith(bridgeBase) && !url.includes("excise.wb.gov.in");
  });

  if (existing?.id) {
    await chrome.tabs.update(existing.id, {url: mappingUrl, active: true});
    if (existing.windowId != null) {
      await chrome.windows.update(existing.windowId, {focused: true});
    }
    return {opened: true, tabId: existing.id, reused: true};
  }

  const tab = await chrome.tabs.create({url: mappingUrl, active: true});
  return {opened: true, tabId: tab?.id, reused: false};
}

async function handleAutoCapture(payload = {}) {
  const items = Array.isArray(payload.items) ? payload.items : [];
  if (!items.length) return {status: "ignored", reason: "empty"};

  const result = await postCapture(items, payload.pageUrl || "", payload.capturedAt || new Date().toISOString());
  if (result?.mappingStatus?.mappingRequired) {
    try {
      const settings = await getSettings();
      result.mappingNavigation = await focusMappingWorkspace(settings);
    } catch (error) {
      console.warn("Could not focus Mapping workspace", error);
      result.mappingNavigation = {opened: false, error: error?.message || "Mapping navigation failed"};
    }
  }
  return result;
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
