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
    script = Path("app/static/mapping-row-identity.js").read_text(encoding="utf-8")

    assert 'Review QR Item Mappings' in script
    assert 'button.hidden = false' in script
    assert 'Review/change mappings if needed' in script
    assert 'originalRenderQrReview' in script
    assert 'keepDocumentMappingReviewAvailable(payload, {qr: true})' in script


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
    script = Path("app/static/app.js").read_text(encoding="utf-8")

    assert 'batchNo: String(item.batchNo || "").trim(),' in script
