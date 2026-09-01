const STORAGE_KEYS = {
  bridgeUrl: "bridgeUrl",
  apiBaseUrl: "apiBaseUrl",
  apiSecret: "apiSecret",
  exciseUser: "exciseUser",
  excisePassword: "excisePassword",
};

const DEFAULT_BRIDGE_URL = "http://13.232.52.191/excise-import";
const EXCISE_LOGIN_URL = "https://excise.wb.gov.in/WBSBCL/Bevco/NIC/UserLogin/Login.aspx";

function normalizeBaseUrl(value) {
  return String(value || "").trim().replace(/\/+$/, "");
}

function buildUrl(baseUrl, path) {
  return `${normalizeBaseUrl(baseUrl)}${path.startsWith("/") ? path : `/${path}`}`;
}

async function saveSettings(payload = {}) {
  const updates = {};
  if ("bridgeUrl" in payload) {
    const bridgeUrl = normalizeBaseUrl(payload.bridgeUrl || DEFAULT_BRIDGE_URL);
    updates[STORAGE_KEYS.bridgeUrl] = bridgeUrl;
    updates[STORAGE_KEYS.apiBaseUrl] = bridgeUrl;
  }
  if ("apiSecret" in payload) updates[STORAGE_KEYS.apiSecret] = String(payload.apiSecret || "").trim();
  if ("exciseUser" in payload) updates[STORAGE_KEYS.exciseUser] = String(payload.exciseUser || "").trim();
  if ("excisePassword" in payload) updates[STORAGE_KEYS.excisePassword] = String(payload.excisePassword || "");
  await chrome.storage.local.set(updates);
  return {status: "saved"};
}

async function getSettings() {
  const data = await chrome.storage.local.get({
    [STORAGE_KEYS.bridgeUrl]: DEFAULT_BRIDGE_URL,
    [STORAGE_KEYS.apiBaseUrl]: DEFAULT_BRIDGE_URL,
    [STORAGE_KEYS.apiSecret]: "",
    [STORAGE_KEYS.exciseUser]: "",
    [STORAGE_KEYS.excisePassword]: "",
  });
  return {
    bridgeUrl: normalizeBaseUrl(data[STORAGE_KEYS.bridgeUrl] || data[STORAGE_KEYS.apiBaseUrl] || DEFAULT_BRIDGE_URL),
    apiSecret: data[STORAGE_KEYS.apiSecret] || "",
    exciseUser: data[STORAGE_KEYS.exciseUser] || "",
    excisePassword: data[STORAGE_KEYS.excisePassword] || "",
  };
}

function requireApiConfig(settings) {
  if (!settings.bridgeUrl) throw new Error("API URL is not configured.");
  if (!settings.apiSecret) throw new Error("API Token is not configured.");
}

async function apiError(response) {
  if (response.status === 401 || response.status === 403) {
    return new Error("Authentication failed. Check API token.");
  }
  let body = {};
  try {
    body = await response.json();
  } catch {
    body = {};
  }
  return new Error(body.detail || body.error || `Server returned HTTP ${response.status}`);
}

async function ensureBackendToken(settings) {
  requireApiConfig(settings);
  let response;
  try {
    response = await fetch(buildUrl(settings.bridgeUrl, "/madhushala/token"), {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({token: settings.apiSecret}),
    });
  } catch {
    throw new Error("Unable to connect to backend.");
  }
  if (!response.ok) throw await apiError(response);
}

async function testApiConnection(payload = {}) {
  const settings = {...await getSettings(), ...payload};
  settings.bridgeUrl = normalizeBaseUrl(settings.bridgeUrl);
  requireApiConfig(settings);

  let health;
  try {
    health = await fetch(buildUrl(settings.bridgeUrl, "/health"));
  } catch {
    throw new Error("Backend unreachable.");
  }
  if (!health.ok) throw new Error("Backend unavailable.");

  await ensureBackendToken(settings);
  const authResponse = await fetch(buildUrl(settings.bridgeUrl, "/mapping/workspace?latestOnly=false"));
  if (!authResponse.ok) throw await apiError(authResponse);
  return {status: "connected"};
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

async function openPortal() {
  const settings = await getSettings();
  if (!settings.exciseUser || !settings.excisePassword) {
    throw new Error("Save User ID and Password first.");
  }
  const tab = await chrome.tabs.create({url: EXCISE_LOGIN_URL, active: true});
  await waitForTabComplete(tab.id);
  const [{result}] = await chrome.scripting.executeScript({
    target: {tabId: tab.id},
    func: fillExciseLogin,
    args: [{exciseUser: settings.exciseUser, excisePassword: settings.excisePassword}],
  });
  if (!result?.userFilled || !result?.passwordFilled) {
    throw new Error("Portal opened. Login fields were not detected.");
  }
  return {status: "opened"};
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
  if (!tab?.id) throw new Error("Open Prepare Indent in Excise first.");
  return tab;
}

async function captureSelected() {
  const settings = await getSettings();
  requireApiConfig(settings);
  await ensureBackendToken(settings);

  const tab = await activeExciseTab();
  const [{result: items}] = await chrome.scripting.executeScript({
    target: {tabId: tab.id},
    func: snapshotPrepareIndentRows,
  });
  if (!items?.length) throw new Error("No rows found. Type case quantity first.");

  let response;
  try {
    response = await fetch(buildUrl(settings.bridgeUrl, "/extension/capture"), {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        pageUrl: tab.url || "",
        capturedAt: new Date().toISOString(),
        items,
      }),
    });
  } catch {
    throw new Error("Unable to connect to backend.");
  }
  if (!response.ok) throw await apiError(response);
  return response.json();
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.source !== "madhushala-web" && message?.source !== "madhushala-popup") return false;

  (async () => {
    if (message.type === "GET_SETTINGS") return getSettings();
    if (message.type === "SAVE_SETTINGS") return saveSettings(message.payload);
    if (message.type === "TEST_API") return testApiConnection(message.payload);
    if (message.type === "OPEN_PORTAL") return openPortal();
    if (message.type === "CAPTURE_SELECTED") return captureSelected();
    throw new Error("Unknown extension action.");
  })()
    .then((result) => sendResponse({ok: true, result}))
    .catch((error) => sendResponse({ok: false, error: error.message || "Extension action failed"}));

  return true;
});
