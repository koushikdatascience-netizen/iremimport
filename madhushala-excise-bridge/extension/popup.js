const DEFAULT_SERVER_URL = "http://13.232.52.191/excise-import";

const serverInput = document.getElementById("server-url");
const saveButton = document.getElementById("save-url");
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
  const stored = await chrome.storage.sync.get({serverUrl: DEFAULT_SERVER_URL});
  return cleanServerUrl(stored.serverUrl);
}

async function setServerUrl(value) {
  const serverUrl = cleanServerUrl(value);
  await chrome.storage.sync.set({serverUrl});
  serverInput.value = serverUrl;
  setStatus("Bridge URL saved.", "success");
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
    setStatus(
      needsMapping
        ? `Captured ${data.itemCount}. Open Mapping to match items.`
        : `Captured ${data.itemCount}. All items are mapped.`,
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
  serverInput.value = await getServerUrl();
});

saveButton.addEventListener("click", () => setServerUrl(serverInput.value));
captureButton.addEventListener("click", captureTypedRows);
mappingButton.addEventListener("click", openMapping);
