const STORAGE_KEYS = {
  bridgeUrl: "bridgeUrl",
  sessionId: "sessionId",
  sessionToken: "sessionToken",
  mappingUrl: "mappingUrl",
};

const DEFAULT_BRIDGE_URL = "https://integrations.madhushalasoftware.com/excise-import";

function normalizeBaseUrl(value) {
  return String(value || DEFAULT_BRIDGE_URL).trim().replace(/\/+$/, "");
}

function normalizeExciseLoginUrl(value) {
  const raw = String(value || "").trim();
  try {
    const url = new URL(raw);
    if (url.protocol !== "https:" || !url.hostname) return "";
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
  });
  return {
    bridgeUrl: normalizeBaseUrl(data[STORAGE_KEYS.bridgeUrl]),
    sessionId: data[STORAGE_KEYS.sessionId] || "",
    sessionToken: data[STORAGE_KEYS.sessionToken] || "",
    mappingUrl: data[STORAGE_KEYS.mappingUrl] || "",
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
  const detail = typeof body.detail === "object" && body.detail
    ? (body.detail.message || body.detail.error || JSON.stringify(body.detail))
    : body.detail;
  if (response.status === 401 || response.status === 403) {
    return new Error(detail || "CRM session expired. Open import from CRM again.");
  }
  return new Error(detail || body.error || `Server returned HTTP ${response.status}`);
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
  const profile = credentials?.loginProfile || {};
  const allowedOrigins = Array.isArray(profile.allowedOrigins) ? profile.allowedOrigins : [];
  if (!allowedOrigins.includes(location.origin)) {
    return {
      userFilled: false,
      passwordFilled: false,
      submitted: false,
      blocked: "unexpected_origin",
      origin: location.origin,
    };
  }

  function visibleElement(selectors) {
    for (const selector of Array.isArray(selectors) ? selectors : []) {
      try {
        const element = document.querySelector(selector);
        if (element && element.offsetParent !== null && !element.disabled) return element;
      } catch {
        // Ignore a bad state-specific selector and continue through the profile.
      }
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

  const user = visibleElement(profile.usernameSelectors);
  const password = visibleElement(profile.passwordSelectors);
  const userFilled = setNativeValue(user, credentials.exciseUser);
  const passwordFilled = setNativeValue(password, credentials.excisePassword);
  const captcha = visibleElement(profile.captchaSelectors);

  let submitted = false;
  if (userFilled && passwordFilled && !captcha && profile.autoSubmit !== false) {
    let loginButton = visibleElement(profile.loginSelectors);
    if (!loginButton) {
      const words = (Array.isArray(profile.loginText) ? profile.loginText : [])
        .map((value) => String(value || "").trim().toLowerCase())
        .filter(Boolean);
      const candidates = Array.from(document.querySelectorAll(
        'button, input[type="button"], input[type="submit"], [role="button"]'
      ));
      loginButton = candidates.find((element) => {
        if (element.offsetParent === null || element.disabled) return false;
        const label = String(
          element.textContent || element.value || element.getAttribute("aria-label") || ""
        ).trim().toLowerCase();
        return words.some((word) => label === word || label.includes(word));
      }) || null;
    }
    if (loginButton) {
      loginButton.click();
      submitted = true;
    } else if (password?.form && typeof password.form.requestSubmit === "function") {
      password.form.requestSubmit();
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
    let latest = {};
    for (let attempt = 0; attempt < 20; attempt += 1) {
      const [{result}] = await chrome.scripting.executeScript({
        target: {tabId},
        func: fillExciseLogin,
        args: [credentials],
      });
      latest = result || {};
      if (latest.userFilled && latest.passwordFilled) return latest;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    return latest;
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
  const loginProfile = portal.loginProfile && typeof portal.loginProfile === "object"
    ? portal.loginProfile
    : {};

  if (!exciseLoginUrl) {
    throw new Error(`No valid HTTPS Excise portal is configured for ${state || "this state"}.`);
  }
  if (!Array.isArray(loginProfile.allowedOrigins) || !loginProfile.allowedOrigins.length) {
    throw new Error(`Excise login profile is missing allowed origins for ${state || "this state"}.`);
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
      loginProfile,
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
    if (message.type === "OPEN_PORTAL") return openPortal();
    if (message.type === "AUTO_CAPTURE") return handleAutoCapture(message.payload);
    throw new Error("Unknown extension action.");
  })()
    .then((result) => sendResponse({ok: true, result}))
    .catch((error) => sendResponse({ok: false, error: error.message || "Extension action failed"}));

  return true;
});
