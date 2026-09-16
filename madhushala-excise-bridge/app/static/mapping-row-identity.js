(() => {
    function isDocumentWorkspace() {
        return Boolean(workspace?.documentMapping || currentDocumentJobId);
    }

    function mappingRowKey(item) {
        const jobItemId = String(item?.jobItemId ?? "").trim();
        if (isDocumentWorkspace() && jobItemId) return `job:${jobItemId}`;
        return `excise:${String(item?.exciseItemCode ?? "").trim()}`;
    }

    function rowForKey(key) {
        return (workspace?.unmappedItems || []).find((item) => mappingRowKey(item) === String(key));
    }

    mappedItemForRow = function mappedItemForUniqueRow(item) {
        const code = selectedMappings.get(mappingRowKey(item)) || item.selectedItemCode || "";
        if (!code) return null;
        return findMadhushalaItem(code) || item.selectedItem || {itemCode: code, itemName: "Mapped item"};
    };

    currentExciseItem = function currentUniqueDocumentRow() {
        return rowForKey(selectedExciseCode);
    };

    updateSummary = function updateUniqueRowSummary() {
        const left = (workspace.unmappedItems || []).filter(
            (item) => !selectedMappings.get(mappingRowKey(item)) && !item.selectedItemCode,
        ).length;
        setText(
            document.getElementById("mapping-summary"),
            `${workspace.documentMapping ? "Document rows" : "Selected"}: ${selectedMappings.size} | Left: ${left}`,
        );
        const submit = document.getElementById("submit-mappings");
        if (submit) submit.disabled = selectedMappings.size === 0;
    };

    renderWorkspace = function renderUniqueDocumentRows() {
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

        const firstKey = mappingRowKey(workspace.unmappedItems[0]);
        if (!selectedExciseCode) selectedExciseCode = firstKey;
        if (selectedExciseCode && !currentExciseItem()) selectedExciseCode = firstKey;

        const documentModeRows = isDocumentWorkspace();
        list.className = documentModeRows ? "list-body document-map-list" : "list-body";
        list.innerHTML = workspace.unmappedItems.map((item) => {
            const code = String(item.exciseItemCode ?? "");
            const rowKey = mappingRowKey(item);
            const selected = rowKey === String(selectedExciseCode);
            const mapped = selectedMappings.get(rowKey) || item.selectedItemCode;
            if (documentModeRows) {
                return `
                    <button class="unmapped-item document-map-row ${selected ? "selected" : ""}" data-row-key="${escapeHtml(rowKey)}" type="button">
                        <span class="doc-extracted"><small>${escapeHtml(code || "New")}</small><strong>${escapeHtml(item.itemName)}</strong>${selectedExciseDetails(item)}</span>
                        <span class="doc-arrow" aria-hidden="true">→</span>
                        ${mappedItemMarkup(item)}
                    </button>`;
            }
            return `
                <button class="unmapped-item ${selected ? "selected" : ""}" data-row-key="${escapeHtml(rowKey)}" type="button">
                    <span class="item-code">${escapeHtml(code)}</span>
                    <span class="item-name">${escapeHtml(item.itemName)}</span>
                    <span class="${mapped ? "map-badge done" : "map-badge"}">${mapped ? "Selected" : "Pending"}</span>
                    ${selectedExciseDetails(item)}
                </button>`;
        }).join("");

        list.querySelectorAll("[data-row-key]").forEach((button) => {
            button.addEventListener("click", () => {
                selectedExciseCode = button.dataset.rowKey;
                const search = document.getElementById("madhushala-search");
                if (search) search.value = "";
                renderWorkspace();
            });
        });

        renderSelectedExcise(currentExciseItem());
        updateSummary();
    };

    selectMadhushalaItem = function selectForUniqueDocumentRow(itemCode, score = null) {
        const exciseItem = currentExciseItem();
        if (!exciseItem) {
            showToast("Select an extracted row first", "error");
            return;
        }
        const madhushalaItem = findMadhushalaItem(itemCode);
        const issues = guardrailIssues(exciseItem, madhushalaItem, score);
        const rowKey = mappingRowKey(exciseItem);
        const apply = () => {
            selectedMappings.set(rowKey, String(itemCode));
            showToast("Selected", "success");
            renderWorkspace();
        };
        if (issues.length) showGuardrailModal(issues, apply);
        else apply();
    };

    loadWorkspace = async function loadUniqueDocumentWorkspace(jobId = currentDocumentJobId, options = {}) {
        const normalizedJobId = sanitizeJobId(isEventLike(jobId) ? "" : jobId || currentDocumentJobId);
        const preserveState = options.preserveState !== false;
        const previousSelected = preserveState ? selectedExciseCode : null;
        const search = document.getElementById("madhushala-search");
        const previousSearch = preserveState ? search?.value || "" : "";
        try {
            const query = normalizedJobId ? `?jobId=${encodeURIComponent(normalizedJobId)}` : "?latestOnly=true";
            workspace = await api(`/mapping/workspace${query}`);
            currentDocumentJobId = normalizedJobId || sanitizeJobId(workspace?.jobId) || currentDocumentJobId;
            const keys = new Set((workspace.unmappedItems || []).map(mappingRowKey));
            selectedExciseCode = previousSelected && keys.has(String(previousSelected)) ? previousSelected : null;
            if (search) search.value = previousSearch;
            renderWorkspace();
            if (previousSearch) runSearch();
        } catch (error) {
            if (!options.quiet) showToast(error.message || "Could not load mapping", "error");
        }
    };

    saveMappings = async function saveUniqueDocumentMappings() {
        const mappings = [];
        const documentJobId = sanitizeJobId(currentDocumentJobId || workspace?.jobId || "");
        const documentMapping = Boolean(documentJobId && isDocumentWorkspace());

        for (const [rowKey, itemCode] of selectedMappings.entries()) {
            const row = rowForKey(rowKey);
            if (!row) continue;
            const rawExciseCode = String(row.exciseItemCode ?? "").trim();
            const parsedExciseCode = Number(rawExciseCode);
            const hasValidExciseCode = Boolean(
                rawExciseCode && Number.isInteger(parsedExciseCode) && parsedExciseCode > 0,
            );

            if (!documentMapping && !hasValidExciseCode) {
                showToast(`${row.itemName || "This row"} does not have a valid Excise item code.`, "error");
                return false;
            }

            mappings.push({
                jobItemId: documentMapping ? row.jobItemId || null : null,
                exciseItemCode: hasValidExciseCode ? parsedExciseCode : null,
                itemCode,
            });
        }
        if (!mappings.length) return false;

        const submit = document.getElementById("submit-mappings");
        if (submit) submit.disabled = true;
        try {
            const endpoint = documentMapping
                ? `/api/v1/document-import/jobs/${encodeURIComponent(documentJobId)}/mapping/save`
                : "/mapping/submit";
            const body = documentMapping ? {mappings} : {mappings, jobId: null};
            const result = await api(endpoint, {
                method: "POST",
                body: JSON.stringify(body),
            });
            selectedMappings.clear();
            showToast(`Saved ${result.mappedCount} mapping${result.mappedCount === 1 ? "" : "s"}`, "success");
            await loadWorkspace(documentJobId || currentDocumentJobId, {preserveState: false});
            if (documentJobId) {
                setHidden(document.getElementById("save-purchase-from-mapping"), false);
            }
            return true;
        } catch (error) {
            showToast(error.message || "Could not save mapping", "error");
            return false;
        } finally {
            if (submit) submit.disabled = selectedMappings.size === 0;
        }
    };

    // If the user chooses items and clicks Save Purchase directly, persist the
    // pending row selections first. Purchase creation only needs mapped_item_code;
    // an Excise item code is optional for document/QR rows.
    const originalSavePurchaseFromJob = savePurchaseFromJob;
    savePurchaseFromJob = async function savePurchaseAfterPendingMappings(source = "review") {
        if (source === "mapping" && selectedMappings.size > 0) {
            const saved = await saveMappings();
            if (!saved) return;
        }
        return originalSavePurchaseFromJob(source);
    };

    // The legacy inline page attached the old saveMappings function directly to this button.
    // Replace the node once so the click handler uses the row-safe implementation above.
    const oldSubmit = document.getElementById("submit-mappings");
    if (oldSubmit) {
        const submit = oldSubmit.cloneNode(true);
        oldSubmit.replaceWith(submit);
        submit.addEventListener("click", () => void saveMappings());
    }

    window.__mappingRowIdentity = {mappingRowKey};
})();
