from pathlib import Path


def test_mapping_keeps_purchase_details_visible_and_persisted():
    script = Path("app/static/mapping-row-identity.js").read_text(encoding="utf-8")

    assert 'id = "mapping-purchase-details"' in script
    assert 'host.appendChild(form)' in script
    assert 'applyPurchaseHeader(loadPurchaseHeader(currentDocumentJobId))' in script
    assert 'persistPurchaseHeader();' in script
    assert '/static/purchase-context.js' in script


def test_saved_document_mapping_is_presented_as_mapped_and_remappable():
    script = Path("app/static/mapping-row-identity.js").read_text(encoding="utf-8")

    assert '<span class="eyebrow">Mapped</span>' in script
    assert 'saved Madhushala item for the extracted purchase row' in script
    assert 'Already mapped. Use Change / Re-map only if the saved item is wrong.' in script
    assert 'Extracted: ${rows.length} | Mapped: ${mapped} | Unmapped: ${left}' in script
    assert 'Change / Re-map' in script


def test_fully_mapped_qr_still_exposes_mapping_review():
    script = Path("app/static/index.html").read_text(encoding="utf-8")

    assert 'await continueExtractedDocumentToMapping(payload, {qr: true});' in script
    assert 'window.location.href = basePath' in script
    assert '"/?view=mapping&jobId="' in script


def test_document_mapping_search_is_not_overwritten_by_background_refresh():
    script = Path("app/static/mapping-row-identity.js").read_text(encoding="utf-8")

    assert 'const liveSearch = preserveState ? (search?.value || "") : "";' in script
    assert 'if (sanitizeJobId(currentDocumentJobId)) return;' in script
    assert 'document.activeElement === search' in script


def test_purchase_context_runs_on_mapping_page_and_restores_header():
    script = Path("app/static/purchase-context.js").read_text(encoding="utf-8")

    assert 'pageParams.get("view") === "mapping"' in script
    assert 'restoreMappingHeader()' in script
    assert 'window.applyPurchaseHeader(window.loadPurchaseHeader(jobId))' in script
    assert 'window.__purchaseContext' in script


def test_mapping_frontend_preserves_structured_auth_error_code():
    script = Path("app/static/app.js").read_text(encoding="utf-8")

    assert 'error.code = detail.code;' in script
    assert 'MADHUSHALA_AUTH_EXPIRED' in script
    assert 'Madhushala login expired. Please reopen Excise Import from Madhushala CRM' in script


def test_review_confirmation_sends_batch_number():
    script = Path("app/static/index.html").read_text(encoding="utf-8")

    assert 'batchNo: String(item.batchNo || "").trim(),' in script


def test_valid_document_import_skips_review_and_auto_confirms_to_mapping():
    script = Path("app/static/index.html").read_text(encoding="utf-8")

    assert "continueExtractedDocumentToMapping" in script
    assert "/review/confirm" in script
    assert 'setDocumentImportState("checking")' in script
    assert "Preparing item mapping" in script
    assert '"/?view=mapping&jobId="' in script

    upload = script.split("async function uploadDocuments", 1)[1].split(
        "async function uploadDocument", 1
    )[0]
    assert "await continueExtractedDocumentToMapping(payload);" in upload
    assert 'setDocumentImportState("review")' not in upload
    assert "renderDocumentReview(payload)" not in upload

    qr = script.split("async function extractQrUrl", 1)[1].split(
        "function renderQrReview", 1
    )[0]
    assert "await continueExtractedDocumentToMapping(payload, {qr: true});" in qr
    assert "renderQrReview(payload)" not in qr


def test_mapping_normal_flow_uses_next_purchase_and_keeps_legacy_save_hidden():
    script = Path("app/static/mapping-row-identity.js").read_text(encoding="utf-8")

    assert 'next.id = "mapping-next-purchase"' in script
    assert 'next.textContent = "Next: Preview Purchase"' in script
    assert "/document-import?view=purchase&jobId=" in script
    assert 'legacySave.style.display = "none"' in script
    assert "Do not mount Purchase fields on Mapping in the simplified flow." in script



def test_final_purchase_bill_mimics_madhushala_and_hides_manual_validate():
    mapping_script = Path("app/static/mapping-row-identity.js").read_text(encoding="utf-8")
    context_script = Path("app/static/purchase-context.js").read_text(encoding="utf-8")

    assert "Purchase Bill" in mapping_script
    assert "Operation Type" in mapping_script
    assert "AI Item" in mapping_script
    assert "T.P.Pass No" in mapping_script
    assert "Purchase Head" in mapping_script
    assert "Tax N Other" in mapping_script
    assert "SAVE PURCHASE" in mapping_script
    assert 'back.textContent = "CANCEL"' in mapping_script
    assert "ms-bill-table" in mapping_script
    assert "ms-bill-total-discount" in mapping_script
    assert "ensureCompactMappingStyles" in mapping_script

    assert 'purchaseReviewMode && source === "review"' in context_script
    assert "Do not render a separate Validate button." in context_script
    assert "schedulePurchasePreviewRefresh" in context_script
    assert "getContext: () => latestContext" in context_script


