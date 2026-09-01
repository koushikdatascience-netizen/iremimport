const STORAGE_KEYS = {
  bridgeUrl: "bridgeUrl",
  apiBaseUrl: "apiBaseUrl",
  apiSecret: "apiSecret",
  exciseUser: "exciseUser",
  excisePassword: "excisePassword",
  mappingWindowId: "mappingWindowId",
};

const LEGACY_KEYS = {
  bridgeUrl: ["serverUrl", "bridgeBaseUrl", "apiUrl", "baseUrl"],
  apiSecret: ["apiKey", "apiToken", "madhushalaToken", "bridgeSecret"],
};

const DEFAULT_BRIDGE_URL = "http://13.232.52.191/excise-import";
const EXCISE_LOGIN_URL = "https://excise.wb.gov.in/WBSBCL/Bevco/NIC/UserLogin/Login.aspx";

const serverInput = document.getElementById("server-url");
const apiSecretInput = document.getElementById("api-secret");
const userInput = document.getElementById("excise-user");
const passwordInput = document.getElementById("excise-password");
const saveLoginButton = document.getElementById("save-login");
const saveApiButton = document.getElementById("save-api");
const testApiButton = document.getElementById("test-api");
const openPortalButton = document.getElementById("open-portal");
const captureButton = document.getElementById("capture");
const mappingButton = document.getElementById("open-mapping");
const statusEl = document.getElementById("status");

function setStatus(message, type = "") {
  statusEl.textContent = message;
  statusEl.className = `status ${type}`.trim();
}

function normalizeBaseUrl(value) {
  return String(value || "").trim().replace(/\/+$/, "");
}

