from pathlib import Path


def test_mapping_keeps_purchase_details_visible_and_persisted():
    script = Path("app/static/mapping-row-identity.js").read_text(encoding="utf-8")

    assert 'id = "mapping-purchase-details"' in script
    assert 'host.appendChild(form)' in script
    assert 'applyPurchaseHeader(loadPurchaseHeader(currentDocumentJobId))' in script
    assert 'persistPurchaseHeader();' in script
    assert '/static/purchase-context.js' in script


def test_saved_document_mapping_is_presented_as_current_mapping():
    script = Path("app/static/mapping-row-identity.js").read_text(encoding="utf-8")

    assert 'Current Mapping' in script
    assert 'Already saved for this extracted row' in script
    assert 'This row is already mapped. Use Change Mapping only if you need to replace it.' in script
    assert 'Mapped: ${mapped}/${rows.length} | Left: ${left}' in script


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
