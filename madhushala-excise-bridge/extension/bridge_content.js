(function () {
  if (window.__MADHUSHALA_EXTENSION_BRIDGE__) return;
  window.__MADHUSHALA_EXTENSION_BRIDGE__ = true;

  const isExcisePortal = window.location.hostname === "excise.wb.gov.in";

  if (!isExcisePortal) {
    window.postMessage({source: "madhushala-extension", type: "READY"}, window.location.origin);
  }

  window.addEventListener("message", async (event) => {
    // Never expose stored extension settings to scripts running on the Excise site.
    if (isExcisePortal) return;
    if (event.source !== window) return;
    const message = event.data || {};
    if (message.source !== "madhushala-web") return;
    if (message.type === "DISCOVER") {
      window.postMessage({source: "madhushala-extension", type: "READY"}, window.location.origin);
      return;
    }
    if (!message.requestId) return;

    try {
      const response = await chrome.runtime.sendMessage(message);
      window.postMessage({
        source: "madhushala-extension",
        requestId: message.requestId,
        ok: true,
        response,
      }, window.location.origin);
    } catch (error) {
      window.postMessage({
        source: "madhushala-extension",
        requestId: message.requestId,
        ok: false,
        error: error.message || "Extension request failed",
      }, window.location.origin);
    }
  });

  if (!isExcisePortal) return;

  let captureTimer = null;
  let statusTimer = null;
  let captureRunning = false;
  let capturePending = false;

  function text(row, selector) {
    return (row.querySelector(selector)?.textContent || "").trim();
  }

  function value(row, selector) {
    return (row.querySelector(selector)?.value || "").trim();
  }

  function selectedRows() {
    return Array.from(document.querySelectorAll('input[id$="_Qty"]'))
      .map((input) => input.closest("tr"))
      .filter(Boolean)
      .filter((row) => Number.parseInt(value(row, 'input[id$="_Qty"]'), 10) > 0)
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

  function showStatus(message, tone = "working") {
    let status = document.getElementById("madhushala-auto-capture-status");
    if (!status) {
      status = document.createElement("div");
      status.id = "madhushala-auto-capture-status";
      Object.assign(status.style, {
        position: "fixed",
        right: "20px",
        bottom: "20px",
        zIndex: "2147483647",
        padding: "11px 15px",
        borderRadius: "8px",
        color: "#fff",
        font: "600 13px/1.4 system-ui, sans-serif",
        boxShadow: "0 8px 28px rgba(0,0,0,.24)",
        transition: "opacity .2s ease",
      });
      document.documentElement.appendChild(status);
    }
    status.style.background = tone === "error" ? "#9f2d2d" : tone === "done" ? "#236b48" : "#364152";
    status.style.opacity = "1";
    status.textContent = message;
    clearTimeout(statusTimer);
    statusTimer = setTimeout(() => { status.style.opacity = "0"; }, tone === "error" ? 6000 : 3000);
  }

  async function captureAfterEdit() {
    if (captureRunning) {
      capturePending = true;
      return;
    }
    const items = selectedRows();
    if (!items.length) return;

    captureRunning = true;
    showStatus("Saving entered Excise items…");
    try {
      const response = await chrome.runtime.sendMessage({
        source: "madhushala-excise-page",
        type: "AUTO_CAPTURE",
        payload: {
          pageUrl: window.location.href,
          capturedAt: new Date().toISOString(),
          items,
        },
      });
      if (!response?.ok) throw new Error(response?.error || "Automatic capture failed");
      if (response.result?.status !== "unchanged") {
        const mappingStatus = response.result?.mappingStatus || {};
        if (mappingStatus.mappingRequired) {
          showStatus("Saved. Opening product mapping…", "done");
        } else if (mappingStatus.state === "complete") {
          showStatus("Saved. All products are already mapped.", "done");
        } else if (mappingStatus.state === "needs_token") {
          showStatus("Items saved. Madhushala service setup is required.", "error");
        } else if (mappingStatus.state === "error") {
          showStatus(mappingStatus.lastError || "Items saved, but mapping could not be checked.", "error");
        } else {
          showStatus("Entered items saved.", "done");
        }
      }
    } catch (error) {
      showStatus(error.message || "Could not save entered items.", "error");
    } finally {
      captureRunning = false;
      if (capturePending) {
        capturePending = false;
        clearTimeout(captureTimer);
        captureTimer = setTimeout(captureAfterEdit, 500);
      }
    }
  }

  function scheduleCapture(event) {
    if (!event.target?.matches?.('input[id$="_Qty"], input[id$="_txt_bot"]')) return;
    clearTimeout(captureTimer);
    captureTimer = setTimeout(captureAfterEdit, 1800);
  }

  document.addEventListener("input", scheduleCapture, true);
  document.addEventListener("change", scheduleCapture, true);
  // Also recover committed quantities after an ASP.NET postback/navigation.
  captureTimer = setTimeout(captureAfterEdit, 2200);
})();