function pickValue(data, canonicalKey, legacyKeys = []) {
  for (const key of [canonicalKey, ...legacyKeys]) {
    const value = data[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return "";
}

async function migrateSettings() {
  const keys = [
    ...Object.values(STORAGE_KEYS),
    ...LEGACY_KEYS.bridgeUrl,
    ...LEGACY_KEYS.apiSecret,
  ];
  const data = await chrome.storage.local.get(keys);
  const storedBridgeUrl = pickValue(data, STORAGE_KEYS.bridgeUrl, [STORAGE_KEYS.apiBaseUrl, ...LEGACY_KEYS.bridgeUrl]);
  const bridgeUrl = normalizeBaseUrl(storedBridgeUrl || DEFAULT_BRIDGE_URL);
  const apiSecret = pickValue(data, STORAGE_KEYS.apiSecret, LEGACY_KEYS.apiSecret);
  const updates = {
    [STORAGE_KEYS.bridgeUrl]: bridgeUrl,
    [STORAGE_KEYS.apiBaseUrl]: bridgeUrl,
  };
  if (apiSecret) updates[STORAGE_KEYS.apiSecret] = apiSecret;
  await chrome.storage.local.set(updates);
  return {...data, ...updates};
}

async function loadSettings() {
  const data = await migrateSettings();
  return {
    bridgeUrl: normalizeBaseUrl(data[STORAGE_KEYS.bridgeUrl] || data[STORAGE_KEYS.apiBaseUrl]),
    apiSecret: data[STORAGE_KEYS.apiSecret] || "",
    exciseUser: data[STORAGE_KEYS.exciseUser] || "",
    excisePassword: data[STORAGE_KEYS.excisePassword] || "",
  };
}

function readApiFields() {
  return {
    bridgeUrl: normalizeBaseUrl(serverInput.value),
    apiSecret: apiSecretInput.value.trim(),
  };
}

function requireApiConfig(config) {
  if (!config.bridgeUrl) {
    throw new Error("API URL is not configured. Open Control Panel -> Backend / API Configuration.");
  }
  if (!config.apiSecret) {
    throw new Error("API Secret is not configured. Open Control Panel -> Backend / API Configuration.");
  }
}

function buildUrl(baseUrl, path) {
  return `${normalizeBaseUrl(baseUrl)}${path.startsWith("/") ? path : `/${path}`}`;
}

async function saveLoginSettings() {
  await chrome.storage.local.set({
    [STORAGE_KEYS.exciseUser]: userInput.value.trim(),
    [STORAGE_KEYS.excisePassword]: passwordInput.value,
  });
  setStatus("Login saved.", "success");
}

async function saveApiSettings() {
  const config = readApiFields();
  if (!config.bridgeUrl) {
    setStatus("API URL is not configured. Open Control Panel -> Backend / API Configuration.", "error");
    return;
  }

  await chrome.storage.local.set({
    [STORAGE_KEYS.bridgeUrl]: config.bridgeUrl,
    [STORAGE_KEYS.apiBaseUrl]: config.bridgeUrl,
    [STORAGE_KEYS.apiSecret]: config.apiSecret,
  });
  setStatus("API settings saved successfully.", "success");
}

async function apiError(response) {
  if (response.status === 401 || response.status === 403) {
    return new Error("Authentication failed. Check API Secret.");
  }
  let body = {};
  try {
    body = await response.json();
  } catch {
    body = {};
  }
  return new Error(body.detail || body.error || `Server returned HTTP ${response.status}`);
}

async function ensureBackendToken(config) {
  requireApiConfig(config);
  let response;
  try {
    response = await fetch(buildUrl(config.bridgeUrl, "/madhushala/token"), {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({token: config.apiSecret}),
    });
  } catch {
    throw new Error("Unable to connect to backend.");
  }
  if (!response.ok) throw await apiError(response);
}

async function apiFetch(path, options = {}, overrideConfig = null) {
  const config = overrideConfig || await loadSettings();
  requireApiConfig(config);
  await ensureBackendToken(config);

  let response;
  try {
    response = await fetch(buildUrl(config.bridgeUrl, path), {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
    });
  } catch {
    throw new Error("Unable to connect to backend.");
  }

  if (!response.ok) throw await apiError(response);
  return response.json().catch(() => null);
}

async function testApiConnection() {
  testApiButton.disabled = true;
  setStatus("Testing API connection...");
  const config = readApiFields();

  try {
    requireApiConfig(config);
    let healthResponse;
    try {
      healthResponse = await fetch(buildUrl(config.bridgeUrl, "/health"));
    } catch {
      throw new Error("Backend unreachable");
    }
    if (!healthResponse.ok) throw new Error("Backend unavailable");

    await ensureBackendToken(config);
    const authResponse = await fetch(buildUrl(config.bridgeUrl, "/mapping/workspace?latestOnly=false"), {
      headers: {"Content-Type": "application/json"},
    });
    if (!authResponse.ok) throw await apiError(authResponse);

    setStatus("API connected and authenticated", "success");
  } catch (error) {
    const message = error.message || "";
    if (message.includes("Authentication failed") || message.includes("HTTP 401") || message.includes("HTTP 403")) {
      setStatus("Authentication failed. Check API Secret.", "error");
    } else if (message.includes("API URL") || message.includes("API Secret")) {
      setStatus(message, "error");
    } else {
      setStatus("Backend unreachable", "error");
    }
  } finally {
    testApiButton.disabled = false;
  }
}

function toggleSecret(input, button) {
  const hidden = input.type === "password";
  input.type = hidden ? "text" : "password";
  button.textContent = hidden ? "Hide" : "Show";
}

function fillExciseLogin(credentials) {
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
    if (setter) {
      setter.call(input, value);
    } else {
      input.value = value;
    }
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

async function openPortal() {
  openPortalButton.disabled = true;
  setStatus("Opening Excise portal...");

  try {
    const settings = await loadSettings();
    if (!settings.exciseUser || !settings.excisePassword) {
      throw new Error("Save Excise User ID and Password first.");
    }

    const tab = await chrome.tabs.create({url: EXCISE_LOGIN_URL});
    await waitForTabComplete(tab.id);
    const [{result}] = await chrome.scripting.executeScript({
      target: {tabId: tab.id},
      func: fillExciseLogin,
      args: [{exciseUser: settings.exciseUser, excisePassword: settings.excisePassword}],
    });

    if (!result?.userFilled || !result?.passwordFilled) {
      throw new Error("Portal opened. Fill login once if fields were not detected.");
    }

    setStatus("ID/password filled. Enter CAPTCHA and login.", "success");
  } catch (error) {
    setStatus(error.message || "Could not open portal.", "error");
  } finally {
    openPortalButton.disabled = false;
  }
}

function snapshotPrepareIndentRows() {
  function text(row, selector) {
    return (row.querySelector(selector)?.textContent || "").trim();
  }

  function value(row, selector) {
    return (row.querySelector(selector)?.value || "").trim();
  }

  function typedCaseQuantity(row) {
    const raw = value(row, 'input[id$="_Qty"]');
    const quantity = Number.parseInt(raw, 10);
    return Number.isFinite(quantity) ? quantity : 0;
  }

  return Array.from(document.querySelectorAll('input[id$="_Qty"]'))
    .map((input) => input.closest("tr"))
    .filter(Boolean)
    .filter((row) => typedCaseQuantity(row) > 0)
    .map((row) => ({
      brand: text(row, '[id$="_glbl_brandvt"]'),
      strengthRaw: text(row, '[id$="_mlbllegStr"]'),
      measureMl: text(row, '[id$="_lblmsr"]'),
      packageType: text(row, '[id$="_lblbottle"]'),
      retailerMargin: text(row, '[id$="_lblrm"]'),
      roundOffGovt: text(row, '[id$="_lbl_Round_Off_Govt3"]'),
      specialPurposeFee: text(row, '[id$="_lbl_Special_Levy3"]'),
      mrpPerUnit: text(row, '[id$="_Label55"]'),
      bottlesPerCase: text(row, '[id$="_lblnobotpercase"]'),
      mrpPerCase: text(row, '[id$="_lblmrppercase"]'),
      supplier: text(row, '[id$="_lblsupplier"]'),
      warehouseCasesRaw: text(row, '[id$="_lblclblcase"]'),
      warehouseBottles: text(row, '[id$="_lblclosbal"]'),
      requestedCases: value(row, 'input[id$="_Qty"]'),
      requestedBottles: value(row, 'input[id$="_txt_bot"]'),
    }));
}

async function activeExciseTab() {
  const tabs = await chrome.tabs.query({url: "https://excise.wb.gov.in/*"});
  const tab = tabs.find((item) => item.active) || tabs[0];
  if (!tab?.id) throw new Error("Open the Excise Prepare Indent tab first.");
  return tab;
}

async function captureTypedRows() {
  captureButton.disabled = true;
  setStatus("Capturing selected rows...");

  try {
    const tab = await activeExciseTab();
    const [{result: items}] = await chrome.scripting.executeScript({
      target: {tabId: tab.id},
      func: snapshotPrepareIndentRows,
    });

    if (!items?.length) {
      throw new Error("No rows found. Type case quantity in Prepare Indent first.");
    }

    const data = await apiFetch("/extension/capture", {
      method: "POST",
      body: JSON.stringify({
        pageUrl: tab.url || "",
        capturedAt: new Date().toISOString(),
        items,
      }),
    });

    const needsMapping = data.mappingStatus?.mappingRequired;
    const unmappedCount = Number(data.mappingStatus?.unmappedCount || 0);
    const mappedCount = Math.max(0, Number(data.itemCount || 0) - unmappedCount);
    setStatus(
      needsMapping
        ? `Captured ${data.itemCount}. Mapped ${mappedCount}, need mapping ${unmappedCount}.`
        : `Captured ${data.itemCount}. All selected items are mapped.`,
      "success",
    );
  } catch (error) {
    setStatus(error.message || "Capture failed.", "error");
  } finally {
    captureButton.disabled = false;
  }
}

async function openMapping() {
  try {
    const settings = await loadSettings();
    requireApiConfig(settings);
    await ensureBackendToken(settings);

    const url = `${buildUrl(settings.bridgeUrl, "/")}?view=mapping`;
    const stored = await chrome.storage.local.get(STORAGE_KEYS.mappingWindowId);
    if (stored[STORAGE_KEYS.mappingWindowId]) {
      try {
        const existingWindow = await chrome.windows.get(stored[STORAGE_KEYS.mappingWindowId], {populate: true});
        const tab = existingWindow.tabs?.[0];
        if (tab?.id) {
          await chrome.tabs.update(tab.id, {url, active: true});
        }
        await chrome.windows.update(existingWindow.id, {focused: true});
        return;
      } catch {
        await chrome.storage.local.remove(STORAGE_KEYS.mappingWindowId);
      }
    }

    const win = await chrome.windows.create({
      url,
      type: "popup",
      width: 1180,
      height: 820,
      focused: true,
    });
    if (win?.id) {
      await chrome.storage.local.set({[STORAGE_KEYS.mappingWindowId]: win.id});
    }
  } catch (error) {
    setStatus(error.message || "Could not open Product Mapping.", "error");
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  const settings = await loadSettings();
  serverInput.value = settings.bridgeUrl;
  apiSecretInput.value = settings.apiSecret || "";
  userInput.value = settings.exciseUser || "";
  passwordInput.value = settings.excisePassword || "";
});

document.getElementById("toggle-excise-password").addEventListener("click", () => {
  toggleSecret(passwordInput, document.getElementById("toggle-excise-password"));
});
document.getElementById("toggle-api-secret").addEventListener("click", () => {
  toggleSecret(apiSecretInput, document.getElementById("toggle-api-secret"));
});
saveLoginButton.addEventListener("click", saveLoginSettings);
saveApiButton.addEventListener("click", saveApiSettings);
testApiButton.addEventListener("click", testApiConnection);
openPortalButton.addEventListener("click", openPortal);
captureButton.addEventListener("click", captureTypedRows);
mappingButton.addEventListener("click", openMapping);
