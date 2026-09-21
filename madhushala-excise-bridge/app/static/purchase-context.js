(() => {
    const fieldConfig = [
        {id: "purchase-supplier-code", key: "supplierCode", optionKey: "suppliers", label: "Supplier", prompt: "Select Supplier", persist: false, required: true},
        {id: "purchase-store-code", key: "storeCode", optionKey: "storages", label: "Store", prompt: "Select Store", persist: true, required: true},
        {id: "purchase-scheme-code", key: "schemeCode", optionKey: "schemes", label: "Scheme", prompt: "Select Scheme", persist: true, required: false},
        {id: "purchase-acc-code", key: "purchaseAccCode", optionKey: "accounts", label: "Purchase A/c", prompt: "Select Purchase A/c", persist: true, required: true},
        {id: "purchase-user-code", key: "userCode", optionKey: "users", label: "User", prompt: "Select User", persist: true, required: true},
    ];

    const pageParams = new URLSearchParams(window.location.search);
    const purchaseReviewMode = pageParams.get("view") === "purchase";
    const sessionId = pageParams.get("sessionId") || "";
    const hashParams = new URLSearchParams(window.location.hash.replace(/^#/, "?"));
    const sessionToken = hashParams.get("session") || sessionStorage.getItem("exciseSession") || "";
    const basePath = window.location.pathname.startsWith("/excise-import/") ? "/excise-import" : "";

    let latestContext = null;
    let contextPromise = null;
    let contextHint = null;
    let initialized = false;
    let latestJobId = pageParams.get("jobId") || "";
    let latestSupplierName = "";
    let validatedFingerprint = "";
    let purchasePreviewTimer = null;

    function clean(value) {
        return String(value ?? "").trim();
    }

    function structuredMessage(detail, fallback = "Request failed") {
        if (!detail) return fallback;
        if (typeof detail === "string") return detail;
        if (typeof detail?.message === "string" && detail.message.trim()) return detail.message.trim();
        try {
            return JSON.stringify(detail, null, 2);
        } catch {
            return fallback;
        }
    }

    function ensureSchemeField() {
        if (document.getElementById("purchase-scheme-code")) return;
        const form = document.getElementById("purchase-form");
        if (!form) return;
        const label = document.createElement("label");
        label.append(document.createTextNode("Scheme"));
        const select = document.createElement("select");
        select.id = "purchase-scheme-code";
        select.name = "schemeCode";
        select.required = false;
        const placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = "None";
        select.appendChild(placeholder);
        label.appendChild(select);
        const accountField = document.getElementById("purchase-acc-code")?.closest("label");
        form.insertBefore(label, accountField || null);
    }

    function profileKey(context = latestContext) {
        if (!context?.shopCode) return "";
        return `madhushalaPurchaseProfile:${context.shopCode}:${context.companyCode || "default"}`;
    }

    function readProfile(context = latestContext) {
        const key = profileKey(context);
        if (!key) return {};
        try {
            return JSON.parse(localStorage.getItem(key) || "{}");
        } catch {
            return {};
        }
    }

    function saveProfile() {
        const key = profileKey();
        if (!key) return;
        const profile = {};
        fieldConfig.filter((field) => field.persist).forEach((field) => {
            profile[field.key] = clean(document.getElementById(field.id)?.value);
        });
        localStorage.setItem(key, JSON.stringify(profile));
    }

    function savedJobHeader(payload) {
        const jobId = clean(payload?.job?.id);
        if (!jobId) return {};
        try {
            return JSON.parse(sessionStorage.getItem(`purchaseHeader:${sessionId || "session"}:${jobId}`) || "{}");
        } catch {
            return {};
        }
    }

    function setLabelText(element, labelText) {
        const label = element?.closest("label");
        if (!label) return;
        const textNode = Array.from(label.childNodes).find((node) => node.nodeType === Node.TEXT_NODE && clean(node.textContent));
        if (textNode) textNode.textContent = labelText;
    }

    function optionExists(select, value) {
        return Array.from(select.options || []).some((option) => String(option.value) === String(value));
    }

    function upgradeField(config, options, preferredValue = "") {
        const current = document.getElementById(config.id);
        if (!current || !Array.isArray(options) || !options.length) return;

        let select = current;
        const previousValue = clean(current.value);
        if (current.tagName !== "SELECT") {
            select = document.createElement("select");
            select.id = current.id;
            select.name = current.getAttribute("name") || config.key;
            select.required = Boolean(config.required);
            select.className = current.className;
            select.setAttribute("aria-label", config.label);
            current.replaceWith(select);
        } else {
            select.required = Boolean(config.required);
        }

        const desired = previousValue || clean(preferredValue);
        select.innerHTML = "";
        const placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = config.required ? config.prompt : "None";
        select.appendChild(placeholder);

        options.forEach((item) => {
            const code = clean(item?.code);
            if (!code) return;
            const name = clean(item?.name) || code;
            const option = document.createElement("option");
            option.value = code;
            option.textContent = name === code ? code : `${name} (${code})`;
            select.appendChild(option);
        });

        if (desired && !optionExists(select, desired)) {
            const option = document.createElement("option");
            option.value = desired;
            option.textContent = `Current (${desired})`;
            select.appendChild(option);
        }
        if (desired) select.value = desired;
        setLabelText(select, config.label);
    }

    function applyContext(context) {
        latestContext = context;
        const profile = readProfile(context);
        const options = context?.options || {};
        const defaults = context?.defaults || {};

        fieldConfig.forEach((config) => {
            const currentValue = clean(document.getElementById(config.id)?.value);
            const savedValue = config.persist ? clean(profile[config.key]) : "";
            const defaultValue = clean(defaults[config.key]);
            upgradeField(
                config,
                options[config.optionKey] || [],
                currentValue || savedValue || defaultValue,
            );
        });
    }

    async function fetchPurchaseContext(supplierName = "") {
        if (!sessionToken) return null;
        const hint = clean(supplierName);
        if (contextPromise && contextHint === hint) return contextPromise;
        contextHint = hint;
        const query = hint ? `?supplierName=${encodeURIComponent(hint)}` : "";
        contextPromise = (async () => {
            const response = await fetch(`${basePath}/api/v1/document-import/purchase/context${query}`, {
                headers: {Authorization: `Bearer ${sessionToken}`},
            });
            const text = await response.text();
            let payload = {};
            if (text) {
                try {
                    payload = JSON.parse(text);
                } catch {
                    payload = {detail: text};
                }
            }
            if (!response.ok) throw new Error(structuredMessage(payload.detail || payload.error, `HTTP ${response.status}`));
            applyContext(payload);
            return payload;
        })();
        try {
            return await contextPromise;
        } finally {
            contextPromise = null;
        }
    }

    function setField(id, value, force = false) {
        const field = document.getElementById(id);
        const next = clean(value);
        if (!field || !next) return;
        if (force || !clean(field.value)) field.value = next;
    }

    function forceYearCode(value = "") {
        const field = document.getElementById("purchase-year-code");
        if (field) field.value = clean(value);
    }

    function applyDocumentDefaults(payload) {
        const documentData = payload?.extractedDocument || {};
        const saved = savedJobHeader(payload);
        latestJobId = clean(payload?.job?.id) || latestJobId;
        latestSupplierName = clean(documentData?.supplierName) || latestSupplierName;

        if (!saved.docNo) setField("purchase-doc-no", documentData.invoiceNumber);
        if (!saved.docDate) setField("purchase-doc-date", documentData.invoiceDate, true);
        if (!saved.tpPassNo) setField("purchase-tp-pass-no", documentData.transportPassNo, true);
        // The observed manual Madhushala Purchase request sends yearCode="".
        // Preserve an explicitly saved value, otherwise remove app.js's
        // automatically invented financial-year default.
        forceYearCode(saved.yearCode || "");
    }

    function requiredPurchaseFields() {
        return fieldConfig
            .filter((field) => field.required)
            .map((field) => ({id: field.id, label: field.label}));
    }

    function currentMissingFields() {
        return requiredPurchaseFields().filter((field) => !clean(document.getElementById(field.id)?.value));
    }

    function showFriendlyMissing() {
        const missing = currentMissingFields();
        if (!missing.length) return false;
        const message = `Purchase requires: ${missing.map((field) => field.label).join(", ")}`;
        if (typeof window.showToast === "function") window.showToast(message, "error");
        else window.alert(message);
        document.getElementById(missing[0].id)?.focus();
        return true;
    }

    function clearDocumentSpecificFields() {
        [
            "purchase-supplier-code",
            "purchase-year-code",
            "purchase-doc-date",
            "purchase-doc-no",
            "purchase-tp-pass-no",
        ].forEach((id) => {
            const field = document.getElementById(id);
            if (field) field.value = "";
        });
    }

    function installHeaderHooks() {
        const originalCollect = window.collectPurchaseHeader;
        if (typeof originalCollect === "function") {
            window.collectPurchaseHeader = function collectRequiredPurchaseHeader() {
                return {
                    ...originalCollect.apply(this, arguments),
                    yearCode: clean(document.getElementById("purchase-year-code")?.value),
                    schemeCode: clean(document.getElementById("purchase-scheme-code")?.value),
                };
            };
        }

        const originalApply = window.applyPurchaseHeader;
        if (typeof originalApply === "function") {
            window.applyPurchaseHeader = function applyRequiredPurchaseHeader(header = {}) {
                const result = originalApply.apply(this, arguments);
                forceYearCode(header.yearCode || "");
                setField("purchase-scheme-code", header.schemeCode || "", true);
                return result;
            };
        }
    }

    function installMappingSearchVisibility() {
        if (pageParams.get("view") !== "mapping") return;
        if (!document.getElementById("mapping-search-visibility-style")) {
            const style = document.createElement("style");
            style.id = "mapping-search-visibility-style";
            style.textContent = `
                #mapping-view .mapping-layout {
                    min-height: 0 !important;
                    align-items: stretch !important;
                }
                #mapping-view .unmapped-list,
                #mapping-view .mapper {
                    height: 100% !important;
                    min-height: 0 !important;
                }
                #mapping-view .list-body,
                #mapping-view .candidate-list {
                    height: auto !important;
                    min-height: 0 !important;
                    max-height: none !important;
                }
                #mapping-view .list-body {
                    flex: 1 1 auto !important;
                }
                #mapping-view .results-panel {
                    min-height: 0 !important;
                    overflow: hidden !important;
                }
                #mapping-view.mapping-search-active .mapping-purchase-details #mapping-purchase-form-host {
                    display: none !important;
                }
                #mapping-view.mapping-search-active .mapping-purchase-details-header {
                    border-bottom: 0 !important;
                }
                #mapping-view.mapping-search-active .best-match-card {
                    display: none !important;
                }
                #mapping-view.mapping-search-active .mapping-layout {
                    min-height: 250px !important;
                }
                #mapping-view.mapping-search-active .candidate-list {
                    min-height: 150px !important;
                }
                #mapping-view.mapping-search-active .candidate {
                    height: 48px !important;
                    min-height: 48px !important;
                    max-height: 48px !important;
                    padding: 6px 9px !important;
                    border-color: #dfe3e7 !important;
                }
                #mapping-view.mapping-search-active .candidate strong {
                    font-size: 12px !important;
                    line-height: 1.2 !important;
                }
                #mapping-view.mapping-search-active .candidate small {
                    font-size: 10px !important;
                    line-height: 1.15 !important;
                }
                #mapping-view.mapping-search-active .results-title {
                    padding: 7px 10px !important;
                    font-size: 11px !important;
                    color: var(--heading) !important;
                    background: #fff9e5 !important;
                }
            `;
            document.head.appendChild(style);
        }

        const mappingView = document.getElementById("mapping-view");
        const search = document.getElementById("madhushala-search");
        if (!mappingView || !search || search.dataset.visibilityBound === "true") return;
        search.dataset.visibilityBound = "true";

        const sync = () => {
            const active = document.activeElement === search || clean(search.value).length > 0;
            mappingView.classList.toggle("mapping-search-active", active);
        };
        search.addEventListener("focus", sync);
        search.addEventListener("input", sync);
        search.addEventListener("blur", () => window.setTimeout(sync, 120));
        sync();
    }

    function ensureValidationStyle() {
        if (document.getElementById("purchase-validation-style")) return;
        const style = document.createElement("style");
        style.id = "purchase-validation-style";
        style.textContent = `
            .purchase-validate-button { margin-right: 8px; }
            .purchase-validation-status {
                display: block;
                width: 100%;
                margin-top: 8px;
                font-size: 12px;
                line-height: 1.4;
                white-space: pre-wrap;
            }
            .purchase-validation-status.ok { color: #177245; }
            .purchase-validation-status.error { color: #a12622; }
            .purchase-validation-status.pending { color: #765c00; }
        `;
        document.head.appendChild(style);
    }

    function purchaseJobId() {
        return clean(latestJobId || pageParams.get("jobId"));
    }

    function purchaseHeaderForJob(jobId) {
        if (typeof window.collectPurchaseHeader === "function") {
            return window.collectPurchaseHeader();
        }
        if (typeof window.loadPurchaseHeader === "function") {
            return window.loadPurchaseHeader(jobId);
        }
        return {};
    }

    function headerFingerprint(jobId, header) {
        const normalized = {
            jobId,
            yearCode: clean(header?.yearCode),
            trnDate: clean(header?.trnDate),
            docDate: clean(header?.docDate),
            docNo: clean(header?.docNo),
            tpPassNo: clean(header?.tpPassNo),
            supplierCode: clean(header?.supplierCode),
            storeCode: clean(header?.storeCode),
            schemeCode: clean(header?.schemeCode),
            purchaseAccCode: clean(header?.purchaseAccCode),
            userCode: clean(header?.userCode),
            taxMode: clean(header?.taxMode),
            narration: clean(header?.narration),
        };
        return JSON.stringify(normalized);
    }

    function validationElements(source) {
        const suffix = source === "mapping" ? "mapping" : "review";
        return {
            save: document.getElementById(source === "mapping" ? "save-purchase-from-mapping" : "save-purchase"),
            validate: document.getElementById(`validate-purchase-${suffix}`),
            status: document.getElementById(`purchase-validation-status-${suffix}`),
        };
    }

    function setValidationUi(source, state, message = "") {
        const elements = validationElements(source);
        if (elements.status) {
            elements.status.className = `purchase-validation-status ${state || ""}`.trim();
            elements.status.textContent = message;
        }
        if (elements.validate) {
            elements.validate.disabled = state === "pending";
            elements.validate.textContent = state === "ok" ? "Validated ✓" : (state === "pending" ? "Validating…" : "Validate Purchase");
        }
        if (elements.save) {
            // On the final Purchase Bill there is no visible Validate button.
            // Keep Save clickable (except while Calculate is running); the existing
            // save wrapper performs a fresh Calculate/revalidation immediately
            // before the real Purchase Save.
            elements.save.disabled = purchaseReviewMode && source === "review"
                ? state === "pending"
                : state !== "ok";
        }
    }

    function invalidatePurchaseValidation(message = "Validate against Madhushala before saving.") {
        validatedFingerprint = "";
        setValidationUi("review", "", message);
        setValidationUi("mapping", "", message);
    }

    function ensureValidationControls() {
        ensureValidationStyle();
        [
            ["review", "save-purchase"],
        ].forEach(([source, saveId]) => {
            const save = document.getElementById(saveId);
            if (!save) return;
            const suffix = "review";
            if (purchaseReviewMode && source === "review") {
                // Final Purchase Bill validates automatically in the background
                // and again at Save time. Do not render a separate Validate button.
                const existingValidate = document.getElementById(`validate-purchase-${suffix}`);
                existingValidate?.remove();
                const existingStatus = document.getElementById(`purchase-validation-status-${suffix}`);
                existingStatus?.remove();
                save.disabled = false;
                return;
            }

            let validate = document.getElementById(`validate-purchase-${suffix}`);
            if (!validate) {
                validate = document.createElement("button");
                validate.type = "button";
                validate.id = `validate-purchase-${suffix}`;
                validate.className = "purchase-validate-button";
                validate.textContent = "Validate Purchase";
                validate.addEventListener("click", () => {
                    void validatePurchase(source, {showSuccessToast: true});
                });
                save.insertAdjacentElement("beforebegin", validate);
            }
            let status = document.getElementById(`purchase-validation-status-${suffix}`);
            if (!status) {
                status = document.createElement("span");
                status.id = `purchase-validation-status-${suffix}`;
                status.className = "purchase-validation-status";
                status.textContent = "Validate against Madhushala before saving.";
                save.insertAdjacentElement("afterend", status);
            }
            if (!validatedFingerprint) save.disabled = true;
        });
    }

    function renderValidationPayload(payload) {
        const purchase = payload?.purchasePayload || {};
        const count = Array.isArray(purchase.items) ? purchase.items.length : 0;
        const summary = `Madhushala validated ${count} item${count === 1 ? "" : "s"} | Gross ${purchase.grossAmount ?? 0} | Tax ${purchase.taxAmount ?? 0} | Net ${purchase.netAmount ?? 0}`;
        const actionSummary = document.getElementById("document-action-summary");
        if (actionSummary) actionSummary.textContent = summary;
        const json = document.getElementById("document-json");
        if (json) {
            // On the final Purchase screen render the exact business payload that
            // will be sent to Madhushala after save-time revalidation. Other
            // legacy/review views retain the complete debug response.
            json.textContent = JSON.stringify(
                pageParams.get("view") === "purchase" ? purchase : payload,
                null,
                2,
            );
        }
        window.dispatchEvent(new CustomEvent("purchase-preview-ready", {detail: payload}));
        return summary;
    }

    async function savePendingMappingsForValidation(source) {
        if (source !== "mapping") return true;
        const submit = document.getElementById("submit-mappings");
        if (!submit || submit.disabled || typeof window.saveMappings !== "function") return true;
        setValidationUi(source, "pending", "Saving pending item mappings before Madhushala validation…");
        const saved = await window.saveMappings();
        if (saved) return true;
        setValidationUi(source, "error", "Pending item mappings could not be saved. Purchase validation was not run.");
        return false;
    }

    async function validatePurchase(source = "review", {showSuccessToast = false} = {}) {
        ensureValidationControls();
        const jobId = purchaseJobId();
        if (!jobId) {
            const message = "No document job is ready for purchase validation.";
            setValidationUi(source, "error", message);
            if (typeof window.showToast === "function") window.showToast(message, "error");
            return false;
        }

        if (!await savePendingMappingsForValidation(source)) return false;

        try {
            await fetchPurchaseContext(source === "mapping" ? "" : latestSupplierName);
        } catch {
            // The preview endpoint will return the precise master/contract error.
        }
        if (showFriendlyMissing()) {
            setValidationUi(source, "error", "Complete the required Purchase master fields first.");
            return false;
        }

        saveProfile();
        if (typeof window.persistPurchaseHeader === "function") window.persistPurchaseHeader();
        const header = purchaseHeaderForJob(jobId);
        setValidationUi(source, "pending", "Calling Madhushala Calculate. No purchase will be saved by this validation step.");

        try {
            const response = await fetch(`${basePath}/api/v1/document-import/jobs/${encodeURIComponent(jobId)}/purchase/calculate-preview`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "Authorization": `Bearer ${sessionToken}`,
                },
                body: JSON.stringify({header}),
            });
            const text = await response.text();
            let payload = {};
            if (text) {
                try {
                    payload = JSON.parse(text);
                } catch {
                    payload = {detail: text};
                }
            }
            if (!response.ok) {
                const detail = payload?.detail || payload?.error || payload;
                const message = structuredMessage(detail, `HTTP ${response.status}`);
                const json = document.getElementById("document-json");
                if (json) json.textContent = JSON.stringify(payload, null, 2);
                setValidationUi(source, "error", message);
                if (typeof window.showToast === "function") window.showToast(message, "error");
                return false;
            }

            validatedFingerprint = headerFingerprint(jobId, header);
            const summary = renderValidationPayload(payload);
            setValidationUi(source, "ok", summary);
            if (showSuccessToast && typeof window.showToast === "function") window.showToast(summary, "success");
            return true;
        } catch (error) {
            const message = error?.message || "Purchase validation failed";
            setValidationUi(source, "error", message);
            if (typeof window.showToast === "function") window.showToast(message, "error");
            return false;
        }
    }

    function schedulePurchasePreviewRefresh() {
        if (!purchaseReviewMode) return;
        if (purchasePreviewTimer) window.clearTimeout(purchasePreviewTimer);
        purchasePreviewTimer = window.setTimeout(() => {
            void validatePurchase("review", {showSuccessToast: false});
        }, 450);
    }

    function installHooks() {
        installHeaderHooks();

        const originalRender = window.renderDocumentReview;
        if (typeof originalRender === "function") {
            window.renderDocumentReview = function purchaseAwareRender(payload) {
                const result = originalRender.apply(this, arguments);
                latestJobId = clean(payload?.job?.id) || latestJobId;
                latestSupplierName = clean(payload?.extractedDocument?.supplierName) || latestSupplierName;
                applyDocumentDefaults(payload);
                invalidatePurchaseValidation();
                ensureValidationControls();
                void fetchPurchaseContext(latestSupplierName).catch(() => {});
                return result;
            };
        }

        const originalSave = window.savePurchaseFromJob;
        if (typeof originalSave === "function") {
            window.savePurchaseFromJob = async function purchaseAwareSave(source = "review") {
                try {
                    await fetchPurchaseContext(source === "mapping" ? "" : latestSupplierName);
                } catch {
                    // The bridge will provide a precise error if master lookup is unavailable.
                }
                if (showFriendlyMissing()) return;
                saveProfile();
                if (typeof window.persistPurchaseHeader === "function") window.persistPurchaseHeader();

                // Re-run live Calculate immediately before every save. Even if a
                // previous validation succeeded, this prevents stale mappings or
                // edited header values from bypassing Madhushala validation.
                const validated = await validatePurchase(source, {showSuccessToast: false});
                if (!validated) return;
                return originalSave.call(this, source);
            };
        }

        const originalReset = window.resetDocumentImport;
        if (typeof originalReset === "function") {
            window.resetDocumentImport = function purchaseAwareReset() {
                const result = originalReset.apply(this, arguments);
                clearDocumentSpecificFields();
                latestJobId = "";
                latestSupplierName = "";
                invalidatePurchaseValidation();
                void fetchPurchaseContext("").catch(() => {});
                return result;
            };
        }

        const continueButton = document.getElementById("continue-document-mapping");
        continueButton?.addEventListener("click", (event) => {
            saveProfile();
            if (showFriendlyMissing()) {
                event.preventDefault();
                event.stopImmediatePropagation();
            } else {
                invalidatePurchaseValidation();
            }
        }, true);
    }

    function restoreMappingHeader() {
        if (pageParams.get("view") !== "mapping") return;
        const jobId = clean(pageParams.get("jobId"));
        if (!jobId) return;
        latestJobId = jobId;
        try {
            if (typeof window.loadPurchaseHeader === "function" && typeof window.applyPurchaseHeader === "function") {
                window.applyPurchaseHeader(window.loadPurchaseHeader(jobId));
            }
        } catch {
            // Keep the mapping page usable even if storage is unavailable.
        }
    }

    function initializePurchaseContext() {
        if (initialized) return;
        const isDocumentImport = window.location.pathname.endsWith("/document-import");
        const isMappingView = pageParams.get("view") === "mapping";
        if (!isDocumentImport || isMappingView) return;
        initialized = true;

        ensureSchemeField();
        installHooks();
        restoreMappingHeader();
        ensureValidationControls();
        invalidatePurchaseValidation();
        if (isMappingView) installMappingSearchVisibility();

        const form = document.getElementById("purchase-form");
        form?.addEventListener("input", () => {
            invalidatePurchaseValidation();
            schedulePurchasePreviewRefresh();
        });
        form?.addEventListener("change", (event) => {
            if (fieldConfig.some((field) => field.persist && field.id === event.target?.id)) saveProfile();
            if (isMappingView && typeof window.persistPurchaseHeader === "function") window.persistPurchaseHeader();
            invalidatePurchaseValidation();
            schedulePurchasePreviewRefresh();
        });

        void fetchPurchaseContext("").then(() => {
            restoreMappingHeader();
            ensureValidationControls();
            if (isMappingView) installMappingSearchVisibility();
        }).catch(() => {
            // Keep the form usable; the bridge will report required master lookup failures.
        });
    }

    window.__purchaseContext = {
        initialize: initializePurchaseContext,
        refresh: fetchPurchaseContext,
        validate: validatePurchase,
        invalidateValidation: invalidatePurchaseValidation,
        getContext: () => latestContext,
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initializePurchaseContext, {once: true});
    } else {
        initializePurchaseContext();
    }
})();
