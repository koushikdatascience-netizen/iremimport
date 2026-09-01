const DEFAULT_SERVER_URL = "http://13.232.52.191/excise-import";
const EXCISE_LOGIN_URL = "https://excise.wb.gov.in/WBSBCL/Bevco/NIC/UserLogin/Login.aspx";

const serverInput = document.getElementById("server-url");
const userInput = document.getElementById("excise-user");
const passwordInput = document.getElementById("excise-password");
const saveButton = document.getElementById("save-settings");
const openPortalButton = document.getElementById("open-portal");
const captureButton = document.getElementById("capture");
const mappingButton = document.getElementById("open-mapping");
const statusEl = document.getElementById("status");

function setStatus(message, type = "") {
  statusEl.textContent = message;
  statusEl.className = `status ${type}`.trim();
}

function cleanServerUrl(value) {
  return String(value || DEFAULT_SERVER_URL).replace(/\/+$/, "");
}

async function getServerUrl() {
  const settings = await loadSettings();
  return cleanServerUrl(settings.serverUrl);
}

async function loadSettings() {
  return chrome.storage.local.get({
    serverUrl: DEFAULT_SERVER_URL,
    exciseUser: "",
    excisePassword: "",
  });
}

async function saveSettings() {
  const serverUrl = cleanServerUrl(serverInput.value);
  await chrome.storage.local.set({
    serverUrl,
    exciseUser: userInput.value.trim(),
    excisePassword: passwordInput.value,
  });
  serverInput.value = serverUrl;
  setStatus("Setup saved.", "success");
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

async function activeTab() {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  if (!tab?.id) throw new Error("Open the Excise Prepare Indent tab first.");
  return tab;
}

async function captureTypedRows() {
  captureButton.disabled = true;
  setStatus("Capturing typed rows...");

  try {
    const tab = await activeTab();
    const [{result: items}] = await chrome.scripting.executeScript({
      target: {tabId: tab.id},
      func: snapshotPrepareIndentRows,
    });

    if (!items?.length) {
      throw new Error("No rows found. Type case quantity in Prepare Indent first.");
    }

    const serverUrl = await getServerUrl();
    const response = await fetch(`${serverUrl}/extension/capture`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        pageUrl: tab.url || "",
        capturedAt: new Date().toISOString(),
        items,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.detail || data.error || "Capture failed.");
    }

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
  const serverUrl = await getServerUrl();
  await chrome.tabs.create({url: `${serverUrl}/`});
}

document.addEventListener("DOMContentLoaded", async () => {
  const settings = await loadSettings();
  serverInput.value = cleanServerUrl(settings.serverUrl);
  userInput.value = settings.exciseUser || "";
  passwordInput.value = settings.excisePassword || "";
});

saveButton.addEventListener("click", saveSettings);
openPortalButton.addEventListener("click", openPortal);
captureButton.addEventListener("click", captureTypedRows);
mappingButton.addEventListener("click", openMapping);
