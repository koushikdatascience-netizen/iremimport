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
let currentDocumentResult = null;
let currentPreviewUrl = "";
let currentUploadKind = "document";
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

function collectPurchaseHeader() {
    return {
        yearCode: document.getElementById("purchase-year-code")?.value.trim() || "",
        trnDate: document.getElementById("purchase-trn-date")?.value || "",
        docDate: document.getElementById("purchase-doc-date")?.value || "",
        docNo: document.getElementById("purchase-doc-no")?.value.trim() || "",
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
        "purchase-doc-date": header.docDate || today,
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
    const missing = validatePurchaseHeader(header);
    if (missing.length) {
        showToast(`Fill purchase fields first: ${missing.join(", ")}`, "error");
        return;
    }
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
    currentDocumentResult = payload;
    currentDocumentJobId = "";
    setText(document.getElementById("metric-detected"), "1");
    setText(document.getElementById("metric-recognized"), "0");
    setText(document.getElementById("metric-unmapped"), "0");
    setText(document.getElementById("document-action-summary"), `QR link opened: ${payload.finalUrl || payload.url || "extracted"}`);
    setHidden(document.getElementById("purchase-form"), true);
    setHidden(document.getElementById("save-purchase"), true);
    setHidden(document.getElementById("continue-document-mapping"), true);
    setText(document.getElementById("document-json"), JSON.stringify(payload, null, 2));
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
    renderDocumentPreview(file);
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
    const response = await fetch(apiUrl(path), {
        ...options,
        headers: {
            "Content-Type": "application/json",
            "Authorization": `Bearer ${sessionToken}`,
            ...(options.headers || {}),
        },
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
        throw new Error(data?.detail || data?.error || `HTTP ${response.status}`);
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

    list.className = "list-body";
    list.innerHTML = workspace.unmappedItems.map((item) => {
        const code = String(item.exciseItemCode);
        const mapped = selectedMappings.get(code) || item.selectedItemCode;
        return `
            <button class="unmapped-item ${code === String(selectedExciseCode) ? "selected" : ""}" data-excise="${code}" type="button">
                <span class="item-code">${code}</span>
                <span class="item-name">${item.itemName}</span>
                <span class="${mapped ? "map-badge done" : "map-badge"}">${mapped ? "Selected" : "Pending"}</span>
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

    if (!selectedExciseCode && workspace.unmappedItems[0]) {
        selectedExciseCode = String(workspace.unmappedItems[0].exciseItemCode);
        renderWorkspace();
        return;
    }
    if (selectedExciseCode && !currentExciseItem()) {
        selectedExciseCode = workspace.unmappedItems[0] ? String(workspace.unmappedItems[0].exciseItemCode) : null;
    }

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
    setText(document.getElementById("mapping-summary"), `Selected: ${selectedMappings.size} | Left: ${left}`);
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
        if (!options.quiet) showToast(error.message || "Could not load mapping", "error");
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
        await loadWorkspace();
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
        file: document.getElementById("document-file"),
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
    if (elements.file) elements.file.disabled = state === "uploading" || state === "extracting" || state === "normalizing" || state === "checking";

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
    if (currentPreviewUrl) URL.revokeObjectURL(currentPreviewUrl);
    currentPreviewUrl = "";
}

function renderDocumentPreview(file) {
    revokeDocumentPreview();
    currentPreviewUrl = URL.createObjectURL(file);
    const preview = document.getElementById("document-preview");
    if (!preview) return;
    if (file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf")) {
        preview.innerHTML = `<iframe title="Uploaded PDF preview" src="${currentPreviewUrl}"></iframe>`;
    } else {
        preview.innerHTML = `<img alt="Uploaded document preview" src="${currentPreviewUrl}">`;
    }
    setText(document.getElementById("review-filename"), file.name);
    setText(document.getElementById("review-filetype"), file.type || file.name.split(".").pop().toUpperCase());
    setText(document.getElementById("review-filesize"), formatBytes(file.size));
}

function renderDocumentReview(payload) {
    const summary = payload.summary || {};
    const job = payload.job || {};
    currentDocumentResult = payload;
    currentUploadKind = "document";
    currentDocumentJobId = job.id || currentDocumentJobId;
    const detected = summary.detected || job.extracted_count || 0;
    const recognized = summary.recognized || job.mapped_count || 0;
    const needMapping = summary.needMapping || 0;
    setText(document.getElementById("metric-detected"), String(detected));
    setText(document.getElementById("metric-recognized"), String(recognized));
    setText(document.getElementById("metric-unmapped"), String(needMapping));
    setText(document.getElementById("document-action-summary"), `${detected} products extracted | ${recognized} recognized | ${needMapping} require mapping`);
    setText(document.getElementById("continue-document-mapping"), "Continue to Mapping");
    setHidden(document.getElementById("purchase-form"), false);
    setHidden(document.getElementById("save-purchase"), Boolean(needMapping));
    setHidden(document.getElementById("continue-document-mapping"), !needMapping);
    applyPurchaseHeader(loadPurchaseHeader(job.id));
    if (payload.extractedDocument?.invoiceDate) setInputValue("purchase-doc-date", payload.extractedDocument.invoiceDate);
    setText(document.getElementById("document-json"), JSON.stringify(payload, null, 2));
}

function resetDocumentImport() {
    revokeDocumentPreview();
    currentDocumentFile = null;
    currentDocumentResult = null;
    currentDocumentJobId = "";
    currentUploadKind = "document";
    const input = document.getElementById("document-file");
    if (input) input.value = "";
    const qrInput = document.getElementById("qr-file");
    if (qrInput) qrInput.value = "";
    setHidden(document.getElementById("purchase-form"), false);
    setHidden(document.getElementById("save-purchase"), true);
    setHidden(document.getElementById("continue-document-mapping"), false);
    setDocumentImportState("idle", "Ready. Select a purchase document or QR image.");
}

async function uploadDocument(file) {
    if (!sessionToken) {
        setDocumentImportState("error", "Open this page from the Madhushala CRM Import PDF / Image button.");
        setText(documentElements().errorMessage, "Missing or expired CRM session.");
        return;
    }
    currentDocumentFile = file;
    renderDocumentPreview(file);
    const form = new FormData();
    form.append("file", file);
    setDocumentImportState("uploading");
    const elements = documentElements();
    setText(elements.progressTitle, "Uploading document");
    setText(elements.progressDetail, "Sending the selected document securely to the bridge.");
    try {
        window.setTimeout(() => {
            if (document.getElementById("document-processing-panel")?.hidden) return;
            setDocumentImportState("extracting");
            const latestElements = documentElements();
            setText(latestElements.progressTitle, "Extracting products");
            setText(latestElements.progressDetail, "Reading product rows and checking Madhushala mappings.");
        }, 500);
        const response = await fetch(apiUrl("/api/v1/document-import/upload"), {
            method: "POST",
            headers: {"Authorization": `Bearer ${sessionToken}`},
            body: form,
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || payload.error || "Document import failed");
        renderDocumentReview(payload);
        setDocumentImportState("review");
    } catch (error) {
        setText(documentElements().errorMessage, error.message || "Document import failed.");
        setDocumentImportState("error");
    }
}

async function initDocumentImport() {
    stopMappingAutoRefresh();
    setHidden(document.getElementById("launch-view"), true);
    setHidden(document.getElementById("mapping-view"), true);
    setHidden(document.getElementById("document-import-view"), false);
    setDocumentImportState("idle", "Ready. Select a purchase document or QR image.");
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
document.getElementById("document-file")?.addEventListener("change", (event) => {
    const [file] = event.target.files || [];
    if (file) uploadDocument(file);
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
function bindDropzone(id, handler) {
    const zone = document.getElementById(id);
    zone?.addEventListener("dragover", (event) => {
        event.preventDefault();
        zone.classList.add("dragover");
    });
    zone?.addEventListener("dragleave", () => zone.classList.remove("dragover"));
    zone?.addEventListener("drop", (event) => {
        event.preventDefault();
        zone.classList.remove("dragover");
        const [file] = event.dataTransfer.files || [];
        if (file) handler(file);
    });
}
bindDropzone("document-dropzone", uploadDocument);
bindDropzone("qr-dropzone", uploadQr);
document.getElementById("document-refresh")?.addEventListener("click", resetDocumentImport);
document.getElementById("replace-document")?.addEventListener("click", resetDocumentImport);
document.getElementById("document-upload-another")?.addEventListener("click", resetDocumentImport);
document.getElementById("success-upload-another")?.addEventListener("click", resetDocumentImport);
document.getElementById("choose-another-document")?.addEventListener("click", resetDocumentImport);
document.getElementById("retry-document")?.addEventListener("click", () => {
    if (currentDocumentFile) uploadDocument(currentDocumentFile);
    else resetDocumentImport();
});
document.getElementById("copy-document-json")?.addEventListener("click", async () => {
    await navigator.clipboard.writeText(document.getElementById("document-json")?.textContent || "");
    showToast("JSON copied", "success");
});
document.getElementById("purchase-form")?.addEventListener("input", persistPurchaseHeader);
document.getElementById("save-purchase")?.addEventListener("click", () => {
    void savePurchaseFromJob("review");
});
document.getElementById("save-purchase-from-mapping")?.addEventListener("click", () => {
    void savePurchaseFromJob("mapping");
});
document.getElementById("continue-document-mapping")?.addEventListener("click", () => {
    const jobId = sanitizeJobId(currentDocumentJobId);
    if (!jobId) return;
    persistPurchaseHeader();
    window.location.href = `${basePath}/?view=mapping&jobId=${encodeURIComponent(jobId)}&sessionId=${encodeURIComponent(sessionId)}#session=${encodeURIComponent(sessionToken)}`;
});
document.getElementById("cancel-guardrail")?.addEventListener("click", closeGuardrailModal);
document.getElementById("confirm-guardrail")?.addEventListener("click", () => {
    const action = pendingGuardrailAction;
    closeGuardrailModal();
    if (action) action();
});

document.addEventListener("DOMContentLoaded", () => {
    if (documentMode) initDocumentImport();
    else if (mappingMode) initMapping();
    else initLaunch();
});




