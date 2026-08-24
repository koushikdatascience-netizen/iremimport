(function () {
  if (window.__MADHUSHALA_EXCISE_CAPTURE_INSTALLED__) {
    return;
  }

  window.__MADHUSHALA_EXCISE_CAPTURE_INSTALLED__ = true;

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

  function snapshotCaseTypedRows() {
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

  window.__madhushalaSnapshotCaseTypedRows = snapshotCaseTypedRows;
  window.__madhushalaCaptureSelectedRows = function () {
    const items = snapshotCaseTypedRows();
    if (window.__madhushalaCaptureRows) {
      window.__madhushalaCaptureRows({
        pageUrl: window.location.href,
        capturedAt: new Date().toISOString(),
        items,
      });
    }
    return items.length;
  };
})();
