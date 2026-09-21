(() => {
    const purchaseReviewMode = pageParams.get("view") === "purchase";

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

    let managementStatusFilter = "all";

    function filteredManagementRows() {
        const rows = workspace?.unmappedItems || [];
        if (!mappingManagementMode) return rows;
        const query = String(document.getElementById("mapping-management-search")?.value || "")
            .trim()
            .toLowerCase();
        return rows.filter((item) => {
            const mapped = Boolean(selectedMappings.get(mappingRowKey(item)) || item.selectedItemCode);
            if (managementStatusFilter === "mapped" && !mapped) return false;
            if (managementStatusFilter === "unmapped" && mapped) return false;
            if (!query) return true;
            const mappedItem = mappedItemForRow(item);
            const haystack = [
                item.exciseItemCode,
                item.itemName,
                item.capturedItem?.brand,
                item.capturedItem?.measureMl,
                mappedItem?.itemCode,
                mappedItem?.itemName,
            ].map((value) => String(value || "").toLowerCase()).join(" ");
            return haystack.includes(query);
        });
    }

    function setupMappingManagementControls() {
        if (!mappingManagementMode) return;
        const search = document.getElementById("mapping-management-search");
        search?.addEventListener("input", () => renderWorkspace());
        document.querySelectorAll("[data-mapping-filter]").forEach((button) => {
            button.addEventListener("click", () => {
                managementStatusFilter = String(button.dataset.mappingFilter || "all");
                document.querySelectorAll("[data-mapping-filter]").forEach((candidate) => {
                    candidate.classList.toggle("active", candidate === button);
                });
                selectedExciseCode = null;
                renderWorkspace();
            });
        });
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
        if (!mappingMode || !sanitizeJobId(currentDocumentJobId || activeJobId || "")) return;
        if (window.__purchaseContext?.initialize) {
            window.__purchaseContext.initialize();
            return;
        }
        if (document.querySelector('script[data-mapping-purchase-context="true"]')) return;
        const script = document.createElement("script");
        script.src = apiUrl("/static/purchase-context.js?v=20260921-inline-mapping-picker-v14");
        script.dataset.mappingPurchaseContext = "true";
        script.onload = () => window.__purchaseContext?.initialize?.();
        document.head.appendChild(script);
    }

    mappedItemForRow = function mappedItemForUniqueRow(item) {
        const code = selectedMappings.get(mappingRowKey(item)) || item.selectedItemCode || "";
        if (!code) return null;
        return findMadhushalaItem(code) || item.selectedItem || {itemCode: code, itemName: "Mapped item"};
    };

    currentExciseItem = function currentUniqueDocumentRow() {
        return rowForKey(selectedExciseCode);
    };

    function setDocumentListTitle() {
        const title = document.querySelector("#mapping-view .list-title span:first-child");
        const mapperTitle = document.querySelector("#mapping-view .mapper-title");
        if (mappingManagementMode) {
            if (title) title.textContent = "All Excise Items";
            if (mapperTitle) mapperTitle.textContent = "Madhushala Mapping / Re-map";
            return;
        }
        if (!isDocumentWorkspace()) return;
        if (title) title.textContent = "Extracted Items";
        if (mapperTitle) mapperTitle.textContent = "Madhushala Mapping / Re-map";
    }

    updateSummary = function updateUniqueRowSummary() {
        const rows = workspace.unmappedItems || [];
        const left = rows.filter(
            (item) => !selectedMappings.get(mappingRowKey(item)) && !item.selectedItemCode,
        ).length;
        const mapped = Math.max(0, rows.length - left);
        const visible = mappingManagementMode ? filteredManagementRows().length : rows.length;
        const mappingSummary = document.getElementById("mapping-summary");
        if (mappingSummary) {
            if (isDocumentWorkspace() && !mappingManagementMode) {
                mappingSummary.hidden = true;
                mappingSummary.textContent = "";
            } else {
                mappingSummary.hidden = false;
                setText(
                    mappingSummary,
                    mappingManagementMode
                        ? `All: ${rows.length} | Mapped: ${mapped} | Unmapped: ${left} | Showing: ${visible}`
                        : `Selected: ${selectedMappings.size} | Left: ${left}`,
                );
            }
        }
        const submit = document.getElementById("submit-mappings");
        if (submit) submit.disabled = selectedMappings.size === 0;
        const next = document.getElementById("mapping-next-purchase");
        if (next) next.disabled = !sanitizeJobId(currentDocumentJobId) || left > 0;
    };

    function managementItemSearchValue(candidate) {
        return itemLabel(candidate);
    }

    function managementSearchResults(query, limit = 12) {
        const clean = normalizeSearchText(query);
        const compact = clean.replace(/\s/g, "");
        const rows = (workspace.madhushalaItems || []).map((candidate) => {
            const code = String(candidate.itemCode || "").trim();
            const name = String(candidate.itemName || "").trim();
            const label = managementItemSearchValue(candidate);
            const codeNorm = normalizeSearchText(code);
            const nameNorm = normalizeSearchText(name);
            const labelNorm = normalizeSearchText(label);
            let score = 0;

            if (!clean) score = 1;
            else {
                if (codeNorm === clean) score += 140;
                if (codeNorm.startsWith(clean)) score += 110;
                if (nameNorm.startsWith(clean)) score += 100;
                if (labelNorm.startsWith(clean)) score += 90;
                if (nameNorm.includes(clean)) score += 65;
                if (labelNorm.includes(clean)) score += 55;
                if (compact && nameNorm.replace(/\s/g, "").includes(compact)) score += 45;
            }
            return {candidate, score};
        }).filter((entry) => entry.score > 0);

        rows.sort((left, right) => right.score - left.score
            || managementItemSearchValue(left.candidate).localeCompare(managementItemSearchValue(right.candidate)));
        return rows.slice(0, limit).map((entry) => entry.candidate);
    }

    function closeManagementPicker(except = null) {
        document.querySelectorAll(".management-search-results.open").forEach((results) => {
            if (results !== except) results.classList.remove("open");
        });
    }

    function renderManagementPickerResults(input, results, query = "") {
        if (!input || !results) return;
        const matches = managementSearchResults(query);
        if (!matches.length) {
            results.innerHTML = '<div class="management-search-empty">No matching Item Master item</div>';
            results.classList.add("open");
            return;
        }

        results.innerHTML = matches.map((candidate) => {
            const code = String(candidate.itemCode || "").trim();
            const name = String(candidate.itemName || "").trim();
            const ml = candidate.ml ? ` · ${escapeHtml(String(candidate.ml))} ML` : "";
            return `
                <button type="button" class="management-search-option" data-item-code="${escapeHtml(code)}">
                    <strong>${escapeHtml(code)}</strong>
                    <span>${escapeHtml(name)}${ml}</span>
                </button>`;
        }).join("");
        results.classList.add("open");

        results.querySelectorAll("[data-item-code]").forEach((button) => {
            button.addEventListener("mousedown", (event) => {
                event.preventDefault();
                const code = String(button.dataset.itemCode || "").trim();
                if (!code) return;
                input.dataset.chosenCode = code;
                input.value = managementItemSearchValue(findMadhushalaItem(code) || {itemCode: code, itemName: ""});
                results.classList.remove("open");
                input.dispatchEvent(new CustomEvent("mapping-item-selected", {detail: {itemCode: code}}));
            });
        });
    }

    function renderManagementTable(rows) {
        const list = document.getElementById("unmapped-items");
        if (!list) return;
        list.className = "list-body management-map-grid";
        setText(document.getElementById("unmapped-count"), `${rows.length}/${(workspace.unmappedItems || []).length}`);

        if (!rows.length) {
            list.className = "list-body empty";
            list.textContent = "No items match this filter";
            updateSummary();
            return;
        }

        list.innerHTML = `
            <div class="management-map-head" role="row">
                <div role="columnheader">Excise Item</div>
                <div role="columnheader">Item Master</div>
            </div>
            ${rows.map((item) => {
                const rowKey = mappingRowKey(item);
                const originalCode = String(item.selectedItemCode || "");
                const pendingCode = String(selectedMappings.get(rowKey) || "");
                const effectiveCode = pendingCode || originalCode;
                const mapped = effectiveCode ? (findMadhushalaItem(effectiveCode) || mappedItemForRow(item)) : null;
                const changed = Boolean(pendingCode && pendingCode !== originalCode);
                const selectedLabel = mapped ? itemLabel(mapped) : "Not mapped";
                return `
                    <div class="management-map-row ${changed ? "changed" : ""}" role="row" data-management-row="${escapeHtml(rowKey)}">
                        <div class="management-excise-cell" role="cell">
                            <strong>${escapeHtml(item.itemName || "Excise item")}</strong>
                            <small>Code: ${escapeHtml(String(item.exciseItemCode ?? ""))}</small>
                        </div>
                        <div class="management-master-cell" role="cell">
                            <div class="management-item-picker">
                                <input
                                    class="management-item-search"
                                    data-row-key="${escapeHtml(rowKey)}"
                                    data-current-code="${escapeHtml(effectiveCode)}"
                                    data-chosen-code="${escapeHtml(effectiveCode)}"
                                    value="${escapeHtml(selectedLabel)}"
                                    placeholder="Search item code or name"
                                    autocomplete="off"
                                    aria-label="Search mapping for ${escapeHtml(item.itemName || "Excise item")}"
                                >
                                <div class="management-search-results" role="listbox"></div>
                            </div>
                            ${changed ? '<span class="management-unsaved">Unsaved change</span>' : ""}
                        </div>
                    </div>`;
            }).join("")}`;

        list.querySelectorAll(".management-item-search").forEach((input) => {
            const rowKey = String(input.dataset.rowKey || "");
            const item = rowForKey(rowKey);
            const picker = input.closest(".management-item-picker");
            const results = picker?.querySelector(".management-search-results");
            if (!item || !results) return;

            const applyCode = (itemCode) => {
                const code = String(itemCode || "").trim();
                if (!code) return;
                selectedExciseCode = rowKey;
                input.dataset.chosenCode = code;
                selectMadhushalaItem(code);
            };

            input.addEventListener("focus", () => {
                closeManagementPicker(results);
                input.select();
                renderManagementPickerResults(input, results, "");
            });
            input.addEventListener("input", () => {
                input.dataset.chosenCode = "";
                renderManagementPickerResults(input, results, input.value);
            });
            input.addEventListener("mapping-item-selected", (event) => {
                applyCode(event.detail?.itemCode);
            });
            input.addEventListener("keydown", (event) => {
                if (event.key === "Escape") {
                    results.classList.remove("open");
                    input.blur();
                    return;
                }
                if (event.key === "Enter") {
                    event.preventDefault();
                    const first = results.querySelector("[data-item-code]");
                    if (first) first.dispatchEvent(new MouseEvent("mousedown", {bubbles: true}));
                }
            });
            input.addEventListener("blur", () => {
                window.setTimeout(() => {
                    results.classList.remove("open");
                    const currentCode = String(input.dataset.chosenCode || input.dataset.currentCode || "");
                    const current = currentCode ? (findMadhushalaItem(currentCode) || mappedItemForRow(item)) : null;
                    input.value = current ? itemLabel(current) : "Not mapped";
                }, 120);
            });
        });

        const submit = document.getElementById("submit-mappings");
        if (submit) submit.textContent = "Save";
        updateSummary();
    }

    renderWorkspace = function renderUniqueDocumentRows() {
        const list = document.getElementById("unmapped-items");
        if (!list) return;
        setDocumentListTitle();
        const rows = mappingManagementMode ? filteredManagementRows() : (workspace.unmappedItems || []);

        if (mappingManagementMode) {
            renderManagementTable(rows);
            return;
        }

        setText(document.getElementById("unmapped-count"), String(rows.length));

        if (!rows.length) {
            list.className = "list-body empty";
            list.textContent = "No extracted items found for this document";
            selectedExciseCode = null;
            renderSelectedExcise(null);
            updateSummary();
            return;
        }

        const firstKey = mappingRowKey(rows[0]);
        const visibleKeys = new Set(rows.map(mappingRowKey));
        if (!selectedExciseCode || !visibleKeys.has(String(selectedExciseCode))) selectedExciseCode = firstKey;

        const documentModeRows = isDocumentWorkspace();
        list.className = documentModeRows ? "list-body document-map-list" : "list-body";
        list.innerHTML = rows.map((item) => {
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
            const query = normalizedJobId
                ? `?jobId=${encodeURIComponent(normalizedJobId)}`
                : (mappingManagementMode ? "?latestOnly=false&includeMapped=true" : "?latestOnly=true");
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
        // Document and management mappings are changed by this page itself, so
        // polling every three seconds only causes UI churn and expensive catalogue reloads.
        if (mappingManagementMode || sanitizeJobId(currentDocumentJobId)) return;
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

    const MADHUSHALA_PURCHASE_URL = "https://report.madhushalasoftware.com";

    function openPurchaseInMadhushala(purchase) {
        if (!purchase || typeof purchase !== "object") {
            throw new Error("Purchase payload is missing.");
        }
        if (!String(purchase.shopCode || "").trim()) {
            throw new Error("Purchase payload has no shopCode.");
        }
        if (!String(purchase.companyCode || "").trim()) {
            throw new Error("Purchase payload has no companyCode.");
        }
        if (!Array.isArray(purchase.items) || purchase.items.length === 0) {
            throw new Error("Purchase payload has no item lines.");
        }
        const missingItemCode = purchase.items.findIndex(
            (item) => !item || !String(item.itemCode || "").trim(),
        );
        if (missingItemCode >= 0) {
            throw new Error(`Purchase item ${missingItemCode + 1} has no itemCode.`);
        }

        const bytes = new TextEncoder().encode(JSON.stringify(purchase));
        let binary = "";
        const chunkSize = 0x8000;
        for (let offset = 0; offset < bytes.length; offset += chunkSize) {
            binary += String.fromCharCode.apply(
                null,
                bytes.subarray(offset, offset + chunkSize),
            );
        }
        const encoded = btoa(binary)
            .replace(/\+/g, "-")
            .replace(/\//g, "_")
            .replace(/=+$/, "");

        const url = MADHUSHALA_PURCHASE_URL.replace(/\/+$/, "")
            + "/app/purchase#prefill=" + encoded;
        window.top.location.href = url;
    }

    async function buildPurchaseHandoff(jobId) {
        return api(
            `/api/v1/document-import/jobs/${encodeURIComponent(jobId)}/purchase/handoff`,
        );
    }

    async function continueMappingToPurchase() {
        const jobId = sanitizeJobId(currentDocumentJobId || workspace?.jobId || "");
        if (!jobId) {
            showToast("No document import job is available.", "error");
            return;
        }

        const next = document.getElementById("mapping-next-purchase");
        const originalText = next?.textContent || "Next: Purchase";
        if (next) {
            next.disabled = true;
            next.textContent = "Preparing Purchase…";
        }

        try {
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

            const preview = await buildPurchaseHandoff(jobId);
            const purchase = preview?.purchasePayload;
            openPurchaseInMadhushala(purchase);
        } catch (error) {
            showToast(error?.message || "Could not prepare Madhushala Purchase.", "error");
        } finally {
            if (next && document.contains(next)) {
                next.textContent = originalText;
                updateSummary();
            }
        }
    }

    function setupMappingNextButton() {
        if (!mappingMode || !sanitizeJobId(currentDocumentJobId)) return;
        const footer = document.querySelector("#mapping-view .mapping-footer");
        if (!footer) return;

        let next = document.getElementById("mapping-next-purchase");
        if (!next) {
            next = document.createElement("button");
            next.id = "mapping-next-purchase";
            next.type = "button";
            next.textContent = "Next: Purchase";
            next.addEventListener("click", () => void continueMappingToPurchase());
            footer.appendChild(next);
        }
        next.disabled = true;
    }

    function ensureCompactMappingStyles() {
        if (document.getElementById("compact-mapping-final-ui-style")) return;
        const style = document.createElement("style");
        style.id = "compact-mapping-final-ui-style";
        style.textContent = `
            body.mapping-mode {
                background: #fff !important;
                overflow: hidden !important;
            }
            body.mapping-mode #mapping-view {
                width: 100% !important;
                max-width: none !important;
                height: 100vh !important;
                min-height: 100vh !important;
                margin: 0 !important;
                padding: 4px 6px 5px !important;
                gap: 4px !important;
            }
            body.mapping-mode #mapping-view .mapping-purchase-details,
            body.mapping-mode #document-import-view > .page-toolbar,
            body.mapping-mode .document-preview-actions,
            body.mapping-mode .app-header {
                display: none !important;
            }
            body.mapping-mode #mapping-view .mapping-layout {
                flex: 1 1 auto !important;
                height: calc(100vh - 50px) !important;
                min-height: 0 !important;
                max-height: none !important;
                gap: 6px !important;
                padding: 0 !important;
                margin: 0 !important;
            }
            body.mapping-management-mode #mapping-view .mapping-layout {
                flex: 1 1 0 !important;
                height: auto !important;
            }
            body.mapping-management-mode #mapping-management-toolbar {
                flex: 0 0 auto !important;
            }
            body.mapping-management-mode #mapping-view .mapping-layout {
                display: block !important;
                overflow: hidden !important;
                border: 1px solid #b8b8b8 !important;
                background: #dedede !important;
            }
            body.mapping-management-mode #mapping-view .unmapped-list {
                width: 100% !important;
                height: 100% !important;
                border: 0 !important;
                border-radius: 0 !important;
                background: #dedede !important;
            }
            body.mapping-management-mode #mapping-view .unmapped-list .list-title {
                display: none !important;
            }
            body.mapping-management-mode #mapping-view .mapper {
                display: none !important;
            }
            body.mapping-management-mode #mapping-view .management-map-grid {
                height: 100% !important;
                overflow: auto !important;
                background: #dedede !important;
            }
            .management-map-head,
            .management-map-row {
                display: grid;
                grid-template-columns: 44% 56%;
                min-width: 760px;
            }
            .management-map-head {
                position: sticky;
                top: 0;
                z-index: 3;
                background: #ffd400;
                color: #171717;
                font-size: 12px;
                font-weight: 700;
            }
            .management-map-head > div {
                padding: 5px 8px;
                border-right: 1px solid #d8b900;
                border-bottom: 1px solid #c4a900;
            }
            .management-map-row {
                min-height: 46px;
                border-bottom: 1px solid #c8c8c8;
                background: #dedede;
            }
            .management-map-row.changed {
                background: #fff1ad;
            }
            .management-excise-cell {
                min-width: 0;
                min-height: 46px;
                padding: 6px 9px;
                border-right: 1px solid #c8c8c8;
                background: #fff;
                color: #111827;
            }
            .management-item-picker {
                position: relative;
                flex: 1 1 auto;
                min-width: 0;
            }
            .management-item-search {
                width: 100%;
                min-width: 0;
                height: 32px;
                padding: 5px 30px 5px 9px;
                border: 1px solid #aeb5be;
                border-radius: 5px;
                background: #fff;
                color: #111827;
                font: 11px Arial, Helvetica, sans-serif;
            }
            .management-item-search:focus {
                outline: 2px solid rgba(255, 196, 0, .28);
                border-color: #e6b400;
            }
            .management-item-picker::after {
                content: "⌄";
                position: absolute;
                right: 9px;
                top: 5px;
                color: #555;
                font-size: 16px;
                pointer-events: none;
            }
            .management-search-results {
                position: absolute;
                left: 0;
                right: 0;
                top: calc(100% + 3px);
                z-index: 60;
                display: none;
                max-height: 260px;
                overflow: auto;
                border: 1px solid #c9a900;
                border-radius: 6px;
                background: #fff;
                box-shadow: 0 12px 28px rgba(0, 0, 0, .18);
            }
            .management-search-results.open {
                display: block;
            }
            .management-search-option {
                width: 100%;
                min-height: 40px;
                display: grid;
                grid-template-columns: minmax(90px, 150px) 1fr;
                gap: 8px;
                align-items: center;
                padding: 7px 9px;
                border: 0;
                border-bottom: 1px solid #ececec;
                border-radius: 0;
                background: #fff;
                color: #181818;
                text-align: left;
            }
            .management-search-option:hover {
                background: #fff7c7;
            }
            .management-search-option strong,
            .management-search-option span {
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
            }
            .management-search-empty {
                padding: 11px;
                color: #777;
                font-size: 11px;
            }
            .management-master-cell {
                min-width: 0;
                padding: 4px 7px;
                border-right: 1px solid #c8c8c8;
            }
            .management-excise-cell {
                display: flex;
                flex-direction: column;
                justify-content: center;
                gap: 2px;
            }
            .management-excise-cell strong {
                display: block;
                overflow: visible;
                font-size: 11px;
                font-weight: 600;
                line-height: 1.25;
                white-space: normal;
                overflow-wrap: anywhere;
            }
            .management-excise-cell small {
                color: #676767;
                font-size: 9px;
            }
            .management-master-cell {
                display: flex;
                align-items: center;
                gap: 7px;
            }
            .management-unsaved {
                flex: 0 0 auto;
                color: #7c5f00;
                font-size: 9px;
                font-weight: 700;
                white-space: nowrap;
            }
            body.mapping-management-mode #mapping-view .mapping-footer {
                border: 1px solid #b8b8b8 !important;
                background: #f1f1f1 !important;
            }
            body.mapping-mode #mapping-view .unmapped-list,
            body.mapping-mode #mapping-view .mapper {
                height: 100% !important;
                min-height: 0 !important;
                max-height: none !important;
                border-radius: 4px !important;
            }
            body.mapping-mode #mapping-view .list-title,
            body.mapping-mode #mapping-view .mapper-title {
                min-height: 30px !important;
                padding: 5px 8px !important;
                font-size: 11px !important;
            }
            body.mapping-mode #mapping-view .list-body,
            body.mapping-mode #mapping-view .candidate-list {
                min-height: 0 !important;
                max-height: none !important;
            }
            body.mapping-mode #mapping-view .mapping-footer {
                flex: 0 0 42px !important;
                min-height: 42px !important;
                margin: 0 !important;
                padding: 5px 8px !important;
                border-radius: 4px !important;
                gap: 7px !important;
            }
            body.mapping-mode #mapping-view .mapping-footer button {
                min-height: 30px !important;
                height: 30px !important;
                padding: 0 12px !important;
                font-size: 11px !important;
            }
            body.mapping-mode #mapping-summary {
                margin-right: auto !important;
                font-size: 11px !important;
            }
        `;
        document.head.appendChild(style);
    }

    function formatPurchaseMoney(value) {
        const number = Number(value ?? 0);
        return Number.isFinite(number) ? number.toFixed(2) : String(value ?? "0.00");
    }

    function ensureSchemeFieldForBill() {
        if (document.getElementById("purchase-scheme-code")) return;
        const form = document.getElementById("purchase-form");
        if (!form) return;
        const label = document.createElement("label");
        label.append(document.createTextNode("Scheme"));
        const select = document.createElement("select");
        select.id = "purchase-scheme-code";
        select.name = "schemeCode";
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "None";
        select.appendChild(option);
        label.appendChild(select);
        const accountField = document.getElementById("purchase-acc-code")?.closest("label");
        form.insertBefore(label, accountField || null);
    }

    function setBillFieldLabel(label, text, className = "") {
        if (!label) return;
        label.className = `ms-bill-field ${className}`.trim();
        const textNode = Array.from(label.childNodes).find(
            (node) => node.nodeType === Node.TEXT_NODE && String(node.textContent || "").trim(),
        );
        if (textNode) textNode.textContent = text;
    }

    function staticBillField(id, label, value = "-") {
        const field = document.createElement("div");
        field.id = id;
        field.className = "ms-bill-field ms-bill-static-field";
        field.innerHTML = `<span class="ms-bill-label">${escapeHtml(label)}</span><div class="ms-bill-static-value">${escapeHtml(value)}</div>`;
        return field;
    }

    function ensurePurchaseReviewStyles() {
        if (document.getElementById("purchase-review-flow-style")) return;
        const style = document.createElement("style");
        style.id = "purchase-review-flow-style";
        style.textContent = `
            body.purchase-review-mode {
                background: #d8dadd !important;
                overflow: auto;
            }
            body.purchase-review-mode #document-import-view {
                width: 100% !important;
                max-width: none !important;
                margin: 0 !important;
                padding: 3px !important;
            }
            body.purchase-review-mode #document-import-view > .page-toolbar,
            body.purchase-review-mode #document-review-panel .document-preview-card,
            body.purchase-review-mode #document-review-panel .document-data-card > header,
            body.purchase-review-mode #document-review-panel .document-metrics,
            body.purchase-review-mode #document-review-panel .document-review-toolbar,
            body.purchase-review-mode #document-review-panel .document-table-wrap,
            body.purchase-review-mode #document-review-panel .document-review-validation,
            body.purchase-review-mode #document-review-panel .document-technical,
            body.purchase-review-mode #document-action-summary,
            body.purchase-review-mode .purchase-validate-button,
            body.purchase-review-mode .purchase-validation-status {
                display: none !important;
            }
            body.purchase-review-mode #document-review-panel {
                display: block !important;
                width: 100% !important;
                max-width: none !important;
                margin: 0 !important;
                padding: 0 !important;
            }
            body.purchase-review-mode #document-review-panel .document-data-card {
                display: block !important;
                width: 100% !important;
                max-width: none !important;
                margin: 0 !important;
                padding: 0 !important;
                border: 0 !important;
                border-radius: 0 !important;
                background: transparent !important;
            }
            body.purchase-review-mode #document-review-panel .purchase-details {
                display: block !important;
                margin: 0 !important;
                padding: 0 !important;
                border: 1px solid #aeb2b7 !important;
                border-top: 3px solid #876a16 !important;
                border-radius: 4px !important;
                background: #fff !important;
                overflow: visible !important;
            }
            body.purchase-review-mode #document-review-panel .purchase-details > summary {
                display: none !important;
            }
            .madhushala-bill-form {
                display: block !important;
                padding: 0 !important;
                margin: 0 !important;
                border: 0 !important;
                background: #fff !important;
                font-family: Arial, Helvetica, sans-serif !important;
                color: #171717 !important;
            }
            .ms-bill-titlebar {
                display: flex;
                align-items: center;
                gap: 7px;
                height: 31px;
                padding: 3px 8px;
                border-bottom: 1px solid #c5c8cc;
                background: #fff;
                font-size: 12px;
                font-weight: 700;
            }
            .ms-bill-title-icon {
                display: grid;
                place-items: center;
                width: 20px;
                height: 20px;
                background: #ffc400;
                border: 1px solid #d89d00;
                font-size: 12px;
            }
            .ms-bill-titlebar strong {
                display: block;
                font-size: 13px;
                line-height: 1;
            }
            .ms-bill-titlebar small {
                display: block;
                margin-top: 1px;
                color: #111;
                font-family: "Space Mono", monospace;
                font-size: 6px;
                letter-spacing: .12em;
            }
            .ms-bill-modebar {
                display: flex;
                align-items: end;
                justify-content: space-between;
                min-height: 51px;
                padding: 5px 8px;
                border: 1px solid #c7cbd0;
                border-width: 0 0 1px;
                background: #f7f8f9;
            }
            .ms-bill-modegroups {
                display: flex;
                gap: 9px;
            }
            .ms-bill-modegroup {
                display: grid;
                gap: 4px;
            }
            .ms-bill-mode-label,
            .ms-bill-label,
            .ms-bill-field {
                color: #666;
                font-family: "Space Mono", monospace;
                font-size: 7px;
                font-weight: 700;
                letter-spacing: .04em;
                text-transform: uppercase;
            }
            .ms-bill-tabs {
                display: flex;
                gap: 0;
            }
            .ms-bill-tab {
                display: inline-flex;
                align-items: center;
                justify-content: center;
                height: 26px;
                min-width: 78px;
                padding: 0 11px;
                border: 1px solid #d5d8dc;
                background: #fff;
                color: #696969;
                font-family: Arial, Helvetica, sans-serif;
                font-size: 10px;
                font-weight: 600;
                text-transform: none;
            }
            .ms-bill-tab + .ms-bill-tab {
                border-left: 0;
            }
            .ms-bill-tab.active {
                border-color: #111;
                background: #111;
                color: #ffd328;
            }
            .ms-bill-modeactions {
                display: flex;
                gap: 8px;
            }
            .ms-bill-action-chip {
                display: inline-flex;
                align-items: center;
                justify-content: center;
                height: 31px;
                min-width: 116px;
                padding: 0 12px;
                border: 1px solid #d7d9dc;
                background: #fff;
                color: #555;
                font-family: "Space Mono", monospace;
                font-size: 7px;
                font-weight: 700;
                letter-spacing: .04em;
            }
            .ms-bill-master-grid {
                display: grid;
                grid-template-columns: 1.15fr 1.45fr .9fr 1.45fr;
                column-gap: 9px;
                row-gap: 5px;
                padding: 7px 8px 6px;
                border-bottom: 1px solid #aeb2b7;
                background: #fff;
            }
            .ms-bill-field {
                display: grid;
                grid-template-columns: 92px minmax(0, 1fr);
                align-items: center;
                gap: 5px;
                min-width: 0;
                text-transform: uppercase;
            }
            .ms-bill-field input,
            .ms-bill-field select {
                width: 100%;
                min-width: 0;
                height: 29px !important;
                padding: 0 9px !important;
                border: 1px solid #d6d9dd !important;
                border-radius: 6px !important;
                outline: none !important;
                background: #fff !important;
                color: #333 !important;
                font-family: Arial, Helvetica, sans-serif !important;
                font-size: 10px !important;
                font-weight: 500 !important;
                text-transform: none !important;
            }
            .ms-bill-field input:focus,
            .ms-bill-field select:focus {
                border-color: #ffbe00 !important;
                box-shadow: 0 0 0 1px #ffbe00 !important;
            }
            .ms-bill-static-value {
                display: flex;
                align-items: center;
                width: 100%;
                height: 29px;
                padding: 0 9px;
                border: 1px solid #d6d9dd;
                border-radius: 2px;
                background: #f7f2e6;
                color: #333;
                font-family: Arial, Helvetica, sans-serif;
                font-size: 10px;
                font-weight: 600;
                text-transform: none;
            }
            .ms-bill-static-field .ms-bill-label {
                display: block;
            }
            .ms-bill-items {
                min-height: 166px;
                border-bottom: 6px solid #eef0f2;
                background: #fff;
            }
            .ms-bill-section-title {
                display: flex;
                align-items: center;
                gap: 8px;
                height: 31px;
                padding: 0 10px;
                border-bottom: 1px solid #c6c9cc;
                background: #e9ecef;
                color: #111;
                font-family: Arial, Helvetica, sans-serif;
                font-size: 11px;
                font-weight: 700;
            }
            .ms-bill-section-title::before {
                content: "☷";
                color: #c99300;
                font-size: 12px;
            }
            .ms-bill-table-wrap {
                overflow: auto;
                max-height: 275px;
                min-height: 132px;
            }
            .ms-bill-table {
                width: 100%;
                min-width: 1040px;
                border-collapse: collapse;
                table-layout: auto;
                font-family: "Space Mono", monospace;
                font-size: 8px;
            }
            .ms-bill-table th {
                position: sticky;
                top: 0;
                z-index: 2;
                height: 31px;
                padding: 0 7px;
                border-right: 1px solid #151515;
                background: #050505;
                color: #fff;
                font-size: 7px;
                font-weight: 700;
                text-align: left;
                white-space: nowrap;
            }
            .ms-bill-table td {
                height: 34px;
                padding: 3px 7px;
                border-right: 1px solid #eef0f2;
                border-bottom: 1px solid #e2e4e6;
                color: #111;
                text-align: right;
                white-space: nowrap;
            }
            .ms-bill-table td:nth-child(1),
            .ms-bill-table td:nth-child(2),
            .ms-bill-table td:nth-child(3) {
                text-align: left;
            }
            .ms-bill-item-name {
                display: block;
                max-width: 265px;
                overflow: hidden;
                color: #333;
                font-family: Arial, Helvetica, sans-serif;
                font-size: 10px;
                font-weight: 600;
                text-overflow: ellipsis;
            }
            .ms-bill-item-code {
                display: block;
                margin-top: 1px;
                color: #8a8a8a;
                font-size: 7px;
            }
            .ms-bill-bottom-grid {
                display: grid;
                grid-template-columns: 1.1fr .85fr .95fr;
                min-height: 96px;
                border-bottom: 1px solid #c9ccd0;
                background: #fff;
            }
            .ms-bill-bottom-panel {
                padding: 6px 8px;
                border-right: 7px solid #eef0f2;
            }
            .ms-bill-bottom-panel:last-child {
                border-right: 0;
            }
            .ms-bill-bottom-title {
                margin-bottom: 7px;
                color: #111;
                font-family: "Space Mono", monospace;
                font-size: 7px;
                font-weight: 700;
                letter-spacing: .04em;
                text-transform: uppercase;
            }
            .ms-bill-scheme-host .ms-bill-field {
                display: block;
            }
            .ms-bill-scheme-host .ms-bill-field {
                color: transparent;
                font-size: 0;
            }
            .ms-bill-scheme-host .ms-bill-field select {
                height: 29px !important;
            }
            .ms-bill-metric-row {
                display: grid;
                grid-template-columns: minmax(0, 1fr) 98px;
                align-items: center;
                min-height: 22px;
                gap: 8px;
                color: #444;
                font-family: Arial, Helvetica, sans-serif;
                font-size: 10px;
            }
            .ms-bill-metric-value {
                height: 19px;
                padding: 2px 6px;
                border: 1px solid #d5d7da;
                background: #f7f2e6;
                font-family: "Space Mono", monospace;
                font-size: 9px;
                font-weight: 700;
                text-align: right;
            }
            .ms-bill-narration-host {
                min-height: 49px;
                padding: 5px 8px 6px;
                background: #fff;
            }
            .ms-bill-narration-host .ms-bill-field {
                display: block;
            }
            .ms-bill-narration-host .ms-bill-field {
                color: #666;
                font-size: 7px;
            }
            .ms-bill-narration-host .ms-bill-field input {
                width: 315px !important;
                max-width: 100%;
                margin-top: 4px;
                border-radius: 0 !important;
            }
            .ms-bill-hidden-fields {
                display: none !important;
            }
            body.purchase-review-mode #document-action-bar {
                display: flex !important;
                align-items: center !important;
                justify-content: flex-end !important;
                min-height: 45px !important;
                margin: 0 !important;
                padding: 5px 7px !important;
                border: 1px solid #c5c8cc !important;
                border-top: 0 !important;
                border-radius: 0 0 4px 4px !important;
                background: #fff !important;
            }
            body.purchase-review-mode #document-action-bar > div {
                display: flex !important;
                flex-direction: row !important;
                align-items: center !important;
                justify-content: flex-end !important;
                gap: 7px !important;
                width: auto !important;
            }
            body.purchase-review-mode #document-action-bar button {
                width: auto !important;
                min-width: 86px !important;
                height: 31px !important;
                padding: 0 12px !important;
                border-radius: 2px !important;
                font-family: "Space Mono", monospace !important;
                font-size: 7px !important;
                font-weight: 700 !important;
                letter-spacing: .03em !important;
            }
            body.purchase-review-mode #save-purchase {
                border-color: #9b9b9b !important;
                background: #9b9b9b !important;
                color: #fff !important;
            }
            body.purchase-review-mode #save-purchase:not(:disabled) {
                border-color: #111 !important;
                background: #111 !important;
            }
            body.purchase-review-mode #purchase-back-to-mapping {
                border: 1px solid #c8cbcf !important;
                background: #fff !important;
                color: #333 !important;
            }
            @media (max-width: 980px) {
                .ms-bill-master-grid {
                    grid-template-columns: 1fr 1fr;
                }
                .ms-bill-bottom-grid {
                    grid-template-columns: 1fr;
                }
                .ms-bill-bottom-panel {
                    border-right: 0;
                    border-bottom: 6px solid #eef0f2;
                }
            }
            @media (max-width: 640px) {
                .ms-bill-master-grid {
                    grid-template-columns: 1fr;
                }
                .ms-bill-field {
                    grid-template-columns: 88px minmax(0, 1fr);
                }
                .ms-bill-modebar {
                    align-items: stretch;
                    flex-direction: column;
                    gap: 6px;
                }
                .ms-bill-modegroups,
                .ms-bill-modeactions {
                    flex-wrap: wrap;
                }
            }
        `;
        document.head.appendChild(style);
    }

    function ensureMadhushalaPurchaseBill() {
        const details = document.querySelector("#document-review-panel .purchase-details");
        const form = document.getElementById("purchase-form");
        if (!details || !form) return null;

        ensureSchemeFieldForBill();

        if (form.dataset.madhushalaBillLayout !== "true") {
            const fieldIds = [
                "purchase-year-code",
                "purchase-trn-date",
                "purchase-doc-date",
                "purchase-doc-no",
                "purchase-tp-pass-no",
                "purchase-supplier-code",
                "purchase-store-code",
                "purchase-scheme-code",
                "purchase-acc-code",
                "purchase-user-code",
                "purchase-tax-mode",
                "purchase-narration",
            ];
            const labels = Object.fromEntries(
                fieldIds.map((id) => [id, document.getElementById(id)?.closest("label") || null]),
            );

            const titlebar = document.createElement("div");
            titlebar.className = "ms-bill-titlebar";
            titlebar.innerHTML = `
                <span class="ms-bill-title-icon">▣</span>
                <div><strong>Purchase Bill</strong><small>NEW BILL</small></div>
            `;

            const modebar = document.createElement("div");
            modebar.className = "ms-bill-modebar";
            modebar.innerHTML = `
                <div class="ms-bill-modegroups">
                    <div class="ms-bill-modegroup">
                        <span class="ms-bill-mode-label">Operation Type</span>
                        <div class="ms-bill-tabs">
                            <span class="ms-bill-tab active">Purchase</span>
                            <span class="ms-bill-tab">Return</span>
                            <span class="ms-bill-tab">Purchase Order</span>
                        </div>
                    </div>
                    <div class="ms-bill-modegroup">
                        <span class="ms-bill-mode-label">Item Type <b style="color:#d62d20">*</b></span>
                        <div class="ms-bill-tabs">
                            <span class="ms-bill-tab active">AI Item</span>
                            <span class="ms-bill-tab">NAI Item</span>
                        </div>
                    </div>
                </div>
                <div class="ms-bill-modeactions">
                    <span class="ms-bill-action-chip">▧ ADD NEW ITEM</span>
                    <span class="ms-bill-action-chip">⌁ ADD NEW SUPPLIER</span>
                </div>
            `;

            const master = document.createElement("div");
            master.id = "ms-bill-master-grid";
            master.className = "ms-bill-master-grid";
            master.append(
                staticBillField("ms-bill-retail", "Retail", "-"),
                staticBillField("ms-bill-company", "Company", "-"),
                staticBillField("ms-bill-trn-no", "TRN No", "Auto"),
            );

            const supplier = labels["purchase-supplier-code"];
            const trnDate = labels["purchase-trn-date"];
            const docDate = labels["purchase-doc-date"];
            const docNo = labels["purchase-doc-no"];
            const tpPass = labels["purchase-tp-pass-no"];
            const purchaseAcc = labels["purchase-acc-code"];
            const storage = labels["purchase-store-code"];
            setBillFieldLabel(supplier, "Supplier *");
            setBillFieldLabel(trnDate, "Trans. Date *");
            setBillFieldLabel(docDate, "Doc./Bill Date");
            setBillFieldLabel(docNo, "Doc./Bill No");
            setBillFieldLabel(tpPass, "T.P.Pass No");
            setBillFieldLabel(purchaseAcc, "Purchase Head *");
            setBillFieldLabel(storage, "Storage *");
            [supplier, trnDate, docDate, docNo, tpPass, purchaseAcc, storage].filter(Boolean).forEach((label) => master.appendChild(label));

            const items = document.createElement("section");
            items.id = "purchase-final-preview";
            items.className = "ms-bill-items";
            items.innerHTML = `
                <div class="ms-bill-section-title">Items</div>
                <div class="ms-bill-table-wrap">
                    <table class="ms-bill-table">
                        <thead>
                            <tr>
                                <th>SL.NO</th><th>ITEM NAME</th><th>BATCH NO</th><th>CASE</th><th>LOOSE</th>
                                <th>QUANTITY</th><th>CASE RATE</th><th>MRP</th><th>LOOSE RATE</th>
                                <th>DISCOUNT</th><th>AMOUNT</th>
                            </tr>
                        </thead>
                        <tbody id="purchase-final-table-body">
                            <tr><td colspan="11">Calculating purchase preview…</td></tr>
                        </tbody>
                    </table>
                </div>
            `;

            const bottom = document.createElement("div");
            bottom.className = "ms-bill-bottom-grid";

            const schemePanel = document.createElement("section");
            schemePanel.className = "ms-bill-bottom-panel";
            schemePanel.innerHTML = '<div class="ms-bill-bottom-title">Tax Scheme</div><div id="ms-bill-scheme-host" class="ms-bill-scheme-host"></div>';

            const taxPanel = document.createElement("section");
            taxPanel.className = "ms-bill-bottom-panel";
            taxPanel.innerHTML = `
                <div class="ms-bill-bottom-title">Tax N Other</div>
                <div class="ms-bill-metric-row"><span>ROUND OFF</span><span id="ms-bill-round-off" class="ms-bill-metric-value">0.00</span></div>
                <div class="ms-bill-metric-row"><span>SPECIAL PURPOSE FEE</span><span id="ms-bill-special-fee" class="ms-bill-metric-value">0.00</span></div>
                <div class="ms-bill-metric-row"><span>TCS</span><span id="ms-bill-tcs" class="ms-bill-metric-value">0.00</span></div>
            `;

            const totalPanel = document.createElement("section");
            totalPanel.className = "ms-bill-bottom-panel";
            totalPanel.innerHTML = `
                <div class="ms-bill-bottom-title">Total</div>
                <div class="ms-bill-metric-row"><span>Gross Amount</span><span id="purchase-final-gross" class="ms-bill-metric-value">0.00</span></div>
                <div class="ms-bill-metric-row"><span>Total Discount</span><span id="ms-bill-total-discount" class="ms-bill-metric-value">0.00</span></div>
                <div class="ms-bill-metric-row"><span>ETD</span><span id="ms-bill-etd" class="ms-bill-metric-value">0.00</span></div>
                <div class="ms-bill-metric-row"><span>Sales Tax on MRP</span><span id="ms-bill-sales-tax" class="ms-bill-metric-value">0.00</span></div>
                <div class="ms-bill-metric-row"><span>Tax Amount</span><span id="purchase-final-tax" class="ms-bill-metric-value">0.00</span></div>
                <div class="ms-bill-metric-row"><span>Net Amount</span><span id="purchase-final-net" class="ms-bill-metric-value">0.00</span></div>
            `;
            bottom.append(schemePanel, taxPanel, totalPanel);

            const narrationHost = document.createElement("div");
            narrationHost.id = "ms-bill-narration-host";
            narrationHost.className = "ms-bill-narration-host";

            const hiddenHost = document.createElement("div");
            hiddenHost.className = "ms-bill-hidden-fields";

            const scheme = labels["purchase-scheme-code"];
            const narration = labels["purchase-narration"];
            const year = labels["purchase-year-code"];
            const user = labels["purchase-user-code"];
            const taxMode = labels["purchase-tax-mode"];
            setBillFieldLabel(scheme, "Tax Scheme");
            setBillFieldLabel(narration, "Narration");
            if (scheme) schemePanel.querySelector("#ms-bill-scheme-host")?.appendChild(scheme);
            if (narration) narrationHost.appendChild(narration);
            [year, user, taxMode].filter(Boolean).forEach((label) => hiddenHost.appendChild(label));

            form.replaceChildren(titlebar, modebar, master, items, bottom, narrationHost, hiddenHost);
            form.classList.add("madhushala-bill-form");
            form.dataset.madhushalaBillLayout = "true";
        }

        const context = window.__purchaseContext?.getContext?.() || null;
        const retail = document.querySelector("#ms-bill-retail .ms-bill-static-value");
        const company = document.querySelector("#ms-bill-company .ms-bill-static-value");
        if (retail) retail.textContent = context?.shopCode || retail.textContent || "-";
        if (company) company.textContent = context?.jwtContext?.companyName || context?.companyCode || company.textContent || "-";

        return form;
    }

    function ensureFinalPurchasePreview() {
        ensureMadhushalaPurchaseBill();
        return document.getElementById("purchase-final-preview");
    }

    function renderFinalPurchasePayload(response) {
        if (!purchaseReviewMode) return;
        const payload = response?.purchasePayload || {};
        const items = Array.isArray(payload.items) ? payload.items : [];
        ensureMadhushalaPurchaseBill();

        const context = window.__purchaseContext?.getContext?.() || null;
        const retail = document.querySelector("#ms-bill-retail .ms-bill-static-value");
        const company = document.querySelector("#ms-bill-company .ms-bill-static-value");
        if (retail) retail.textContent = payload.shopCode || context?.shopCode || "-";
        if (company) company.textContent = context?.jwtContext?.companyName || payload.companyCode || context?.companyCode || "-";

        const tbody = document.getElementById("purchase-final-table-body");
        if (tbody) {
            tbody.innerHTML = items.length
                ? items.map((item, index) => `
                    <tr>
                        <td>${index + 1}</td>
                        <td>
                            <span class="ms-bill-item-name">${escapeHtml(item.itemName ?? "")}</span>
                            <span class="ms-bill-item-code">${escapeHtml(item.itemCode ?? "")}</span>
                        </td>
                        <td>${escapeHtml(item.batchNo ?? "")}</td>
                        <td>${escapeHtml(item.box ?? 0)}</td>
                        <td>${escapeHtml(item.loose ?? 0)}</td>
                        <td>${escapeHtml(item.qnty ?? 0)}</td>
                        <td>${escapeHtml(item.boxRate ?? item.rate ?? 0)}</td>
                        <td>${escapeHtml(item.mrp ?? 0)}</td>
                        <td>${escapeHtml(item.looseRate ?? 0)}</td>
                        <td>${escapeHtml(item.discount ?? 0)}</td>
                        <td>${escapeHtml(item.itemAmount ?? 0)}</td>
                    </tr>
                `).join("")
                : '<tr><td colspan="11">No purchase items returned by Calculate Preview.</td></tr>';
        }

        const etd = items.reduce((sum, item) => sum + (Number(item.etd) || 0), 0);
        const totalDiscount = payload.discount ?? items.reduce((sum, item) => sum + (Number(item.discount) || 0), 0);
        setText(document.getElementById("purchase-final-gross"), formatPurchaseMoney(payload.grossAmount));
        setText(document.getElementById("purchase-final-tax"), formatPurchaseMoney(payload.taxAmount));
        setText(document.getElementById("purchase-final-net"), formatPurchaseMoney(payload.netAmount));
        setText(document.getElementById("ms-bill-total-discount"), formatPurchaseMoney(totalDiscount));
        setText(document.getElementById("ms-bill-etd"), formatPurchaseMoney(payload.etd ?? etd));
        setText(document.getElementById("ms-bill-sales-tax"), formatPurchaseMoney(payload.salesTaxOnMRP));
        setText(document.getElementById("ms-bill-round-off"), formatPurchaseMoney(payload.roundOff));
        setText(document.getElementById("ms-bill-special-fee"), formatPurchaseMoney(payload.specialPurposeFee ?? payload.specialPurposeFeeAmount));
        setText(document.getElementById("ms-bill-tcs"), formatPurchaseMoney(payload.tcs ?? payload.tcsAmount));

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

        setHidden(document.getElementById("document-refresh"), true);
        setHidden(document.getElementById("document-upload-another"), true);
        setHidden(document.getElementById("continue-document-mapping"), true);
        setHidden(document.getElementById("save-purchase"), false);

        const details = document.querySelector("#document-review-panel .purchase-details");
        if (details) details.open = true;
        ensureMadhushalaPurchaseBill();

        const save = document.getElementById("save-purchase");
        if (save) save.textContent = "SAVE PURCHASE";

        const actionButtons = document.querySelector("#document-action-bar > div");
        if (actionButtons && !document.getElementById("purchase-back-to-mapping")) {
            const back = document.createElement("button");
            back.id = "purchase-back-to-mapping";
            back.type = "button";
            back.className = "secondary";
            back.textContent = "CANCEL";
            back.addEventListener("click", () => {
                persistPurchaseHeader();
                window.location.href = basePath
                    + "/?view=mapping&jobId=" + encodeURIComponent(jobId)
                    + "&sessionId=" + encodeURIComponent(sessionId)
                    + "#session=" + encodeURIComponent(sessionToken);
            });
            actionButtons.appendChild(back);
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
                ensureMadhushalaPurchaseBill();
                await window.__purchaseContext?.validate?.("review", {showSuccessToast: false});
            } catch (error) {
                showToast(error?.message || "Could not calculate purchase preview.", "error");
            }
        };
        window.setTimeout(() => void validateWhenReady(), 0);
    }

    const originalInitMapping = initMapping;
    initMapping = function initMappingWithPurchaseDetails() {
        // Do not mount Purchase fields on Mapping in the simplified flow.
        // The function is retained above so the old UI can be restored later.
        ensureCompactMappingStyles();
        setupMappingNextButton();
        setupMappingManagementControls();
        const result = originalInitMapping();
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
        continueMappingToPurchase,
        renderFinalPurchasePayload,
    };
})();
