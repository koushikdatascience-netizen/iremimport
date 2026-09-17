(() => {
    const fieldConfig = [
        {id: "purchase-supplier-code", key: "supplierCode", optionKey: "suppliers", label: "Supplier", prompt: "Select Supplier", persist: false, required: true},
        {id: "purchase-store-code", key: "storeCode", optionKey: "storages", label: "Store", prompt: "Select Store", persist: true, required: true},
        {id: "purchase-scheme-code", key: "schemeCode", optionKey: "schemes", label: "Scheme", prompt: "Select Scheme", persist: true, required: false},
        {id: "purchase-acc-code", key: "purchaseAccCode", optionKey: "accounts", label: "Purchase A/c", prompt: "Select Purchase A/c", persist: true, required: true},
        {id: "purchase-user-code", key: "userCode", optionKey: "users", label: "User", prompt: "Select User", persist: true, required: true},
    ];

    const pageParams = new URLSearchParams(window.location.search);
    const sessionId = pageParams.get("sessionId") || "";
    const hashParams = new URLSearchParams(window.location.hash.replace(/^#/, "?"));
    const sessionToken = hashParams.get("session") || sessionStorage.getItem("exciseSession") || "";
    const basePath = window.location.pathname.startsWith("/excise-import/") ? "/excise-import" : "";

    let latestContext = null;
    let contextPromise = null;
    let contextHint = null;
    let initialized = false;

    function clean(value) {
        return String(value ?? "").trim();
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
            if (!response.ok) throw new Error(payload.detail || payload.error || `HTTP ${response.status}`);
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

    function applyDocumentDefaults(payload) {
        const documentData = payload?.extractedDocument || {};
        const saved = savedJobHeader(payload);

        if (!saved.docNo) setField("purchase-doc-no", documentData.invoiceNumber);
        if (!saved.docDate) setField("purchase-doc-date", documentData.invoiceDate, true);
        if (!saved.tpPassNo) setField("purchase-tp-pass-no", documentData.transportPassNo, true);
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
                    schemeCode: clean(document.getElementById("purchase-scheme-code")?.value),
                };
            };
        }

        const originalApply = window.applyPurchaseHeader;
        if (typeof originalApply === "function") {
            window.applyPurchaseHeader = function applyRequiredPurchaseHeader(header = {}) {
                const result = originalApply.apply(this, arguments);
                setField("purchase-scheme-code", header.schemeCode || "", true);
                return result;
            };
        }
    }

    function installHooks() {
        installHeaderHooks();

        const originalRender = window.renderDocumentReview;
        if (typeof originalRender === "function") {
            window.renderDocumentReview = function purchaseAwareRender(payload) {
                const result = originalRender.apply(this, arguments);
                applyDocumentDefaults(payload);
                void fetchPurchaseContext(payload?.extractedDocument?.supplierName || "").catch(() => {});
                return result;
            };
        }

        const originalSave = window.savePurchaseFromJob;
        if (typeof originalSave === "function") {
            window.savePurchaseFromJob = async function purchaseAwareSave(source = "review") {
                try {
                    await fetchPurchaseContext(
                        source === "mapping" ? "" : window.currentDocumentResult?.extractedDocument?.supplierName || "",
                    );
                } catch {
                    // The bridge will provide a precise error if master lookup is unavailable.
                }
                if (showFriendlyMissing()) return;
                saveProfile();
                if (typeof window.persistPurchaseHeader === "function") window.persistPurchaseHeader();
                return originalSave.call(this, source);
            };
        }

        const originalReset = window.resetDocumentImport;
        if (typeof originalReset === "function") {
            window.resetDocumentImport = function purchaseAwareReset() {
                const result = originalReset.apply(this, arguments);
                clearDocumentSpecificFields();
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
            }
        }, true);
    }

    function restoreMappingHeader() {
        if (pageParams.get("view") !== "mapping") return;
        const jobId = clean(pageParams.get("jobId"));
        if (!jobId) return;
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
        if (!isDocumentImport && !isMappingView) return;
        initialized = true;

        ensureSchemeField();
        installHooks();
        restoreMappingHeader();
        document.getElementById("purchase-form")?.addEventListener("change", (event) => {
            if (fieldConfig.some((field) => field.persist && field.id === event.target?.id)) saveProfile();
            if (isMappingView && typeof window.persistPurchaseHeader === "function") window.persistPurchaseHeader();
        });
        void fetchPurchaseContext("").then(() => {
            restoreMappingHeader();
        }).catch(() => {
            // Keep the form usable; the bridge will report required master lookup failures.
        });
    }

    window.__purchaseContext = {
        initialize: initializePurchaseContext,
        refresh: fetchPurchaseContext,
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initializePurchaseContext, {once: true});
    } else {
        initializePurchaseContext();
    }
})();
