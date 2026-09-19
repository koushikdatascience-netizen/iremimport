const pageParams = new URLSearchParams(window.location.search);
const hashParams = new URLSearchParams(window.location.hash.replace(/^#/, "?"));
const sessionId = pageParams.get("sessionId") || "";
const sessionToken = hashParams.get("session") || sessionStorage.getItem("exciseSession") || "";
const mappingMode = pageParams.get("view") === "mapping";
const documentMode = window.location.pathname.endsWith("/document-import");
const activeJobId = sanitizeJobId(pageParams.get("jobId")) || "";
const basePath = window.location.pathname.startsWith("/excise-import/") ? "/excise-import" : "";

document.body.classList.toggle("mapping-mode", mappingMode);

if (sessionToken) {
    sessionStorage.setItem("exciseSession", sessionToken);
}

let extensionConnected = false;
const extensionRequests = new Map();
let workspace = {unmappedItems: [], madhushalaItems: []};
let selectedExciseCode = null;
const selectedMappings = new Map();
let pendingGuardrailAction = null;
let currentDocumentJobId = activeJobId;
let currentDocumentFile = null;
let currentDocumentFiles = [];
let currentDocumentResult = null;
let currentPreviewUrl = "";
let currentPreviewUrls = [];
let currentPreviewIndex = 0;
let currentUploadKind = "pdf";
let currentReviewItems = [];
let currentSourceUrl = "";
let mappingRefreshTimer = null;
let mappingRefreshInFlight = false;
const DEFAULT_EXCISE_LOGIN_URL = "https://excise.wb.gov.in/WBSBCL/Bevco/NIC/UserLogin/Login.aspx";

function portalSettingsFromForm() {
    return {
        exciseLoginUrl: document.getElementById("excise-login-url")?.value.trim() || DEFAULT_EXCISE_LOGIN_URL,
        exciseUser: document.getElementById("excise-user")?.value.trim() || "",
        excisePassword: document.getElementById("excise-password")?.value || "",
    };
}

function applyPortalSettings(settings = {}) {
    const url = document.getElementById("excise-login-url");
    const user = document.getElementById("excise-user");
    const password = document.getElementById("excise-password");
    if (url) url.value = settings.exciseLoginUrl || DEFAULT_EXCISE_LOGIN_URL;
    if (user) user.value = settings.exciseUser || "";
    if (password) password.value = settings.excisePassword || "";
}

function missingPortalFields(settings = portalSettingsFromForm()) {
    const missing = [];
    if (!settings.exciseLoginUrl) missing.push("login URL");
    if (!settings.exciseUser) missing.push("user ID");
    if (!settings.excisePassword) missing.push("password");
    return missing;
}


function purchaseHeaderStorageKey(jobId = currentDocumentJobId) {
    return `purchaseHeader:${sessionId || "session"}:${jobId || "latest"}`;
}

function todayIso() {
    return new Date().toISOString().slice(0, 10);
}

function defaultYearCode() {
    const now = new Date();
    const year = now.getMonth() >= 3 ? now.getFullYear() : now.getFullYear() - 1;
    return `${year}-${String((year + 1) % 100).padStart(2, "0")}`;
}

function setInputValue(id, value) {
    const input = document.getElementById(id);
    if (input && !input.value && value) input.value = value;
}

["purchase-doc-no", "purchase-doc-date"].forEach((id) => {
    document.getElementById(id)?.addEventListener("input", (event) => {
        event.currentTarget.dataset.userEdited = "true";
    });
});

function collectPurchaseHeader() {
    return {
        yearCode: document.getElementById("purchase-year-code")?.value.trim() || "",
        trnDate: document.getElementById("purchase-trn-date")?.value || "",
        docDate: document.getElementById("purchase-doc-date")?.value || "",
        docNo: document.getElementById("purchase-doc-no")?.value.trim() || "",
        _docDateEdited: document.getElementById("purchase-doc-date")?.dataset.userEdited === "true",
        _docNoEdited: document.getElementById("purchase-doc-no")?.dataset.userEdited === "true",
        tpPassNo: document.getElementById("purchase-tp-pass-no")?.value.trim() || "",
        supplierCode: document.getElementById("purchase-supplier-code")?.value.trim() || "",
        storeCode: document.getElementById("purchase-store-code")?.value.trim() || "",
        purchaseAccCode: document.getElementById("purchase-acc-code")?.value.trim() || "",
        userCode: document.getElementById("purchase-user-code")?.value.trim() || "",
        taxMode: document.getElementById("purchase-tax-mode")?.value || "ITEMWISE",
        narration: document.getElementById("purchase-narration")?.value.trim() || "PDF import",
    };
}

function applyPurchaseHeader(header = {}) {
    const today = todayIso();
    const pairs = {
        "purchase-year-code": header.yearCode || defaultYearCode(),
        "purchase-trn-date": header.trnDate || today,
        "purchase-doc-date":
            header.docDate
            || currentDocumentResult?.extractedDocument?.invoiceDate
            || currentDocumentResult?.job?.invoice_date
            || today,
        "purchase-doc-no": header.docNo || currentDocumentResult?.extractedDocument?.invoiceNumber || "",
        "purchase-tp-pass-no": header.tpPassNo || "",
        "purchase-supplier-code": header.supplierCode || "",
        "purchase-store-code": header.storeCode || "",
        "purchase-acc-code": header.purchaseAccCode || "",
        "purchase-user-code": header.userCode || "",
        "purchase-narration": header.narration || "PDF import",
    };
    Object.entries(pairs).forEach(([id, value]) => setInputValue(id, value));
    const taxMode = document.getElementById("purchase-tax-mode");
    if (taxMode && header.taxMode) taxMode.value = header.taxMode;
}

function persistPurchaseHeader() {
    const jobId = sanitizeJobId(currentDocumentJobId);
    if (!jobId) return;
    sessionStorage.setItem(purchaseHeaderStorageKey(jobId), JSON.stringify(collectPurchaseHeader()));
}

function loadPurchaseHeader(jobId = currentDocumentJobId) {
    try {
        return JSON.parse(sessionStorage.getItem(purchaseHeaderStorageKey(jobId)) || "{}");
    } catch {
        return {};
    }
}

function validatePurchaseHeader(header) {
    const required = ["yearCode", "trnDate", "docDate", "docNo", "supplierCode", "storeCode", "purchaseAccCode", "userCode"];
    return required.filter((key) => !String(header[key] || "").trim());
}

function purchaseResponseText(payload) {
    const response = payload?.madhushalaResponse || {};
    const trnNo = response.trnNo || response.trnNumber || response.data?.trnNo || "";
    const message = response.message || payload?.message || "Purchase saved successfully.";
    const itemCount = payload?.purchasePayload?.items?.length || 0;
    return {trnNo, message, itemCount};
}

function renderPurchaseSuccess(payload, source = "review") {
    const {trnNo, message, itemCount} = purchaseResponseText(payload);
    const title = source === "mapping" ? "Mapping saved and purchase created." : "Purchase saved successfully.";
    const detail = `${message}${trnNo ? ` TRN No: ${trnNo}.` : ""}${itemCount ? ` Items saved: ${itemCount}.` : ""}`;

    const success = document.getElementById("document-success-panel");
    if (success) {
        const heading = success.querySelector("h2");
        const paragraph = success.querySelector("p");
        if (heading) heading.textContent = title;
        if (paragraph) paragraph.textContent = detail;
    }
    setText(document.getElementById("document-action-summary"), detail);
    setText(document.getElementById("document-json"), JSON.stringify(payload, null, 2));

    if (source === "mapping") {
        stopMappingAutoRefresh();
        const mappingView = document.getElementById("mapping-view");
        if (mappingView) {
            mappingView.innerHTML = `
                <section class="document-card document-success mapping-success">
                    <h2>${title}</h2>
                    <p>${detail}</p>
                    <button id="mapping-success-upload" type="button">Import Another Document</button>
                </section>
            `;
            document.getElementById("mapping-success-upload")?.addEventListener("click", () => {
                window.location.href = `${basePath}/document-import?sessionId=${encodeURIComponent(sessionId)}#session=${encodeURIComponent(sessionToken)}`;
            });
        }
    } else {
        setDocumentImportState("complete", detail);
    }
    showToast(detail, "success");
}

async function savePurchaseFromJob(source = "review") {
    const jobId = sanitizeJobId(currentDocumentJobId || activeJobId);
    if (!jobId) {
        showToast("No document job is ready for purchase save", "error");
        return;
    }
    let header = source === "mapping" ? loadPurchaseHeader(jobId) : collectPurchaseHeader();
    persistPurchaseHeader();
    const button = source === "mapping" ? document.getElementById("save-purchase-from-mapping") : document.getElementById("save-purchase");
    if (button) button.disabled = true;
    try {
        const payload = await api(`/api/v1/document-import/jobs/${encodeURIComponent(jobId)}/purchase/save`, {
            method: "POST",
            body: JSON.stringify({header}),
        });
        renderPurchaseSuccess(payload, source);
    } catch (error) {
        showToast(error.message || "Purchase save failed", "error");
    } finally {
        if (button) button.disabled = false;
    }
}

async function decodeQrFromImage(file) {
    if (!("BarcodeDetector" in window)) {
        throw new Error("QR scanning is not supported in this browser. Paste the QR link below after scanning externally.");
    }
    const bitmap = await createImageBitmap(file);
    try {
        const detector = new BarcodeDetector({formats: ["qr_code"]});
        const codes = await detector.detect(bitmap);
        const value = codes?.[0]?.rawValue || "";
        if (!value) throw new Error("No QR code detected in this image. Paste the QR link below if the code is readable on your phone.");
        return value;
    } finally {
        bitmap.close?.();
    }
}

async function extractQrUrl(qrUrl) {
    const cleanUrl = String(qrUrl || "").trim();
    if (!cleanUrl) throw new Error("Paste a QR link first.");
    const payload = await api("/api/v1/document-import/qr/extract", {
        method: "POST",
        body: JSON.stringify({url: cleanUrl}),
    });
    renderQrReview(payload);
}

function renderQrReview(payload) {
    currentUploadKind = "qr";
    if (!payload?.job) {
        throw new Error("QR extraction did not create a reviewable import job.");
    }
    renderDocumentReview(payload);
    currentUploadKind = "qr";
    renderReviewSource(payload);
    setInputValue("purchase-doc-no", payload.extractedDocument?.invoiceNumber || "");
    setInputValue("purchase-doc-date", payload.extractedDocument?.invoiceDate || "");
    setInputValue("purchase-narration", "QR HTML import");
    setDocumentImportState("review");
}
async function uploadQr(file) {
    if (!sessionToken) {
        setDocumentImportState("error", "Open this page from the Madhushala CRM Import PDF / Image button.");
        setText(documentElements().errorMessage, "Missing or expired CRM session.");
        return;
    }
    currentUploadKind = "qr";
    currentDocumentFile = file;
    renderDocumentPreviews([file]);
    setDocumentImportState("uploading");
    const elements = documentElements();
    setText(elements.progressTitle, "Scanning QR image");
    setText(elements.progressDetail, "Reading the QR locally before opening the linked page. This does not use Llama credits.");
    try {
        const qrUrl = await decodeQrFromImage(file);
        setDocumentImportState("extracting");
        const latestElements = documentElements();
        setText(latestElements.progressTitle, "Opening QR link");
        setText(latestElements.progressDetail, "Fetching the linked page and extracting visible data.");
        await extractQrUrl(qrUrl);
    } catch (error) {
        setText(documentElements().errorMessage, error.message || "QR extraction failed.");
        setDocumentImportState("error");
    }
}

async function submitQrLink() {
    if (!sessionToken) {
        setDocumentImportState("error", "Open this page from the Madhushala CRM Import PDF / Image button.");
        setText(documentElements().errorMessage, "Missing or expired CRM session.");
        return;
    }
    const input = document.getElementById("qr-link-input");
    const button = document.getElementById("qr-link-submit");
    if (button) button.disabled = true;
    setDocumentImportState("extracting");
    const elements = documentElements();
    setText(elements.progressTitle, "Opening QR link");
    setText(elements.progressDetail, "Fetching the linked page and extracting visible data. This does not use Llama credits.");
    try {
        await extractQrUrl(input?.value || "");
    } catch (error) {
        setText(documentElements().errorMessage, error.message || "QR link extraction failed.");
        setDocumentImportState("error");
    } finally {
        if (button) button.disabled = false;
    }
}
function apiUrl(path) {
    return `${basePath}${path.startsWith("/") ? path : `/${path}`}`;
}

function sanitizeJobId(value) {
    if (!value || typeof value !== "string") return "";
    const trimmed = value.trim();
    if (!trimmed || /^\[object\s+\w+Event\]$/i.test(trimmed)) return "";
    if (trimmed === "[object PointerEvent]" || trimmed === "[object Event]" || trimmed === "[object MouseEvent]") return "";
    return /^[a-zA-Z0-9_-]{8,128}$/.test(trimmed) ? trimmed : "";
}

function isEventLike(value) {
    return value && typeof value === "object" && ("type" in value || "target" in value || "currentTarget" in value);
}

function setHidden(element, hidden) {
    if (element) element.hidden = hidden;
}

function setText(element, text) {
    if (element) element.textContent = text;
}

async function api(path, options = {}) {
    const isFormData = typeof FormData !== "undefined" && options.body instanceof FormData;
    const headers = {
        "Authorization": `Bearer ${sessionToken}`,
        ...(options.headers || {}),
    };
    const hasContentType = Object.keys(headers).some((name) => name.toLowerCase() === "content-type");
    if (!isFormData && options.body != null && !hasContentType) {
        headers["Content-Type"] = "application/json";
    }

    const response = await fetch(apiUrl(path), {
        ...options,
        headers,
    });
    const text = await response.text();
    let data = null;
    if (text) {
        try {
            data = JSON.parse(text);
        } catch {
            data = {detail: text};
        }
    }
    if (!response.ok) {
        const apiError = data?.error && typeof data.error === "object" ? data.error : null;
        const detail = data?.detail;
        const message = apiError?.message || (
            detail && typeof detail === "object"
                ? (detail.message || detail.error || JSON.stringify(detail))
                : (detail || (typeof data?.error === "string" ? data.error : "") || `HTTP ${response.status}`)
        );
        const error = new Error(message);
        error.status = response.status;
        error.code = apiError?.code;
        if (!error.code && detail && typeof detail === "object") {
            error.code = detail.code;
        }
        error.correlationId = apiError?.correlationId || response.headers.get("X-Correlation-ID") || "";
        error.retryable = Boolean(apiError?.retryable);
        throw error;
    }
    return data;
}

function extensionRequest(type, payload = {}, timeoutMs = 15000) {
    return new Promise((resolve, reject) => {
        if (!extensionConnected) {
            reject(new Error("Madhushala Excise Chrome extension is not installed or not reloaded."));
            return;
        }

        const requestId = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
        const timeout = setTimeout(() => {
            extensionRequests.delete(requestId);
            reject(new Error("Extension did not respond."));
        }, timeoutMs);
        extensionRequests.set(requestId, {resolve, reject, timeout});
        window.postMessage({source: "madhushala-web", requestId, type, payload}, window.location.origin);
    });
}

window.addEventListener("message", (event) => {
    if (event.source !== window) return;
    const message = event.data || {};
    if (message.source !== "madhushala-extension") return;

    if (message.type === "READY") {
        extensionConnected = true;
        return;
    }

    const pending = extensionRequests.get(message.requestId);
    if (!pending) return;
    clearTimeout(pending.timeout);
    extensionRequests.delete(message.requestId);
    const response = message.response || {};
    if (message.ok && response.ok !== false) {
        pending.resolve(response.result ?? response);
    } else {
        pending.reject(new Error(response.error || message.error || "Extension action failed"));
    }
});
window.postMessage({source: "madhushala-web", type: "DISCOVER"}, window.location.origin);

function setStatus(message, isError = false) {
    const element = document.getElementById("status-message");
    if (!element) return;
    element.textContent = message;
    element.className = isError ? "status-message error" : "status-message ok";
}

function showToast(message, type = "info") {
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3500);
}

async function initLaunch() {
    if (!sessionToken || !sessionId) {
        setStatus("Open this page from the Madhushala CRM Import button.", true);
        return;
    }

    try {
        const session = await api("/session/status");
        const sessionInfo = document.getElementById("session-info");
        if (sessionInfo) {
            sessionInfo.textContent = `Shop ${session.shopCode} | Session expires ${new Date(session.expiresAt).toLocaleString()}`;
        }

        await new Promise((resolve) => setTimeout(resolve, 350));
        const mappingUrl = `${window.location.origin}${basePath}/?view=mapping&sessionId=${encodeURIComponent(sessionId)}#session=${encodeURIComponent(sessionToken)}`;
        await extensionRequest("SET_SESSION", {
            bridgeUrl: `${window.location.origin}${basePath}`,
            sessionId,
            sessionToken,
            mappingUrl,
        });
        const settings = await extensionRequest("GET_SETTINGS");
        applyPortalSettings(settings);
        const missing = missingPortalFields(settings);
        if (missing.length) {
            setStatus(`Fill ${missing.join(", ")} once, then open the portal.`, true);
        } else {
            setStatus("Ready. Open Excise Portal and enter CAPTCHA manually.");
        }
    } catch (error) {
        setStatus(error.message || "CRM session could not be opened.", true);
    }
}

async function openPortal() {
    const portalSettings = portalSettingsFromForm();
    const missing = missingPortalFields(portalSettings);
    if (missing.length) {
        setStatus(`Fill ${missing.join(", ")} before opening the portal.`, true);
        return;
    }
    try {
        await extensionRequest("SAVE_SETTINGS", portalSettings);
        const result = await extensionRequest("OPEN_PORTAL", {}, 30000);
        if (result.status === "needs_credentials") {
            setStatus("Fill and save Excise portal details, then click Open Excise Portal again.", true);
            return;
        }
        setStatus("WB Excise opened with saved details. Enter CAPTCHA, login, then prepare indent.");
    } catch (error) {
        setStatus(error.message || "Could not open Excise portal.", true);
    }
}

function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function compactValue(value) {
    const text = String(value ?? "").trim();
    return text && text !== "0" && text !== "0.00" ? text : "";
}

function selectedExciseDetails(item) {
    const captured = item?.capturedItem || item || {};
    const ml = compactValue(captured.measureMl || captured.ml);
    const packageType = compactValue(captured.packageType);
    const pack = compactValue(captured.bottlesPerCase || captured.packing);
    const meta = [ml, packageType, pack].filter(Boolean).map(escapeHtml).join(" • ");
    const chips = [
        [captured.mrpPerUnit, "MRP per unit"],
        [captured.retailerMargin, "Retailer margin"],
        [captured.roundOffGovt, "Government round off"],
        [captured.specialPurposeFee, "Special purpose fee"],
    ]
        .map(([value, title]) => [compactValue(value), title])
        .filter(([value]) => value)
        .map(([value, title]) => `<span class="excise-value-chip" title="${escapeHtml(title)}">${escapeHtml(value)}</span>`)
        .join("");
    if (!meta && !chips) return "";
    return `<span class="excise-expanded"><span class="excise-meta" title="ML, package type, bottles per case">${meta}</span>${chips ? `<span class="excise-values">${chips}</span>` : ""}</span>`;
}

function itemLabel(item) {
    return `${item.itemCode} - ${item.itemName}`;
}

function parseNumber(value) {
    const match = String(value || "").match(/\d+/);
    return match ? Number(match[0]) : 0;
}

function findMadhushalaItem(itemCode) {
    return workspace.madhushalaItems.find((item) => String(item.itemCode) === String(itemCode));
}

function guardrailIssues(exciseItem, madhushalaItem, score = null) {
    const issues = [];
    const leftMl = parseNumber(exciseItem?.capturedItem?.measureMl || exciseItem?.itemName);
    const rightMl = parseNumber(madhushalaItem?.ml || madhushalaItem?.itemName);
    const leftPack = parseNumber(exciseItem?.capturedItem?.bottlesPerCase);
    const rightPack = parseNumber(madhushalaItem?.packing);

    if (leftMl && rightMl && leftMl !== rightMl) issues.push(`ML mismatch: Excise ${leftMl} ML, Madhushala ${rightMl} ML`);
    if (leftPack && rightPack && leftPack !== rightPack) issues.push(`Pack mismatch: Excise ${leftPack}, Madhushala ${rightPack}`);
    if (score !== null && Number(score) > 0 && Number(score) < 55) issues.push(`Low match confidence: ${Math.round(score)}%`);
    return issues;
}

function showGuardrailModal(issues, onConfirm) {
    pendingGuardrailAction = onConfirm;
    const modal = document.getElementById("guardrail-modal");
    const body = document.getElementById("guardrail-body");
    if (!modal || !body) return;
    body.innerHTML = `<p>This match looks risky. Please check before saving.</p><ul>${issues.map((issue) => `<li>${issue}</li>`).join("")}</ul>`;
    modal.hidden = false;
}

function closeGuardrailModal() {
    setHidden(document.getElementById("guardrail-modal"), true);
    pendingGuardrailAction = null;
}

function mappedItemForRow(item) {
    const code = selectedMappings.get(String(item.exciseItemCode)) || item.selectedItemCode || "";
    if (!code) return null;
    return findMadhushalaItem(code) || item.selectedItem || {itemCode: code, itemName: "Mapped item"};
}

function mappedItemMarkup(item) {
    const mapped = mappedItemForRow(item);
    if (!mapped) return `<span class="mapped-choice empty">Select item</span>`;
    return `<span class="mapped-choice done"><strong>${escapeHtml(mapped.itemCode || "")}</strong><span>${escapeHtml(mapped.itemName || "Mapped item")}</span></span>`;
}
function currentExciseItem() {
    return workspace.unmappedItems.find((item) => String(item.exciseItemCode) === String(selectedExciseCode));
}

function renderWorkspace() {
    const list = document.getElementById("unmapped-items");
    if (!list) return;
    setText(document.getElementById("unmapped-count"), String(workspace.unmappedItems.length));

    if (!workspace.unmappedItems.length) {
        list.className = "list-body empty";
        list.textContent = "No unmapped items";
        renderSelectedExcise(null);
        updateSummary();
        return;
    }

    if (!selectedExciseCode && workspace.unmappedItems[0]) {
        selectedExciseCode = String(workspace.unmappedItems[0].exciseItemCode);
    }
    if (selectedExciseCode && !currentExciseItem()) {
        selectedExciseCode = workspace.unmappedItems[0] ? String(workspace.unmappedItems[0].exciseItemCode) : null;
    }

    const documentModeRows = Boolean(workspace.documentMapping || currentDocumentJobId);
    list.className = documentModeRows ? "list-body document-map-list" : "list-body";
    list.innerHTML = workspace.unmappedItems.map((item) => {
        const code = String(item.exciseItemCode);
        const selected = code === String(selectedExciseCode);
        const mapped = selectedMappings.get(code) || item.selectedItemCode;
        if (documentModeRows) {
            return `
                <button class="unmapped-item document-map-row ${selected ? "selected" : ""}" data-excise="${escapeHtml(code)}" type="button">
                    <span class="doc-extracted"><small>${escapeHtml(code || "New")}</small><strong>${escapeHtml(item.itemName)}</strong>${selectedExciseDetails(item)}</span>
                    <span class="doc-arrow" aria-hidden="true">→</span>
                    ${mappedItemMarkup(item)}
                </button>`;
        }
        return `
            <button class="unmapped-item ${selected ? "selected" : ""}" data-excise="${escapeHtml(code)}" type="button">
                <span class="item-code">${escapeHtml(code)}</span>
                <span class="item-name">${escapeHtml(item.itemName)}</span>
                <span class="${mapped ? "map-badge done" : "map-badge"}">${mapped ? "Selected" : "Pending"}</span>
                ${selectedExciseDetails(item)}
            </button>`;
    }).join("");

    list.querySelectorAll("[data-excise]").forEach((button) => {
        button.addEventListener("click", () => {
            selectedExciseCode = button.dataset.excise;
            const search = document.getElementById("madhushala-search");
            if (search) search.value = "";
            renderWorkspace();
        });
    });


    renderSelectedExcise(currentExciseItem());
    updateSummary();
}

function renderSelectedExcise(item) {
    const card = document.getElementById("best-match-card");

    if (!item) {
        renderCandidates([]);
        setHidden(card, true);
        return;
    }

    const best = item.suggestions?.[0];
    if (best) {
        if (!card) return;
        card.hidden = false;
        card.innerHTML = `
            <div>
                <span class="eyebrow">Best Match</span>
                <h3>${itemLabel(best.item)}</h3>
                <p>ML ${best.item.ml || "-"} | ${Math.round(best.score)}%</p>
            </div>
            <button type="button" id="confirm-best-match">Correct</button>
            <button type="button" id="choose-another" class="secondary">Change</button>`;
        document.getElementById("confirm-best-match")?.addEventListener("click", () => selectMadhushalaItem(best.item.itemCode, best.score));
        document.getElementById("choose-another")?.addEventListener("click", () => document.getElementById("madhushala-search")?.focus());
    } else {
        setHidden(card, true);
    }

    renderCandidates(item.suggestions || []);
}

function renderCandidates(entries) {
    const container = document.getElementById("suggestions");
    if (!container) return;
    if (!entries.length) {
        container.className = "candidate-list empty";
        container.textContent = "No results";
        return;
    }
    container.className = "candidate-list";
    container.innerHTML = entries.map((entry) => {
        const item = entry.item || entry;
        const score = entry.score ? `<span class="score">${Math.round(entry.score)}%</span>` : "";
        return `
            <button class="candidate" data-code="${item.itemCode}" data-score="${entry.score || 0}" type="button">
                <span><strong>${itemLabel(item)}</strong><small>ML ${item.ml || "-"} | Pack ${item.packing || "-"}</small></span>
                ${score}
            </button>`;
    }).join("");
    container.querySelectorAll("[data-code]").forEach((button) => {
        button.addEventListener("click", () => selectMadhushalaItem(button.dataset.code, button.dataset.score));
    });
}

function selectMadhushalaItem(itemCode, score = null) {
    const exciseItem = currentExciseItem();
    const madhushalaItem = findMadhushalaItem(itemCode);
    const issues = guardrailIssues(exciseItem, madhushalaItem, score);
    const apply = () => {
        selectedMappings.set(String(selectedExciseCode), String(itemCode));
        showToast("Selected", "success");
        renderWorkspace();
    };
    if (issues.length) showGuardrailModal(issues, apply);
    else apply();
}

function normalizeSearchText(value) {
    return String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").replace(/\s+/g, " ").trim();
}

function itemInitials(itemName) {
    return normalizeSearchText(itemName).split(" ").filter(Boolean).map((word) => word[0]).join("");
}

function scoreSearch(item, query) {
    const clean = normalizeSearchText(query);
    if (!clean) return 0;
    const name = normalizeSearchText(item.itemName);
    const compact = name.replace(/\s/g, "");
    const code = normalizeSearchText(item.itemCode);
    let score = 0;
    if (code === clean) score += 120;
    if (code.startsWith(clean)) score += 85;
    if (name.startsWith(clean)) score += 100;
    if (compact.startsWith(clean.replace(/\s/g, ""))) score += 80;
    if (itemInitials(item.itemName).startsWith(clean.replace(/\s/g, ""))) score += 90;
    if (clean.length >= 3 && name.includes(clean)) score += 45;
    return score;
}

function runSearch() {
    const query = document.getElementById("madhushala-search")?.value || "";
    if (!query.trim()) {
        renderSelectedExcise(currentExciseItem());
        return;
    }
    const results = workspace.madhushalaItems
        .map((item) => ({item, score: scoreSearch(item, query)}))
        .filter((entry) => entry.score > 0)
        .sort((left, right) => right.score - left.score)
        .slice(0, 50);
    renderCandidates(results);
}

function updateSummary() {
    const left = workspace.unmappedItems.filter((item) => !selectedMappings.get(String(item.exciseItemCode)) && !item.selectedItemCode).length;
    setText(document.getElementById("mapping-summary"), `${workspace.documentMapping ? "Document rows" : "Selected"}: ${selectedMappings.size} | Left: ${left}`);
    const submit = document.getElementById("submit-mappings");
    if (submit) submit.disabled = selectedMappings.size === 0;
}

async function loadWorkspace(jobId = currentDocumentJobId, options = {}) {
    const normalizedJobId = sanitizeJobId(isEventLike(jobId) ? "" : jobId || currentDocumentJobId);
    const preserveState = options.preserveState !== false;
    const previousSelected = preserveState ? selectedExciseCode : null;
    const search = document.getElementById("madhushala-search");
    const previousSearch = preserveState ? search?.value || "" : "";
    try {
        const query = normalizedJobId ? `?jobId=${encodeURIComponent(normalizedJobId)}` : "?latestOnly=true";
        workspace = await api(`/mapping/workspace${query}`);
        currentDocumentJobId = normalizedJobId || currentDocumentJobId;
        const codes = new Set((workspace.unmappedItems || []).map((item) => String(item.exciseItemCode)));
        selectedExciseCode = previousSelected && codes.has(String(previousSelected)) ? previousSelected : null;
        if (search) search.value = previousSearch;
        renderWorkspace();
        if (previousSearch) runSearch();
    } catch (error) {
        if (!options.quiet) {
            const authExpired = error?.code === "MADHUSHALA_AUTH_EXPIRED"
                || String(error?.message || "").includes("Madhushala login/JWT is expired");
            showToast(
                authExpired
                    ? "Madhushala login expired. Please reopen Excise Import from Madhushala CRM, then continue this import."
                    : (error.message || "Could not load mapping"),
                "error",
            );
        }
    }
}

function startMappingAutoRefresh() {
    if (mappingRefreshTimer) return;
    mappingRefreshTimer = window.setInterval(async () => {
        if (mappingRefreshInFlight || document.getElementById("mapping-view")?.hidden) return;
        mappingRefreshInFlight = true;
        try {
            await loadWorkspace(currentDocumentJobId, {quiet: true, preserveState: true});
        } finally {
            mappingRefreshInFlight = false;
        }
    }, 3000);
}

function stopMappingAutoRefresh() {
    if (mappingRefreshTimer) window.clearInterval(mappingRefreshTimer);
    mappingRefreshTimer = null;
    mappingRefreshInFlight = false;
}

async function saveMappings() {
    const mappings = Array.from(selectedMappings.entries()).map(([exciseItemCode, itemCode]) => ({
        exciseItemCode: Number(exciseItemCode),
        itemCode,
    }));
    try {
        const result = await api("/mapping/submit", {method: "POST", body: JSON.stringify({mappings, jobId: currentDocumentJobId || null})});
        selectedMappings.clear();
        showToast(`Saved ${result.mappedCount}`, "success");
        await loadWorkspace(currentDocumentJobId);
        if (sanitizeJobId(currentDocumentJobId)) {
            setHidden(document.getElementById("save-purchase-from-mapping"), false);
        }
    } catch (error) {
        showToast(error.message || "Could not save mapping", "error");
    }
}

function initMapping() {
    stopMappingAutoRefresh();
    setHidden(document.getElementById("launch-view"), true);
    setHidden(document.getElementById("document-import-view"), true);
    setHidden(document.getElementById("mapping-view"), false);
    setHidden(document.getElementById("save-purchase-from-mapping"), !sanitizeJobId(currentDocumentJobId));
    if (!sessionToken) {
        showToast("Invalid or missing CRM session.", "error");
        return;
    }
    void loadWorkspace(currentDocumentJobId, {preserveState: false});
    startMappingAutoRefresh();
}

function documentElements() {
    return {
        upload: document.getElementById("document-upload-panel"),
        processing: document.getElementById("document-processing-panel"),
        review: document.getElementById("document-review-panel"),
        success: document.getElementById("document-success-panel"),
        error: document.getElementById("document-error-panel"),
        action: document.getElementById("document-action-bar"),
        pdfFile: document.getElementById("pdf-file"),
        imageFile: document.getElementById("image-file"),
        status: document.getElementById("document-status"),
        errorMessage: document.getElementById("document-error-message"),
        progressTitle: document.getElementById("document-progress-title"),
        progressDetail: document.getElementById("document-progress-detail"),
    };
}

function setDocumentImportState(state, message = "") {
    const elements = documentElements();
    setHidden(elements.upload, state !== "idle" && state !== "selected");
    setHidden(elements.processing, state !== "uploading" && state !== "extracting" && state !== "normalizing" && state !== "checking");
    setHidden(elements.review, state !== "review");
    setHidden(elements.success, state !== "complete");
    setHidden(elements.error, state !== "error");
    setHidden(elements.action, state !== "review");
    const uploadDisabled = state === "uploading" || state === "extracting" || state === "normalizing" || state === "checking";
    if (elements.pdfFile) elements.pdfFile.disabled = uploadDisabled;
    if (elements.imageFile) elements.imageFile.disabled = uploadDisabled;

    if (elements.status) {
        elements.status.textContent = message;
        elements.status.className = state === "error" ? "status-message error" : "status-message ok";
    }

    const stages = document.querySelectorAll(".document-stages li");
    const stageOrder = ["uploading", "extracting", "normalizing", "checking"];
    const activeIndex = stageOrder.indexOf(state);
    stages.forEach((stage) => {
        const active = stage.dataset.stage === state;
        stage.classList.toggle("active", active);
        stage.classList.toggle("done", activeIndex > -1 && stageOrder.indexOf(stage.dataset.stage) < activeIndex);
    });
}

function formatBytes(bytes) {
    if (!bytes) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function revokeDocumentPreview() {
    currentPreviewUrls.forEach((url) => {
        try { URL.revokeObjectURL(url); } catch {}
    });
    currentPreviewUrls = [];
    currentPreviewUrl = "";
    currentPreviewIndex = 0;
}

function currentPreviewFile() {
    return currentDocumentFiles[currentPreviewIndex] || currentDocumentFile || null;
}

function renderPreviewFileList() {
    const container = document.getElementById("document-file-tabs");
    if (!container) return;
    if (currentDocumentFiles.length <= 1) {
        container.innerHTML = "";
        container.hidden = true;
        return;
    }
    container.hidden = false;
    container.innerHTML = currentDocumentFiles.map((file, index) => {
        const active = index === currentPreviewIndex ? " active" : "";
        return '<button type="button" class="document-file-tab' + active + '" data-preview-index="' + index + '">' +
            '<span>' + (index + 1) + '</span>' +
            '<strong>' + escapeHtml(file.name) + '</strong>' +
            '</button>';
    }).join("");
}

function renderCurrentDocumentPreview() {
    const file = currentPreviewFile();
    const preview = document.getElementById("document-preview");
    if (!preview || !file) return;
    currentPreviewUrl = currentPreviewUrls[currentPreviewIndex] || "";
    const isPdf = file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
    preview.innerHTML = isPdf
        ? '<iframe title="Uploaded PDF preview" src="' + currentPreviewUrl + '#toolbar=1&navpanes=0"></iframe>'
        : '<img alt="Uploaded document preview" src="' + currentPreviewUrl + '">';
    setText(document.getElementById("review-filename"), file.name);
    setText(document.getElementById("review-filetype"), file.type || file.name.split(".").pop().toUpperCase());
    setText(document.getElementById("review-filesize"), formatBytes(file.size));
    renderPreviewFileList();
}

function renderDocumentPreviews(files) {
    revokeDocumentPreview();
    currentDocumentFiles = Array.from(files || []);
    currentDocumentFile = currentDocumentFiles[0] || null;
    currentPreviewUrls = currentDocumentFiles.map((file) => URL.createObjectURL(file));
    currentPreviewIndex = 0;
    renderCurrentDocumentPreview();
}

function setPreviewIndex(index) {
    const next = Number(index);
    if (!Number.isInteger(next) || next < 0 || next >= currentDocumentFiles.length) return;
    currentPreviewIndex = next;
    renderCurrentDocumentPreview();
    if (!document.getElementById("document-viewer-modal")?.hidden) {
        renderDocumentViewer();
    }
}

function renderDocumentViewer() {
    const file = currentPreviewFile();
    const stage = document.getElementById("document-viewer-stage");
    if (!file || !stage) return;
    const url = currentPreviewUrls[currentPreviewIndex] || "";
    const isPdf = file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
    stage.innerHTML = isPdf
        ? '<iframe title="Full document preview" src="' + url + '#toolbar=1&navpanes=1"></iframe>'
        : '<img alt="Full document preview" src="' + url + '">';
    setText(document.getElementById("document-viewer-title"), file.name);
    setText(document.getElementById("document-viewer-count"), currentDocumentFiles.length > 1
        ? ((currentPreviewIndex + 1) + " of " + currentDocumentFiles.length)
        : "Source preview");
    const previous = document.getElementById("document-viewer-prev");
    const next = document.getElementById("document-viewer-next");
    if (previous) previous.disabled = currentPreviewIndex <= 0;
    if (next) next.disabled = currentPreviewIndex >= currentDocumentFiles.length - 1;
}

function openDocumentViewer() {
    if (!currentPreviewFile()) {
        showToast("No local PDF/image preview is available.", "error");
        return;
    }
    const modal = document.getElementById("document-viewer-modal");
    if (!modal) return;
    renderDocumentViewer();
    modal.hidden = false;
    document.body.classList.add("document-viewer-open");
}

function closeDocumentViewer() {
    const modal = document.getElementById("document-viewer-modal");
    if (modal) modal.hidden = true;
    document.body.classList.remove("document-viewer-open");
}

function safeSourceUrl(value) {
    try {
        const parsed = new URL(String(value || ""));
        return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : "";
    } catch {
        return "";
    }
}

function reviewItemsFromPayload(payload) {
    const review = Array.isArray(payload?.reviewItems) ? payload.reviewItems : [];
    if (review.length) {
        return review.map((item) => ({
            id: String(item.id || ""),
            name: String(item.name || ""),
            brand: String(item.brand || item.name || ""),
            ml: item.ml ?? "",
            batchNo: String(item.batchNo || ""),
            box: item.box ?? 0,
            loose: item.loose ?? 0,
            sourceFile: String(item.sourceFile || ""),
            issues: Array.isArray(item.issues) ? item.issues : [],
        }));
    }
    return (payload?.normalizedItems || []).map((item, index) => ({
        id: String(item.id || item.sourceItemId || ("review-" + (index + 1))),
        name: String(item.rawName || item.itemName || ""),
        brand: String(item.brand || item.rawName || item.itemName || ""),
        ml: item.ml ?? "",
        batchNo: String(item.batchNo || item.rawData?.batchNo || item.rawData?.batch || ""),
        box: item.box ?? 0,
        loose: item.loose ?? 0,
        sourceFile: String(item.rawData?.sourceFilename || ""),
        issues: [],
    }));
}

function validateReviewItem(item) {
    const issues = [];
    const name = String(item.name || "").trim();
    const brand = String(item.brand || "").trim();
    const ml = Number(item.ml);
    const box = Number(item.box);
    const loose = Number(item.loose);
    if (!name) issues.push("Name is required");
    if (!brand) issues.push("Brand is required");
    if (!Number.isInteger(ml) || ml <= 0) issues.push("ML must be a positive whole number");
    if (!Number.isInteger(box) || box < 0) issues.push("Box/Cases must be a non-negative whole number");
    if (!Number.isInteger(loose) || loose < 0) issues.push("Loose/Bottles must be a non-negative whole number");
    if (Number.isInteger(box) && Number.isInteger(loose) && box === 0 && loose === 0) {
        issues.push("Enter at least one case/box or loose bottle");
    }
    return issues;
}

function collectReviewItems() {
    const rows = document.querySelectorAll("#document-items-table-body tr[data-review-id]");
    return Array.from(rows).map((row) => ({
        id: row.dataset.reviewId || "",
        name: row.querySelector('[data-field="name"]')?.value || "",
        brand: row.querySelector('[data-field="brand"]')?.value || "",
        ml: row.querySelector('[data-field="ml"]')?.value || "",
        batchNo: row.querySelector('[data-field="batchNo"]')?.value || "",
        box: row.querySelector('[data-field="box"]')?.value || "0",
        loose: row.querySelector('[data-field="loose"]')?.value || "0",
    }));
}

function updateReviewValidation() {
    const items = collectReviewItems();
    let invalid = 0;
    document.querySelectorAll("#document-items-table-body tr[data-review-id]").forEach((row, index) => {
        const issues = validateReviewItem(items[index] || {});
        row.classList.toggle("invalid-row", issues.length > 0);
        const target = row.querySelector(".document-row-issues");
        if (target) target.textContent = issues.join(" • ");
        const status = row.querySelector(".row-status");
        if (status) {
            status.textContent = issues.length ? "Needs attention" : "Ready";
            status.className = "row-status " + (issues.length ? "issue" : "valid");
        }
        const current = items[index] || {};
        row.dataset.searchText = [
            current.name, current.brand, current.ml, current.batchNo, current.box, current.loose,
            row.querySelector("[data-source-file]")?.dataset.sourceFile || "",
        ].join(" ");
        if (issues.length) invalid += 1;
    });
    const valid = Math.max(0, items.length - invalid);
    setText(document.getElementById("metric-detected"), String(items.length));
    setText(document.getElementById("metric-recognized"), String(valid));
    setText(document.getElementById("metric-unmapped"), String(invalid));

    const message = document.getElementById("document-review-validation");
    if (message) {
        message.textContent = invalid
            ? (invalid + " row" + (invalid === 1 ? "" : "s") + " need correction before mapping.")
            : "All extracted rows are valid. You can continue to mapping.";
        message.className = "document-review-validation " + (invalid ? "error" : "ok");
    }
    const continueButton = document.getElementById("continue-document-mapping");
    if (continueButton) continueButton.disabled = Boolean(invalid || !items.length);
    return {items, invalid};
}

function applyReviewFilter() {
    const query = String(document.getElementById("document-review-search")?.value || "").trim().toLowerCase();
    const issuesOnly = Boolean(document.getElementById("document-review-issues-only")?.checked);
    document.querySelectorAll("#document-items-table-body tr[data-review-id]").forEach((row) => {
        const haystack = String(row.dataset.searchText || "").toLowerCase();
        const hasIssues = row.classList.contains("invalid-row");
        row.hidden = Boolean((query && !haystack.includes(query)) || (issuesOnly && !hasIssues));
    });
}

function renderReviewTable(items) {
    currentReviewItems = items;
    const tbody = document.getElementById("document-items-table-body");
    if (!tbody) return;
    tbody.innerHTML = items.map((item, index) => {
        const initialIssues = validateReviewItem(item);
        const source = item.sourceFile || "";
        const searchText = [item.name, item.brand, item.ml, item.batchNo, item.box, item.loose, source].join(" ");
        return '<tr data-review-id="' + escapeHtml(item.id) + '" data-search-text="' + escapeHtml(searchText) + '" class="' + (initialIssues.length ? "invalid-row" : "") + '">' +
            '<td class="row-number-cell"><span class="row-number">' + (index + 1) + '</span></td>' +
            '<td class="name-cell"><input data-field="name" type="text" value="' + escapeHtml(item.name) + '" maxlength="300" aria-label="Product name"><span class="document-row-issues">' + escapeHtml(initialIssues.join(" • ")) + '</span></td>' +
            '<td class="brand-cell"><input data-field="brand" type="text" value="' + escapeHtml(item.brand) + '" maxlength="300" aria-label="Brand"></td>' +
            '<td class="number-cell"><input data-field="ml" type="number" min="1" step="1" value="' + escapeHtml(item.ml) + '" aria-label="ML"></td>' +
            '<td class="batch-cell"><input data-field="batchNo" type="text" value="' + escapeHtml(item.batchNo || "") + '" maxlength="200" aria-label="Batch No. and Date"></td>' +
            '<td class="number-cell"><input data-field="box" type="number" min="0" step="1" value="' + escapeHtml(item.box) + '" aria-label="Box or cases"></td>' +
            '<td class="number-cell"><input data-field="loose" type="number" min="0" step="1" value="' + escapeHtml(item.loose) + '" aria-label="Loose bottles"></td>' +
            '<td class="source-cell"><button type="button" class="source-jump" data-source-file="' + escapeHtml(source) + '" title="Open source file">' +
                (source ? escapeHtml(source) : "Source") +
            '</button></td>' +
            '<td class="status-cell"><span class="row-status ' + (initialIssues.length ? "issue" : "valid") + '">' +
                (initialIssues.length ? "Needs attention" : "Ready") +
            '</span></td>' +
            '</tr>';
    }).join("");
    updateReviewValidation();
    applyReviewFilter();
}

function renderReviewSource(payload) {
    currentSourceUrl = safeSourceUrl(payload?.finalUrl || payload?.url || "");
    const panel = document.getElementById("review-source-panel");
    const link = document.getElementById("review-source-link");
    setHidden(panel, !currentSourceUrl);
    if (link) {
        link.href = currentSourceUrl || "#";
        link.textContent = currentSourceUrl || "";
    }
    if (currentUploadKind === "qr" && !currentDocumentFile) {
        const preview = document.getElementById("document-preview");
        if (preview) {
            preview.innerHTML = currentSourceUrl
                ? '<div class="document-source-placeholder">QR source page extracted. Use “Open Source Page” to visually verify it.</div>'
                : '<div class="document-source-placeholder">QR source page preview is unavailable.</div>';
        }
        setText(document.getElementById("review-filename"), currentSourceUrl || "QR source");
        setText(document.getElementById("review-filetype"), "QR URL");
        setText(document.getElementById("review-filesize"), "-");
    }
}

function renderDocumentReview(payload) {
    const job = payload.job || {};
    currentDocumentResult = payload;
    if (!["pdf", "image", "qr"].includes(currentUploadKind)) currentUploadKind = "pdf";
    currentDocumentJobId = job.id || currentDocumentJobId;
    const items = reviewItemsFromPayload(payload);
    renderReviewTable(items);
    renderReviewSource(payload);
    setText(document.getElementById("document-action-summary"), items.length + " products extracted • Verify Name, Brand, ML, Box/Cases and Loose/Bottles before mapping");
    setText(document.getElementById("continue-document-mapping"), "Confirm & Continue to Mapping");
    setHidden(document.getElementById("save-purchase"), true);
    setHidden(document.getElementById("continue-document-mapping"), false);
    applyPurchaseHeader(loadPurchaseHeader(job.id));
    if (payload.extractedDocument?.invoiceDate) setInputValue("purchase-doc-date", payload.extractedDocument.invoiceDate);
    if (payload.extractedDocument?.invoiceNumber) setInputValue("purchase-doc-no", payload.extractedDocument.invoiceNumber);
    setText(document.getElementById("document-json"), JSON.stringify(payload, null, 2));
    updateReviewValidation();
}
function resetDocumentImport() {
    revokeDocumentPreview();
    currentDocumentFile = null;
    currentDocumentFiles = [];
    currentDocumentResult = null;
    currentDocumentJobId = "";
    currentUploadKind = "pdf";
    currentReviewItems = [];
    currentSourceUrl = "";
    const pdfInput = document.getElementById("pdf-file");
    if (pdfInput) pdfInput.value = "";
    const imageInput = document.getElementById("image-file");
    if (imageInput) imageInput.value = "";
    const qrInput = document.getElementById("qr-file");
    if (qrInput) qrInput.value = "";
    setHidden(document.getElementById("save-purchase"), true);
    setHidden(document.getElementById("continue-document-mapping"), false);
    setHidden(document.getElementById("review-source-panel"), true);
    const tbody = document.getElementById("document-items-table-body");
    if (tbody) tbody.innerHTML = "";
    setDocumentImportState("idle", "Ready. Select a purchase PDF, purchase image, or QR image.");
}

async function uploadDocuments(files, kind = "pdf") {
    const selectedFiles = Array.from(files || []).filter(Boolean);
    if (!selectedFiles.length) return;
    if (!sessionToken) {
        setDocumentImportState("error", "Open this page from the Madhushala CRM Import PDF / Image button.");
        setText(documentElements().errorMessage, "Missing or expired CRM session.");
        return;
    }

    currentUploadKind = kind;
    currentDocumentJobId = "";
    currentDocumentResult = null;
    currentReviewItems = [];
    const existingBody = document.getElementById("document-items-table-body");
    if (existingBody) existingBody.innerHTML = "";
    renderDocumentPreviews(selectedFiles);
    const form = new FormData();
    selectedFiles.forEach((file) => form.append("files", file));

    setDocumentImportState("uploading");
    const elements = documentElements();
    setText(elements.progressTitle, selectedFiles.length > 1 ? "Uploading purchase documents" : "Uploading document");
    setText(
        elements.progressDetail,
        selectedFiles.length > 1
            ? ("Uploading " + selectedFiles.length + " files as one purchase and validating their document identity.")
            : "Sending the selected document securely to the bridge."
    );

    try {
        window.setTimeout(() => {
            if (document.getElementById("document-processing-panel")?.hidden) return;
            setDocumentImportState("extracting");
            setText(elements.progressTitle, "Extracting purchase data");
            setText(elements.progressDetail, "Checking PyMuPDF coverage first and using LlamaParse automatically when extraction is incomplete.");
        }, 250);

        const payload = await api("/api/v1/document-import/upload/batch/" + encodeURIComponent(kind), {
            method: "POST",
            body: form,
        });
        if (!payload?.job) throw new Error(payload.detail || payload.error || "Document import failed");
        renderDocumentReview(payload);
        const engines = (payload.extraction?.files || [])
            .map((entry) => entry.engine)
            .filter(Boolean);
        const engineText = engines.length ? Array.from(new Set(engines)).join(" + ") : (payload.extraction?.engine || "unknown");
        setText(
            document.getElementById("document-action-summary"),
            (document.getElementById("document-action-summary")?.textContent || "") +
            " • " + selectedFiles.length + " source file" + (selectedFiles.length === 1 ? "" : "s") +
            " • Extractor: " + engineText
        );
        setDocumentImportState("review");
    } catch (error) {
        setText(documentElements().errorMessage, error.message || "Document import failed.");
        setDocumentImportState("error");
    }
}

async function uploadDocument(file, kind = "pdf") {
    return uploadDocuments([file], kind);
}

async function initDocumentImport() {
    stopMappingAutoRefresh();
    setHidden(document.getElementById("launch-view"), true);
    setHidden(document.getElementById("mapping-view"), true);
    setHidden(document.getElementById("document-import-view"), false);
    setDocumentImportState("idle", "Ready. Select a purchase PDF, purchase image, or QR image.");
    if (!sessionToken) {
        setDocumentImportState("error", "Open this page from the Madhushala CRM Import PDF / Image button.");
        setText(documentElements().errorMessage, "Missing or expired CRM session.");
        return;
    }
    try {
        const session = await api("/session/status");
        setText(document.getElementById("document-session-info"), `Shop ${session.shopCode}`);
    } catch (error) {
        setText(documentElements().errorMessage, error.message || "CRM session could not be opened.");
        setDocumentImportState("error");
    }
}

document.getElementById("open-portal")?.addEventListener("click", openPortal);
document.getElementById("madhushala-search")?.addEventListener("input", runSearch);
document.getElementById("submit-mappings")?.addEventListener("click", saveMappings);
document.getElementById("pdf-file")?.addEventListener("click", (event) => {
    event.currentTarget.value = "";
});
document.getElementById("image-file")?.addEventListener("click", (event) => {
    event.currentTarget.value = "";
});
document.getElementById("pdf-file")?.addEventListener("change", (event) => {
    const files = Array.from(event.target.files || []);
    if (files.length) void uploadDocuments(files, "pdf");
});
document.getElementById("image-file")?.addEventListener("change", (event) => {
    const files = Array.from(event.target.files || []);
    if (files.length) void uploadDocuments(files, "image");
});
document.getElementById("qr-file")?.addEventListener("change", (event) => {
    const [file] = event.target.files || [];
    if (file) uploadQr(file);
});
document.getElementById("qr-link-submit")?.addEventListener("click", () => {
    void submitQrLink();
});
document.getElementById("qr-link-input")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
        event.preventDefault();
        void submitQrLink();
    }
});
function bindDropzone(id, handler, multiple = false) {
    const zone = document.getElementById(id);
    zone?.addEventListener("dragover", (event) => {
        event.preventDefault();
        zone.classList.add("dragover");
    });
    zone?.addEventListener("dragleave", () => zone.classList.remove("dragover"));
    zone?.addEventListener("drop", (event) => {
        event.preventDefault();
        zone.classList.remove("dragover");
        const files = Array.from(event.dataTransfer.files || []);
        if (!files.length) return;
        handler(multiple ? files : files[0]);
    });
}
bindDropzone("pdf-dropzone", (files) => void uploadDocuments(files, "pdf"), true);
bindDropzone("image-dropzone", (files) => void uploadDocuments(files, "image"), true);
bindDropzone("qr-dropzone", uploadQr);
document.getElementById("document-refresh")?.addEventListener("click", resetDocumentImport);
document.getElementById("replace-document")?.addEventListener("click", resetDocumentImport);
document.getElementById("document-upload-another")?.addEventListener("click", resetDocumentImport);
document.getElementById("success-upload-another")?.addEventListener("click", resetDocumentImport);
document.getElementById("choose-another-document")?.addEventListener("click", resetDocumentImport);
document.getElementById("retry-document")?.addEventListener("click", () => {
    if (currentUploadKind === "qr" && currentDocumentFile) {
        void uploadQr(currentDocumentFile);
    } else if (currentDocumentFiles.length) {
        void uploadDocuments(currentDocumentFiles, currentUploadKind === "image" ? "image" : "pdf");
    } else {
        resetDocumentImport();
    }
});
document.getElementById("copy-document-json")?.addEventListener("click", async () => {
    await navigator.clipboard.writeText(document.getElementById("document-json")?.textContent || "");
    showToast("JSON copied", "success");
});
document.getElementById("document-items-table-body")?.addEventListener("input", () => {
    updateReviewValidation();
    applyReviewFilter();
});
document.getElementById("document-items-table-body")?.addEventListener("click", (event) => {
    const button = event.target.closest?.("[data-source-file]");
    if (!button) return;
    const filename = String(button.dataset.sourceFile || "");
    const index = currentDocumentFiles.findIndex((file) => file.name === filename);
    if (index >= 0) {
        setPreviewIndex(index);
        openDocumentViewer();
    }
});
document.getElementById("document-file-tabs")?.addEventListener("click", (event) => {
    const button = event.target.closest?.("[data-preview-index]");
    if (button) setPreviewIndex(Number(button.dataset.previewIndex));
});
document.getElementById("document-review-search")?.addEventListener("input", applyReviewFilter);
document.getElementById("document-review-issues-only")?.addEventListener("change", applyReviewFilter);
document.getElementById("document-fullscreen")?.addEventListener("click", openDocumentViewer);
document.getElementById("document-viewer-close")?.addEventListener("click", closeDocumentViewer);
document.getElementById("document-viewer-backdrop")?.addEventListener("click", closeDocumentViewer);
document.getElementById("document-viewer-prev")?.addEventListener("click", () => setPreviewIndex(currentPreviewIndex - 1));
document.getElementById("document-viewer-next")?.addEventListener("click", () => setPreviewIndex(currentPreviewIndex + 1));
document.getElementById("document-open-new-tab")?.addEventListener("click", () => {
    const target = (currentPreviewUrls[currentPreviewIndex] || currentPreviewUrl) || currentSourceUrl;
    if (!target) {
        showToast("No source preview is available.", "error");
        return;
    }
    window.open(target, "_blank", "noopener,noreferrer");
});
document.getElementById("open-qr-source")?.addEventListener("click", () => {
    if (!currentSourceUrl) {
        showToast("QR source URL is unavailable.", "error");
        return;
    }
    window.open(currentSourceUrl, "_blank", "noopener,noreferrer");
});
document.getElementById("purchase-form")?.addEventListener("input", persistPurchaseHeader);
document.getElementById("save-purchase")?.addEventListener("click", () => {
    void savePurchaseFromJob("review");
});
document.getElementById("save-purchase-from-mapping")?.addEventListener("click", () => {
    void savePurchaseFromJob("mapping");
});
async function confirmReviewAndContinue() {
    const jobId = sanitizeJobId(currentDocumentJobId);
    if (!jobId) {
        showToast("No reviewable import job is available.", "error");
        return;
    }
    const result = updateReviewValidation();
    const items = result.items;
    const invalid = result.invalid;
    if (invalid || !items.length) {
        showToast("Correct the highlighted extracted rows before mapping.", "error");
        return;
    }

    const button = document.getElementById("continue-document-mapping");
    if (button) button.disabled = true;
    persistPurchaseHeader();
    try {
        const payload = await api("/api/v1/document-import/jobs/" + encodeURIComponent(jobId) + "/review/confirm", {
            method: "POST",
            body: JSON.stringify({
                items: items.map((item) => ({
                    id: item.id,
                    name: String(item.name || "").trim(),
                    brand: String(item.brand || "").trim(),
                    ml: Number(item.ml),
                    batchNo: String(item.batchNo || "").trim(),
                    box: Number(item.box),
                    loose: Number(item.loose),
                })),
            }),
        });
        currentDocumentResult = Object.assign({}, currentDocumentResult || {}, payload || {});
        window.location.href = basePath + "/?view=mapping&jobId=" + encodeURIComponent(jobId) + "&sessionId=" + encodeURIComponent(sessionId) + "#session=" + encodeURIComponent(sessionToken);
    } catch (error) {
        showToast(error.message || "Could not confirm extracted products", "error");
        if (button) button.disabled = false;
    }
}

document.getElementById("continue-document-mapping")?.addEventListener("click", () => {
    void confirmReviewAndContinue();
});
document.getElementById("cancel-guardrail")?.addEventListener("click", closeGuardrailModal);
document.getElementById("confirm-guardrail")?.addEventListener("click", () => {
    const action = pendingGuardrailAction;
    closeGuardrailModal();
    if (action) action();
});

document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !document.getElementById("document-viewer-modal")?.hidden) {
        closeDocumentViewer();
    }
});

document.addEventListener("DOMContentLoaded", () => {
    if (documentMode) initDocumentImport();
    else if (mappingMode) initMapping();
    else initLaunch();
});
