(() => {
    const purchaseReviewMode = pageParams.get("view") === "purchase";
    const autoConfirmingJobs = new Set();
    let suppressSuccessfulReview = false;

    const originalSetDocumentImportState = setDocumentImportState;
    setDocumentImportState = function setDocumentImportStateWithoutSuccessfulPreview(state, message = "") {
        if (state === "review" && suppressSuccessfulReview) {
            return originalSetDocumentImportState("checking", message || "Preparing item mapping");
        }
        return originalSetDocumentImportState(state, message);
    };

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

    function mappingRowsLeft() {
        return (workspace?.unmappedItems || []).filter(
            (item) => !selectedMappings.get(mappingRowKey(item)) && !item.selectedItemCode,
        ).length;
    }

    function purchaseSourceHintKey(jobId) {
        return `purchaseSourceHint:${sessionId || "session"}:${jobId || "latest"}`;
    }

    function storePurchaseSourceHint(payload, jobId) {
        try {
            sessionStorage.setItem(
                purchaseSourceHintKey(jobId),
                JSON.stringify({
                    supplierName: String(payload?.extractedDocument?.supplierName || "").trim(),
                    invoiceNumber: String(payload?.extractedDocument?.invoiceNumber || "").trim(),
                    invoiceDate: String(payload?.extractedDocument?.invoiceDate || "").trim(),
                    sourceType: String(payload?.job?.source_type || payload?.job?.sourceType || "").trim(),
                }),
            );
        } catch {
            // Session storage is a convenience only; backend validation remains authoritative.
        }
    }

    function loadPurchaseSourceHint(jobId) {
        try {
            return JSON.parse(sessionStorage.getItem(purchaseSourceHintKey(jobId)) || "{}");
        } catch {
            return {};
        }
    }

    function reviewConfirmItems(payload) {
        const items = typeof reviewItemsFromPayload === "function" ? reviewItemsFromPayload(payload) : [];
        const invalid = items.filter((item) => (
            typeof validateReviewItem === "function" ? validateReviewItem(item).length > 0 : false
        ));
        return {items, invalid};
    }

    function primeSuccessfulImport(payload) {
        const jobId = sanitizeJobId(payload?.job?.id || currentDocumentJobId || "");
        currentDocumentResult = payload;
        currentDocumentJobId = jobId || currentDocumentJobId;
        if (jobId) storePurchaseSourceHint(payload, jobId);

        try {
            applyPurchaseHeader(loadPurchaseHeader(jobId));
        } catch {
            // The Purchase screen will still resolve authoritative defaults later.
        }
        setInputValue("purchase-doc-no", payload?.extractedDocument?.invoiceNumber || "");
        setInputValue("purchase-doc-date", payload?.extractedDocument?.invoiceDate || "");
        if (currentUploadKind === "qr") setInputValue("purchase-narration", "QR HTML import");

        suppressSuccessfulReview = true;
        setDocumentImportState("checking");
        setText(document.getElementById("document-progress-title"), "Preparing item mapping");
        setText(
            document.getElementById("document-progress-detail"),
            "Extraction is complete. Opening Madhushala mapping automatically.",
        );
    }

    async function autoConfirmExtractedReview(payload) {
        const jobId = sanitizeJobId(payload?.job?.id || currentDocumentJobId || "");
        if (!jobId || autoConfirmingJobs.has(jobId)) return;

        const {items, invalid} = reviewConfirmItems(payload);
        if (!items.length || invalid.length) {
            suppressSuccessfulReview = false;
            originalRenderDocumentReview(payload);
            keepDocumentMappingReviewAvailable(payload, {qr: currentUploadKind === "qr"});
            setDocumentImportState("review");
            if (invalid.length) {
                showToast(
                    `${invalid.length} extracted row${invalid.length === 1 ? "" : "s"} need correction before mapping.`,
                    "error",
                );
            }
            return;
        }

        autoConfirmingJobs.add(jobId);
        currentDocumentJobId = jobId;
        storePurchaseSourceHint(payload, jobId);
        persistPurchaseHeader();
        setDocumentImportState("checking");
        setText(document.getElementById("document-progress-title"), "Preparing item mapping");
        setText(
            document.getElementById("document-progress-detail"),
            "Extraction is complete. Confirming the extracted rows and opening Madhushala mapping automatically.",
        );

        try {
            const confirmed = await api(
                "/api/v1/document-import/jobs/" + encodeURIComponent(jobId) + "/review/confirm",
                {
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
                },
            );
            currentDocumentResult = Object.assign({}, currentDocumentResult || {}, confirmed || {});
            window.location.href = basePath
                + "/?view=mapping&jobId=" + encodeURIComponent(jobId)
                + "&sessionId=" + encodeURIComponent(sessionId)
                + "#session=" + encodeURIComponent(sessionToken);
        } catch (error) {
            autoConfirmingJobs.delete(jobId);
            // Keep the old review UI only as an error/correction fallback.
            suppressSuccessfulReview = false;
            originalRenderDocumentReview(payload);
            keepDocumentMappingReviewAvailable(payload, {qr: currentUploadKind === "qr"});
            setDocumentImportState("review");
            showToast(error.message || "Could not prepare mapping automatically", "error");
        }
    }

    function ensureMappingPurchaseStyles() {
        if (document.getElementById("mapping-purchase-details-style")) return;
        const style = document.createElement("style");
        style.id = "mapping-purchase-details-style";
        style.textContent = `
            .mapping-purchase-details {
                flex: 0 0 auto;
                margin: 10px 14px 0;
                border: 1px solid var(--line);
                border-radius: 8px;
                background: #fff;
                overflow: hidden;
            }
            .mapping-purchase-details-header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 12px;
                min-height: 38px;
                padding: 7px 10px;
                border-bottom: 1px solid var(--line);
                background: var(--warm);
            }
            .mapping-purchase-details-header strong {
                font-size: 12px;
            }
            .mapping-purchase-details-header span {
                color: var(--muted);
                font-size: 10px;
                font-weight: 700;
            }
            .mapping-purchase-details .purchase-form {
                grid-template-columns: repeat(6, minmax(120px, 1fr));
                gap: 7px;
                padding: 8px 10px;
                border: 0;
                border-radius: 0;
                background: #fff;
            }
            .mapping-purchase-details .purchase-form label {
                font-size: 10px;
            }
            .mapping-purchase-details .purchase-form input,
            .mapping-purchase-details .purchase-form select {
                height: 30px;
                font-size: 11px;
            }
            .mapping-purchase-details .purchase-form .wide {
                grid-column: span 2;
            }
            .current-mapping-card {
                border-color: #b7dcc4 !important;
                background: #f3fbf6 !important;
            }
            @media (max-width: 1100px) {
                .mapping-purchase-details .purchase-form {
                    grid-template-columns: repeat(3, minmax(120px, 1fr));
                }
            }
            @media (max-width: 760px) {
                .mapping-purchase-details .purchase-form {
                    grid-template-columns: 1fr;
                }
                .mapping-purchase-details .purchase-form .wide {
                    grid-column: auto;
                }
            }
        `;
        document.head.appendChild(style);
    }

    function mountPurchaseFormForMapping() {
        if (!mappingMode) return;
        const mappingView = document.getElementById("mapping-view");
        const form = document.getElementById("purchase-form");
        if (!mappingView || !form) return;

        ensureMappingPurchaseStyles();
        let panel = document.getElementById("mapping-purchase-details");
        if (!panel) {
            panel = document.createElement("section");
            panel.id = "mapping-purchase-details";
            panel.className = "mapping-purchase-details";
            const header = document.createElement("div");
            header.className = "mapping-purchase-details-header";
            header.innerHTML = "<strong>Purchase Details</strong><span>These values stay with this document while you map items.</span>";
            const host = document.createElement("div");
            host.id = "mapping-purchase-form-host";
            panel.append(header, host);
            const layout = mappingView.querySelector(".mapping-layout");
            mappingView.insertBefore(panel, layout || mappingView.firstChild);
        }

        const host = document.getElementById("mapping-purchase-form-host");
        if (host && form.parentElement !== host) host.appendChild(form);
        form.hidden = false;

        try {
            if (typeof loadPurchaseHeader === "function" && typeof applyPurchaseHeader === "function") {
                applyPurchaseHeader(loadPurchaseHeader(currentDocumentJobId));
            }
        } catch {
            // The server-side purchase resolver will still validate any missing fields.
        }
    }

    function ensurePurchaseContextOnMapping() {
        if (!mappingMode) return;
        if (window.__purchaseContext?.initialize) {
            window.__purchaseContext.initialize();
            return;
        }
        if (document.querySelector('script[data-mapping-purchase-context="true"]')) return;
        const script = document.createElement("script");
        script.src = apiUrl("/static/purchase-context.js?v=20260920-direct-flow-v3");
        script.dataset.mappingPurchaseContext = "true";
        script.onload = () => window.__purchaseContext?.initialize?.();
        document.head.appendChild(script);
    }

    function keepDocumentMappingReviewAvailable(payload, {qr = false} = {}) {
        const job = payload?.job || {};
        const summary = payload?.summary || {};
        const jobId = sanitizeJobId(job.id || currentDocumentJobId || "");
        const detected = Number(summary.detected ?? job.extracted_count ?? 0) || 0;
        const recognized = Number(summary.recognized ?? job.mapped_count ?? 0) || 0;
        const needMapping = Number(summary.needMapping ?? Math.max(0, detected - recognized)) || 0;
        const button = document.getElementById("continue-document-mapping");
        if (!button || !jobId || detected <= 0) return;

        // Mapping is also the purchase-item review screen. Keep it available even
        // when every row is already mapped so the operator can see what is being
        // purchased, verify the saved Madhushala item, and change a wrong mapping.
        button.hidden = false;
        button.textContent = needMapping > 0
            ? (qr ? `Map ${needMapping} QR Item${needMapping === 1 ? "" : "s"}` : `Map ${needMapping} Item${needMapping === 1 ? "" : "s"}`)
            : (qr ? "Review QR Item Mappings" : "Review Item Mappings");

        if (needMapping === 0) {
            setText(
                document.getElementById("document-action-summary"),
                `${detected} products extracted | ${recognized || detected} mapped | Review/change mappings if needed`,
            );
        }
    }

    // The original review hid the Mapping button when needMapping === 0. That made
    // a fully mapped QR appear to contain no extracted rows. Always expose the
    // mapping/review screen for a real document job.
    const originalRenderDocumentReview = renderDocumentReview;
    renderDocumentReview = function renderDocumentReviewWithDirectMapping(payload) {
        const {items, invalid} = reviewConfirmItems(payload);
        if (!items.length || invalid.length) {
            suppressSuccessfulReview = false;
            originalRenderDocumentReview(payload);
            keepDocumentMappingReviewAvailable(payload, {qr: currentUploadKind === "qr"});
            return;
        }

        // Successful extraction must never render the old source/extracted-products
        // preview. Prime the hidden Purchase fields, show only processing state,
        // and move directly into Mapping.
        primeSuccessfulImport(payload);
        void autoConfirmExtractedReview(payload);
    };

    const originalRenderQrReview = renderQrReview;
    renderQrReview = function renderQrReviewWithDirectMapping(payload) {
        const {items, invalid} = reviewConfirmItems(payload);
        if (payload?.job && items.length && !invalid.length) {
            currentUploadKind = "qr";
            renderDocumentReview(payload);
            return;
        }
        suppressSuccessfulReview = false;
        originalRenderQrReview(payload);
        if (payload?.job) keepDocumentMappingReviewAvailable(payload, {qr: true});
    };

    mappedItemForRow = function mappedItemForUniqueRow(item) {
        const code = selectedMappings.get(mappingRowKey(item)) || item.selectedItemCode || "";
        if (!code) return null;
        return findMadhushalaItem(code) || item.selectedItem || {itemCode: code, itemName: "Mapped item"};
    };

    currentExciseItem = function currentUniqueDocumentRow() {
        return rowForKey(selectedExciseCode);
    };

    function setDocumentListTitle() {
        if (!isDocumentWorkspace()) return;
        const title = document.querySelector("#mapping-view .list-title span:first-child");
        if (title) title.textContent = "Extracted Items";
        const mapperTitle = document.querySelector("#mapping-view .mapper-title");
        if (mapperTitle) mapperTitle.textContent = "Madhushala Mapping / Re-map";
    }

    updateSummary = function updateUniqueRowSummary() {
        const rows = workspace.unmappedItems || [];
        const left = rows.filter(
            (item) => !selectedMappings.get(mappingRowKey(item)) && !item.selectedItemCode,
        ).length;
        const mapped = Math.max(0, rows.length - left);
        setText(
            document.getElementById("mapping-summary"),
            isDocumentWorkspace()
                ? `Extracted: ${rows.length} | Mapped: ${mapped} | Unmapped: ${left}`
                : `Selected: ${selectedMappings.size} | Left: ${left}`,
        );
        const submit = document.getElementById("submit-mappings");
        if (submit) submit.disabled = selectedMappings.size === 0;
        const next = document.getElementById("mapping-next-purchase");
        if (next) next.disabled = !sanitizeJobId(currentDocumentJobId) || left > 0;
    };

    renderWorkspace = function renderUniqueDocumentRows() {
        const list = document.getElementById("unmapped-items");
        if (!list) return;
        setDocumentListTitle();
        setText(document.getElementById("unmapped-count"), String(workspace.unmappedItems.length));

        if (!workspace.unmappedItems.length) {
            list.className = "list-body empty";
            list.textContent = "No extracted items found for this document";
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
                    <span class="${mapped ? "map-badge done" : "map-badge"}">${mapped ? "Mapped" : "Pending"}</span>
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

    renderSelectedExcise = function renderDocumentMappingSelection(item) {
        const card = document.getElementById("best-match-card");
        const search = document.getElementById("madhushala-search");
        if (!item) {
            renderCandidates([]);
            setHidden(card, true);
            return;
        }

        const mapped = mappedItemForRow(item);
        if (mapped) {
            if (card) {
                card.className = "best-match-card current-mapping-card";
                card.hidden = false;
                card.innerHTML = `
                    <div>
                        <span class="eyebrow">Mapped</span>
                        <h3>${escapeHtml(mapped.itemCode || "")} - ${escapeHtml(mapped.itemName || "Mapped item")}</h3>
                        <p>This is the saved Madhushala item for the extracted purchase row.</p>
                    </div>
                    <button type="button" id="change-current-mapping" class="secondary">Change / Re-map</button>`;
                document.getElementById("change-current-mapping")?.addEventListener("click", () => {
                    if (search) search.value = "";
                    renderCandidates(item.suggestions || []);
                    search?.focus();
                });
            }
            if (search) search.placeholder = "Search only if you want to change this mapping";
            if (!search?.value?.trim()) {
                const container = document.getElementById("suggestions");
                if (container) {
                    container.className = "candidate-list empty";
                    container.textContent = "Already mapped. Use Change / Re-map only if the saved item is wrong.";
                }
            } else {
                runSearch();
            }
            return;
        }

        if (card) card.className = "best-match-card";
        if (search) search.placeholder = "Search name, code, barcode or ML";
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
            document.getElementById("choose-another")?.addEventListener("click", () => search?.focus());
        } else {
            setHidden(card, true);
        }
        renderCandidates(item.suggestions || []);
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
            showToast("Mapping selected", "success");
            const search = document.getElementById("madhushala-search");
            if (search) search.value = "";
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
        try {
            const query = normalizedJobId ? `?jobId=${encodeURIComponent(normalizedJobId)}` : "?latestOnly=true";
            workspace = await api(`/mapping/workspace${query}`);
            currentDocumentJobId = normalizedJobId || sanitizeJobId(workspace?.jobId) || currentDocumentJobId;
            const keys = new Set((workspace.unmappedItems || []).map(mappingRowKey));
            selectedExciseCode = previousSelected && keys.has(String(previousSelected)) ? previousSelected : null;

            // Never restore a stale search value captured before the request. The user
            // may have typed while the request was in flight; keep the live DOM value.
            const liveSearch = preserveState ? (search?.value || "") : "";
            if (!preserveState && search) search.value = "";
            renderWorkspace();
            if (liveSearch.trim()) {
                if (search) search.value = liveSearch;
                runSearch();
            }
        } catch (error) {
            if (!options.quiet) showToast(error.message || "Could not load mapping", "error");
        }
    };

    startMappingAutoRefresh = function startStableMappingAutoRefresh() {
        // Document mappings are changed by this page itself, so polling every three
        // seconds only causes UI churn and can interfere with search typing.
        if (sanitizeJobId(currentDocumentJobId)) return;
        if (mappingRefreshTimer) return;
        mappingRefreshTimer = window.setInterval(async () => {
            const search = document.getElementById("madhushala-search");
            if (
                mappingRefreshInFlight
                || document.getElementById("mapping-view")?.hidden
                || document.activeElement === search
            ) return;
            mappingRefreshInFlight = true;
            try {
                await loadWorkspace(currentDocumentJobId, {quiet: true, preserveState: true});
            } finally {
                mappingRefreshInFlight = false;
            }
        }, 3000);
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
                // Keep the legacy direct-save control in the DOM for compatibility,
                // but the production flow now continues through the Purchase review.
                const legacySave = document.getElementById("save-purchase-from-mapping");
                if (legacySave) {
                    legacySave.hidden = true;
                    legacySave.style.display = "none";
                }
            }
            return true;
        } catch (error) {
            showToast(error.message || "Could not save mapping", "error");
            return false;
        } finally {
            if (submit) submit.disabled = selectedMappings.size === 0;
        }
    };

    async function continueMappingToPurchase() {
        const jobId = sanitizeJobId(currentDocumentJobId || workspace?.jobId || "");
        if (!jobId) {
            showToast("No document import job is available.", "error");
            return;
        }

        if (selectedMappings.size > 0) {
            const saved = await saveMappings();
            if (!saved) return;
        } else {
            await loadWorkspace(jobId, {preserveState: true});
        }

        const left = mappingRowsLeft();
        if (left > 0) {
            showToast(
                `Map all extracted items before continuing. ${left} item${left === 1 ? "" : "s"} still unmapped.`,
                "error",
            );
            updateSummary();
            return;
        }

        persistPurchaseHeader();
        window.location.href = basePath
            + "/document-import?view=purchase&jobId=" + encodeURIComponent(jobId)
            + "&sessionId=" + encodeURIComponent(sessionId)
            + "#session=" + encodeURIComponent(sessionToken);
    }

    function setupMappingNextButton() {
        if (!mappingMode || !sanitizeJobId(currentDocumentJobId)) return;
        const footer = document.querySelector("#mapping-view .mapping-footer");
        if (!footer) return;

        const legacySave = document.getElementById("save-purchase-from-mapping");
        if (legacySave) {
            legacySave.hidden = true;
            legacySave.style.display = "none";
        }

        let next = document.getElementById("mapping-next-purchase");
        if (!next) {
            next = document.createElement("button");
            next.id = "mapping-next-purchase";
            next.type = "button";
            next.textContent = "Next: Review Purchase";
            next.addEventListener("click", () => void continueMappingToPurchase());
            footer.appendChild(next);
        }
        next.disabled = true;
    }

    function ensurePurchaseReviewStyles() {
        if (document.getElementById("purchase-review-flow-style")) return;
        const style = document.createElement("style");
        style.id = "purchase-review-flow-style";
        style.textContent = `
            body.purchase-review-mode #document-review-panel {
                grid-template-columns: 1fr;
                max-width: 1500px;
                margin: 0 auto;
            }
            body.purchase-review-mode #document-review-panel .document-preview-card,
            body.purchase-review-mode #document-review-panel .document-data-card > header,
            body.purchase-review-mode #document-review-panel .document-metrics,
            body.purchase-review-mode #document-review-panel .document-review-toolbar,
            body.purchase-review-mode #document-review-panel .document-table-wrap,
            body.purchase-review-mode #document-review-panel .document-review-validation {
                display: none !important;
            }
            body.purchase-review-mode #document-review-panel .document-data-card {
                display: block;
                width: 100%;
                max-width: none;
            }
            body.purchase-review-mode #document-review-panel .purchase-details {
                display: block;
                margin: 0 0 14px;
                border: 1px solid var(--line);
                border-radius: 10px;
                background: #fff;
            }
            body.purchase-review-mode #document-review-panel .purchase-details > summary {
                padding: 14px 16px;
                font-size: 15px;
                font-weight: 800;
                cursor: default;
            }
            .purchase-final-preview {
                margin-top: 14px;
                border: 1px solid var(--line);
                border-radius: 10px;
                background: #fff;
                overflow: hidden;
            }
            .purchase-final-preview > header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 12px;
                padding: 12px 14px;
                border-bottom: 1px solid var(--line);
                background: var(--warm);
            }
            .purchase-final-preview .purchase-preview-metrics {
                display: grid;
                grid-template-columns: repeat(4, minmax(120px, 1fr));
                gap: 8px;
                padding: 12px 14px;
            }
            .purchase-final-preview .purchase-preview-metrics div {
                padding: 8px 10px;
                border: 1px solid var(--line);
                border-radius: 8px;
            }
            .purchase-final-preview .purchase-preview-metrics span {
                display: block;
                color: var(--muted);
                font-size: 10px;
                text-transform: uppercase;
                letter-spacing: .04em;
            }
            .purchase-final-preview .purchase-preview-metrics strong {
                display: block;
                margin-top: 3px;
                font-size: 14px;
            }
            .purchase-final-table-wrap {
                overflow: auto;
                max-height: 46vh;
                border-top: 1px solid var(--line);
            }
            .purchase-final-table {
                width: 100%;
                border-collapse: collapse;
                font-size: 12px;
            }
            .purchase-final-table th,
            .purchase-final-table td {
                padding: 8px 10px;
                border-bottom: 1px solid var(--line);
                text-align: left;
                white-space: nowrap;
            }
            .purchase-final-table th {
                position: sticky;
                top: 0;
                background: #f7f7f7;
                z-index: 1;
            }
            @media (max-width: 760px) {
                .purchase-final-preview .purchase-preview-metrics {
                    grid-template-columns: repeat(2, minmax(100px, 1fr));
                }
            }
        `;
        document.head.appendChild(style);
    }

    function ensureFinalPurchasePreview() {
        const card = document.querySelector("#document-review-panel .document-data-card");
        const technical = document.querySelector("#document-review-panel .document-technical");
        if (!card) return null;
        let preview = document.getElementById("purchase-final-preview");
        if (!preview) {
            preview = document.createElement("section");
            preview.id = "purchase-final-preview";
            preview.className = "purchase-final-preview";
            preview.innerHTML = `
                <header>
                    <div>
                        <span class="app-kicker">Madhushala Calculate</span>
                        <strong>Final Purchase Payload Preview</strong>
                    </div>
                    <span id="purchase-final-validation-state">Waiting for validation</span>
                </header>
                <div class="purchase-preview-metrics">
                    <div><span>Items</span><strong id="purchase-final-items">0</strong></div>
                    <div><span>Gross</span><strong id="purchase-final-gross">0</strong></div>
                    <div><span>Tax</span><strong id="purchase-final-tax">0</strong></div>
                    <div><span>Net</span><strong id="purchase-final-net">0</strong></div>
                </div>
                <div class="purchase-final-table-wrap">
                    <table class="purchase-final-table">
                        <thead>
                            <tr>
                                <th>Item Code</th><th>Item</th><th>Batch</th><th>Box</th>
                                <th>Loose</th><th>Qty</th><th>Rate</th><th>MRP</th><th>Amount</th>
                            </tr>
                        </thead>
                        <tbody id="purchase-final-table-body">
                            <tr><td colspan="9">Validate Purchase to load the final payload.</td></tr>
                        </tbody>
                    </table>
                </div>
            `;
            card.insertBefore(preview, technical || null);
        }
        return preview;
    }

    function renderFinalPurchasePayload(response) {
        if (!purchaseReviewMode) return;
        const payload = response?.purchasePayload || {};
        const items = Array.isArray(payload.items) ? payload.items : [];
        ensureFinalPurchasePreview();
        setText(document.getElementById("purchase-final-validation-state"), response?.validated ? "Validated" : "Preview");
        setText(document.getElementById("purchase-final-items"), String(items.length));
        setText(document.getElementById("purchase-final-gross"), String(payload.grossAmount ?? 0));
        setText(document.getElementById("purchase-final-tax"), String(payload.taxAmount ?? 0));
        setText(document.getElementById("purchase-final-net"), String(payload.netAmount ?? 0));

        const tbody = document.getElementById("purchase-final-table-body");
        if (tbody) {
            tbody.innerHTML = items.length
                ? items.map((item) => `
                    <tr>
                        <td>${escapeHtml(item.itemCode ?? "")}</td>
                        <td>${escapeHtml(item.itemName ?? "")}</td>
                        <td>${escapeHtml(item.batchNo ?? "")}</td>
                        <td>${escapeHtml(item.box ?? 0)}</td>
                        <td>${escapeHtml(item.loose ?? 0)}</td>
                        <td>${escapeHtml(item.qnty ?? 0)}</td>
                        <td>${escapeHtml(item.rate ?? 0)}</td>
                        <td>${escapeHtml(item.mrp ?? 0)}</td>
                        <td>${escapeHtml(item.itemAmount ?? 0)}</td>
                    </tr>
                `).join("")
                : '<tr><td colspan="9">No purchase items returned by validation.</td></tr>';
        }

        const json = document.getElementById("document-json");
        if (json) json.textContent = JSON.stringify(payload, null, 2);
    }

    function setupPurchaseReviewMode() {
        if (!purchaseReviewMode) return;
        const jobId = sanitizeJobId(currentDocumentJobId || activeJobId || "");
        if (!jobId) {
            showToast("No document import job is available for purchase review.", "error");
            return;
        }

        document.body.classList.add("purchase-review-mode");
        ensurePurchaseReviewStyles();
        setHidden(document.getElementById("launch-view"), true);
        setHidden(document.getElementById("mapping-view"), true);
        setHidden(document.getElementById("document-import-view"), false);
        setDocumentImportState("review");

        const title = document.querySelector("#document-import-view .page-toolbar h1");
        if (title) title.textContent = "Review Purchase";
        setHidden(document.getElementById("document-refresh"), true);
        setHidden(document.getElementById("document-upload-another"), true);
        setHidden(document.getElementById("continue-document-mapping"), true);
        setHidden(document.getElementById("save-purchase"), false);

        const details = document.querySelector("#document-review-panel .purchase-details");
        if (details) {
            details.open = true;
            const summary = details.querySelector("summary");
            if (summary) summary.textContent = "Purchase Details";
        }
        const technical = document.querySelector("#document-review-panel .document-technical");
        if (technical) {
            const summary = technical.querySelector("summary");
            if (summary) summary.textContent = "Final Payload JSON / Technical Details";
        }

        ensureFinalPurchasePreview();
        setText(
            document.getElementById("document-action-summary"),
            "Review the pre-filled purchase values. Validate against Madhushala, then save the purchase.",
        );

        const actionButtons = document.querySelector("#document-action-bar > div");
        if (actionButtons && !document.getElementById("purchase-back-to-mapping")) {
            const back = document.createElement("button");
            back.id = "purchase-back-to-mapping";
            back.type = "button";
            back.className = "secondary";
            back.textContent = "Back to Mapping";
            back.addEventListener("click", () => {
                persistPurchaseHeader();
                window.location.href = basePath
                    + "/?view=mapping&jobId=" + encodeURIComponent(jobId)
                    + "&sessionId=" + encodeURIComponent(sessionId)
                    + "#session=" + encodeURIComponent(sessionToken);
            });
            actionButtons.insertBefore(back, actionButtons.firstChild);
        }

        try {
            applyPurchaseHeader(loadPurchaseHeader(jobId));
        } catch {
            // Purchase context below will still populate server-side defaults.
        }

        const validateWhenReady = async () => {
            try {
                const hint = loadPurchaseSourceHint(jobId);
                await window.__purchaseContext?.refresh?.(hint.supplierName || "");
                applyPurchaseHeader(loadPurchaseHeader(jobId));
                await window.__purchaseContext?.validate?.("review", {showSuccessToast: false});
            } catch {
                // The explicit Validate Purchase button remains available.
            }
        };
        window.setTimeout(() => void validateWhenReady(), 0);
    }

    const originalInitMapping = initMapping;
    initMapping = function initMappingWithPurchaseDetails() {
        // Do not mount Purchase fields on Mapping in the simplified flow.
        // The function is retained above so the old UI can be restored later.
        ensurePurchaseContextOnMapping();
        setupMappingNextButton();
        const result = originalInitMapping();
        const jobId = sanitizeJobId(currentDocumentJobId || activeJobId || "");
        if (jobId) {
            const hint = loadPurchaseSourceHint(jobId);
            window.setTimeout(() => {
                void window.__purchaseContext?.refresh?.(hint.supplierName || "");
            }, 0);
        }
        const legacySave = document.getElementById("save-purchase-from-mapping");
        if (legacySave) {
            legacySave.hidden = true;
            legacySave.style.display = "none";
        }
        return result;
    };

    // If the user chooses items and clicks Save Purchase directly, persist the
    // current purchase fields and pending row selections before creating Purchase.
    const originalSavePurchaseFromJob = savePurchaseFromJob;
    savePurchaseFromJob = async function savePurchaseAfterPendingMappings(source = "review") {
        if (source === "mapping" && typeof persistPurchaseHeader === "function") {
            persistPurchaseHeader();
        }
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

    window.addEventListener("purchase-preview-ready", (event) => {
        renderFinalPurchasePayload(event.detail || {});
    });

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", setupPurchaseReviewMode, {once: true});
    } else {
        setupPurchaseReviewMode();
    }

    window.__mappingRowIdentity = {
        mappingRowKey,
        mountPurchaseFormForMapping,
        keepDocumentMappingReviewAvailable,
        autoConfirmExtractedReview,
        continueMappingToPurchase,
        renderFinalPurchasePayload,
    };
})();