def test_purchase_bill_save_still_revalidates_before_backend_save():
    context_script = Path("app/static/purchase-context.js").read_text(encoding="utf-8")

    assert "const validated = await validatePurchase(source, {showSuccessToast: false});" in context_script
    assert "if (!validated) return;" in context_script
    assert "return originalSave.call(this, source);" in context_script

def test_purchase_review_renders_server_validated_purchase_payload():
    mapping_script = Path("app/static/mapping-row-identity.js").read_text(encoding="utf-8")
    context_script = Path("app/static/purchase-context.js").read_text(encoding="utf-8")

    assert 'const purchaseReviewMode = pageParams.get("view") === "purchase"' in mapping_script
    assert 'items.id = "purchase-final-preview"' in mapping_script
    assert "renderFinalPurchasePayload" in mapping_script
    assert '"purchase-preview-ready"' in mapping_script

    assert 'pageParams.get("view") === "purchase" ? purchase : payload' in context_script
    assert 'new CustomEvent("purchase-preview-ready", {detail: payload})' in context_script


def test_successful_import_never_renders_legacy_preview_before_mapping():
    runtime = Path("app/static/index.html").read_text(encoding="utf-8")
    enhancer = Path("app/static/mapping-row-identity.js").read_text(encoding="utf-8")

    assert "showDocumentReviewFallback" in runtime
    assert "if (!items.length || invalid.length)" in runtime
    assert "renderDocumentReviewWithDirectMapping" not in enhancer
    assert "setDocumentImportStateWithoutSuccessfulPreview" not in enhancer
    assert "autoConfirmExtractedReview" not in enhancer


def test_frontend_static_assets_are_cache_busted():
    main = Path("app/main.py").read_text(encoding="utf-8")

    assert 'STATIC_ASSET_VERSION = "20260921-mapping-management-v7"' in main
    assert 'mapping-row-identity.js?v={STATIC_ASSET_VERSION}' in main
    assert 'purchase-context.js?v={STATIC_ASSET_VERSION}' in main


def test_portal_capture_auto_hands_launch_page_to_mapping():
    runtime = Path("app/static/index.html").read_text(encoding="utf-8")

    assert "function startPortalMappingWatch()" in runtime
    assert "function checkPortalMappingHandoff" in runtime
    assert 'session?.state === "mapping_required"' in runtime
    assert "window.location.replace(portalMappingUrl())" in runtime
    assert "Mapping will open here automatically after capture." in runtime


def test_portal_mapping_is_mapping_only_without_purchase_controls():
    mapping_script = Path("app/static/mapping-row-identity.js").read_text(encoding="utf-8")
    context_script = Path("app/static/purchase-context.js").read_text(encoding="utf-8")

    assert 'if (!mappingMode || !sanitizeJobId(currentDocumentJobId || activeJobId || "")) return;' in mapping_script
    assert 'if (!mappingMode || !sanitizeJobId(currentDocumentJobId)) return;' in mapping_script
    assert 'next.textContent = "Next: Preview Purchase"' in mapping_script
    assert 'const isDocumentMappingView = isMappingView && Boolean(clean(pageParams.get("jobId")));' in context_script
    assert 'if (!isDocumentImport && !isDocumentMappingView) return;' in context_script


def test_extension_focuses_mapping_workspace_after_portal_capture():
    background = Path("extension/background.js").read_text(encoding="utf-8")
    manifest = Path("extension/manifest.json").read_text(encoding="utf-8")

    assert "async function focusMappingWorkspace(settings)" in background
    assert "result?.mappingStatus?.mappingRequired" in background
    assert "result.mappingNavigation = await focusMappingWorkspace(settings)" in background
    assert "https://integrations.madhushalasoftware.com/*" in manifest


def test_mapping_management_entrypoint_and_filters_are_available():
    runtime = Path("app/static/index.html").read_text(encoding="utf-8")
    enhancer = Path("app/static/mapping-row-identity.js").read_text(encoding="utf-8")
    service = Path("app/services/mapping_service.py").read_text(encoding="utf-8")
    main = Path("app/main.py").read_text(encoding="utf-8")

    assert 'id="manage-mappings"' in runtime
    assert 'view=mapping&manage=1' in runtime
    assert 'id="mapping-management-toolbar"' in runtime
    assert 'data-mapping-filter="all"' in runtime
    assert 'data-mapping-filter="unmapped"' in runtime
    assert 'data-mapping-filter="mapped"' in runtime

    assert 'mappingManagementMode ? "?latestOnly=false&includeMapped=true"' in runtime
    assert 'mappingManagementMode ? "?latestOnly=false&includeMapped=true"' in enhancer
    assert 'function filteredManagementRows()' in enhancer
    assert 'Change / Re-map' in enhancer

    assert 'include_mapped: bool = False' in service
    assert 'set(remote_by_code) | set(imported_by_code) | set(mapped_by_code)' in service
    assert '"mappingStatus": "MAPPED" if mapped_code else "UNMAPPED"' in service
    assert 'includeMapped: bool = False' in main
