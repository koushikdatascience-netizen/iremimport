let lastSeenBatchId = null;
let lastMappingKey = null;
let workspace = {unmappedItems: [], madhushalaItems: []};
let selectedExciseCode = null;
const selectedMappings = new Map();
let appConfig = {tokenConfigured: false, exciseCredentialsConfigured: false};
let workspaceMode = 'latest';
let pendingGuardrailAction = null;
const basePath = window.location.pathname.startsWith('/excise-import/') ? '/excise-import' : '';
const pageParams = new URLSearchParams(window.location.search);
const mappingOnlyMode = pageParams.get('view') === 'mapping';

async function api(path, options = {}) {
    const response = await fetch(`${basePath}${path}`, {
        ...options,
        headers: {
            'Content-Type': 'application/json',
            ...(options.headers || {})
        }
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
        const detail = data?.detail || data?.error || `Request failed (HTTP ${response.status})`;
        throw new Error(detail);
    }
    return data;
}

async function pollStatus() {
    try {
        const status = await api('/automation/status');
        updateStatusUI(status);
    } catch (error) {
        showNotification('Could not reach local server', 'error');
    } finally {
        setTimeout(pollStatus, 1500);
    }
}

async function loadApiStatus() {
    try {
        const status = await api('/madhushala/status');
        appConfig = status;
        setText(
            'api-token-pill',
            status.tokenConfigured ? 'API Ready' : 'API Missing',
            status.tokenConfigured ? 'status-pill ready' : 'status-pill'
        );
        const username = document.getElementById('username');
        const password = document.getElementById('password');
        if (status.exciseCredentialsConfigured) {
            if (username) username.placeholder = 'Saved';
            if (password) password.placeholder = 'Saved';
        }
    } catch (error) {
        setText('api-token-pill', 'API Unknown', 'status-pill');
    }
}

function setText(id, value, className) {
    const element = document.getElementById(id);
    if (!element) return;
    element.textContent = value;
    element.className = className || '';
}

function updateStatusUI(status) {
    setText(
        'browser-status',
        status.browserRunning ? 'Browser: Open' : 'Browser: Closed',
        status.browserRunning ? 'status-active' : 'status-waiting'
    );

    const loginText = status.loginPageDetected
        ? 'Login: CAPTCHA'
        : status.browserRunning
            ? 'Login: Done'
            : 'Login: Waiting';
    setText('login-status', loginText, status.browserRunning ? 'status-active' : 'status-waiting');

    setText(
        'prepare-indent-status',
        status.prepareIndentDetected ? 'Prepare: Ready' : 'Prepare: Waiting',
        status.prepareIndentDetected ? 'status-active' : 'status-waiting'
    );

    const batch = status.lastCapturedBatch;
    setText(
        'capture-status',
        batch ? `Captured: ${batch.itemCount}` : 'Captured: 0',
        batch ? 'status-active' : 'status-waiting'
    );

    const errorEl = document.getElementById('last-error');
    if (errorEl) {
        errorEl.hidden = !status.lastError;
        errorEl.textContent = status.lastError || '';
    }

    if (batch && batch.batchId !== lastSeenBatchId) {
        lastSeenBatchId = batch.batchId;
        showNotification(`Captured ${batch.itemCount} items`, 'success');
    }

    updateMappingAlert(status.mappingStatus);
    updateInstructions(status);
}

async function updateMappingAlert(mappingStatus) {
    const alert = document.getElementById('mapping-alert');
    if (!alert || !mappingStatus) return;

    alert.className = `mapping-alert ${mappingStatus.state || 'idle'}`;
    alert.textContent = mappingStatusLabel(mappingStatus);

    const mappingKey = `${mappingStatus.state || 'idle'}|${mappingStatus.updatedAt || ''}`;
    if (
        mappingStatus.mappingRequired &&
        mappingKey !== lastMappingKey
    ) {
        lastMappingKey = mappingKey;
        await refreshWorkspace(false);
        openMappingModal();
        showNotification('Match items', 'info');
    } else if (mappingKey !== lastMappingKey) {
        lastMappingKey = mappingKey;
    }
}

function mappingStatusLabel(mappingStatus) {
    const state = mappingStatus?.state || 'idle';
    const count = Number(mappingStatus?.unmappedCount || 0);
    const labels = {
        idle: 'Waiting',
        needs_token: 'Add API Token',
        processing: 'Checking...',
        mapping_required: count === 1 ? '1 item needs match' : `${count} items need match`,
        complete: 'All items matched',
        error: 'Could not check'
    };
    return labels[state] || 'Waiting';
}

function updateInstructions(status) {
    const instructionEl = document.getElementById('instruction-message');
    if (!instructionEl) return;

    if (!status.browserRunning) {
        instructionEl.textContent = 'Click Open Portal.';
    } else if (status.loginPageDetected) {
        instructionEl.textContent = 'Enter CAPTCHA, then Login.';
    } else if (status.prepareIndentDetected) {
        instructionEl.textContent = 'Type case quantity, then Capture Selected.';
    } else {
        instructionEl.textContent = 'Open Prepare Indent.';
    }
}

function showNotification(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3500);
}

function itemLabel(item) {
    return `${item.itemCode} - ${item.itemName}`;
}

function parseNumber(value) {
    const match = String(value || '').match(/\d+/);
    return match ? Number(match[0]) : 0;
}

function exciseMl(item) {
    const captured = item?.capturedItem || {};
    return parseNumber(captured.measureMl || item?.measureMl || item?.itemName);
}

function excisePack(item) {
    const captured = item?.capturedItem || {};
    return parseNumber(captured.bottlesPerCase || item?.bottlesPerCase);
}

function madhushalaMl(item) {
    return parseNumber(item?.ml || item?.itemName);
}

function madhushalaPack(item) {
    return parseNumber(item?.packing);
}

function findMadhushalaItem(itemCode) {
    return workspace.madhushalaItems.find((item) => String(item.itemCode) === String(itemCode));
}

function guardrailIssues(exciseItem, madhushalaItem, score = null) {
    const issues = [];
    const leftMl = exciseMl(exciseItem);
    const rightMl = madhushalaMl(madhushalaItem);
    const leftPack = excisePack(exciseItem);
    const rightPack = madhushalaPack(madhushalaItem);

    if (leftMl && rightMl && leftMl !== rightMl) {
        issues.push(`ML mismatch: Excise ${leftMl} ML, Madhushala ${rightMl} ML`);
    }
    if (leftPack && rightPack && leftPack !== rightPack) {
        issues.push(`Pack mismatch: Excise ${leftPack}, Madhushala ${rightPack}`);
    }
    if (score !== null && Number(score) > 0 && Number(score) < 55) {
        issues.push(`Low match confidence: ${Math.round(score)}%`);
    }

    return issues;
}

function duplicateMappingIssue(itemCode, currentExciseCode = selectedExciseCode) {
    const duplicate = Array.from(selectedMappings.entries()).find(([exciseCode, mappedCode]) => (
        String(exciseCode) !== String(currentExciseCode) && String(mappedCode) === String(itemCode)
    ));
    if (!duplicate) return null;
    const duplicateItem = workspace.unmappedItems.find((item) => String(item.exciseItemCode) === String(duplicate[0]));
    return `Same Madhushala item already selected for ${duplicateItem?.itemName || `Excise ${duplicate[0]}`}`;
}

function openMappingModal() {
    const modal = document.getElementById('mapping-modal');
    if (!modal) return;
    modal.hidden = false;
    document.body.classList.add('modal-open');
}

function closeMappingModal() {
    const modal = document.getElementById('mapping-modal');
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove('modal-open');
}

function showGuardrailModal(issues, onConfirm) {
    const modal = document.getElementById('guardrail-modal');
    const body = document.getElementById('guardrail-body');
    if (!modal || !body) return;

    pendingGuardrailAction = onConfirm;
    body.innerHTML = `
        <p>This match looks risky. Please check before saving.</p>
        <ul>${issues.map((issue) => `<li>${issue}</li>`).join('')}</ul>
    `;
    modal.hidden = false;
    document.body.classList.add('modal-open');
}

function closeGuardrailModal() {
    const modal = document.getElementById('guardrail-modal');
    if (!modal) return;
    modal.hidden = true;
    pendingGuardrailAction = null;
    if (document.getElementById('mapping-modal')?.hidden !== false) {
        document.body.classList.remove('modal-open');
    }
}

function normalizeSearchText(value) {
    return String(value || '')
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();
}

function itemInitials(itemName) {
    return normalizeSearchText(itemName)
        .split(' ')
        .filter(Boolean)
        .map((word) => word[0])
        .join('');
}

function searchTokens(query) {
    return normalizeSearchText(query).split(' ').filter(Boolean);
}

function scoreMadhushalaSearch(item, query) {
    const cleanQuery = normalizeSearchText(query);
    if (!cleanQuery) return 0;

    const name = normalizeSearchText(item.itemName);
    const code = normalizeSearchText(item.itemCode);
    const barcode = normalizeSearchText(item.barcode);
    const barcode2 = normalizeSearchText(item.barcode2);
    const barcode3 = normalizeSearchText(item.barcode3);
    const shortCode = normalizeSearchText(item.shortCode);
    const initials = itemInitials(item.itemName);
    const compactName = name.replace(/\s/g, '');
    const compactQuery = cleanQuery.replace(/\s/g, '');
    const tokens = searchTokens(query);
    const words = name.split(' ').filter(Boolean);
    const isShortQuery = compactQuery.length <= 1;
    const allTokensMatch = tokens.every((token) => (
        words.some((word) => word.startsWith(token) || (token.length >= 3 && word.includes(token)))
    ));

    let score = 0;
    let directMatch = false;

    if (code === cleanQuery) {
        score += 120;
        directMatch = true;
    }
    if (code.startsWith(cleanQuery)) {
        score += 80;
        directMatch = true;
    }
    if (shortCode && shortCode.startsWith(cleanQuery)) {
        score += 75;
        directMatch = true;
    }
    if (barcode && barcode.startsWith(cleanQuery)) {
        score += 70;
        directMatch = true;
    }
    if (barcode2 && barcode2.startsWith(cleanQuery)) {
        score += 65;
        directMatch = true;
    }
    if (barcode3 && barcode3.startsWith(cleanQuery)) {
        score += 65;
        directMatch = true;
    }
    if (name.startsWith(cleanQuery)) {
        score += 100;
        directMatch = true;
    }
    if (compactName.startsWith(compactQuery)) {
        score += 85;
        directMatch = true;
    }
    if (initials.startsWith(compactQuery)) {
        score += 90;
        directMatch = true;
    }
    if (!isShortQuery && cleanQuery.length >= 3 && name.includes(cleanQuery)) {
        score += 45;
        directMatch = true;
    }

    if (!directMatch && !allTokensMatch) return 0;

    for (const token of tokens) {
        if (words.some((word) => word.startsWith(token))) score += 25;
        else if (token.length >= 3 && words.some((word) => word.includes(token))) score += 12;
    }

    if (String(item.ml || '') === cleanQuery) score += 10;
    return score;
}

function renderWorkspace() {
    const list = document.getElementById('unmapped-items');
    setText('unmapped-count', String(workspace.unmappedItems.length));
    updateWorkspaceModeButtons();

    if (!list) return;
    if (!workspace.unmappedItems.length) {
        list.className = 'list-body empty';
        list.textContent = 'No items';
        renderSelectedExcise(null);
        return;
    }

    list.className = 'list-body';
    list.innerHTML = workspace.unmappedItems.map((item) => {
        const code = String(item.exciseItemCode);
        const mapped = selectedMappings.get(code) || item.selectedItemCode;
        return `
            <button class="unmapped-item ${code === String(selectedExciseCode) ? 'selected' : ''}" data-excise="${code}" type="button">
                <span class="item-code">${code}</span>
                <span class="item-name">${item.itemName}</span>
                <span class="${mapped ? 'map-badge done' : 'map-badge'}">${mapped ? 'Selected' : 'Pending'}</span>
            </button>
        `;
    }).join('');

    list.querySelectorAll('[data-excise]').forEach((button) => {
        button.addEventListener('click', () => {
            selectedExciseCode = button.dataset.excise;
            const searchInput = document.getElementById('madhushala-search');
            if (searchInput) searchInput.value = '';
            renderWorkspace();
            renderSelectedExcise(currentExciseItem());
        });
    });

    if (!selectedExciseCode && workspace.unmappedItems.length) {
        selectedExciseCode = String(workspace.unmappedItems[0].exciseItemCode);
        renderWorkspace();
        return;
    }

    renderSelectedExcise(currentExciseItem());
    updateMappingSummary();
}

function currentExciseItem() {
    return workspace.unmappedItems.find((item) => String(item.exciseItemCode) === String(selectedExciseCode));
}

function renderSelectedExcise(item) {
    const panel = document.getElementById('selected-excise');
    if (!panel) return;

    if (!item) {
        panel.className = 'selected-empty';
        panel.textContent = 'Choose an item.';
        renderCandidates('suggestions', []);
        renderBestMatch(null);
        const searchInput = document.getElementById('madhushala-search');
        if (searchInput) searchInput.value = '';
        return;
    }

    const captured = item.capturedItem || {};
    panel.className = 'selected-excise';
    panel.innerHTML = `
        <div>
            <span class="eyebrow">Code ${item.exciseItemCode}</span>
            <h3>${item.itemName}</h3>
        </div>
        <dl>
            <div><dt>ML</dt><dd>${captured.measureMl || '-'}</dd></div>
            <div><dt>Pack</dt><dd>${captured.packageType || '-'}</dd></div>
            <div><dt>MRP</dt><dd>${captured.mrpPerUnit || '-'}</dd></div>
        </dl>
    `;

    renderBestMatch(item);
    runSearch();
}

function renderBestMatch(item) {
    const card = document.getElementById('best-match-card');
    if (!card) return;

    const suggestion = item?.suggestions?.[0];
    if (!item || !suggestion) {
        card.hidden = true;
        card.innerHTML = '';
        return;
    }

    const match = suggestion.item;
    card.hidden = false;
    card.innerHTML = `
        <div>
            <span class="eyebrow">Best Match</span>
            <h3>${itemLabel(match)}</h3>
            <p>ML ${match.ml || '-'} · ${Math.round(suggestion.score)}%</p>
        </div>
        <button type="button" id="confirm-best-match">Correct</button>
        <button type="button" id="choose-another" class="secondary">Change</button>
    `;

    document.getElementById('confirm-best-match')?.addEventListener('click', () => {
        selectMadhushalaItem(match.itemCode, suggestion.score);
    });
    document.getElementById('choose-another')?.addEventListener('click', () => {
        document.getElementById('madhushala-search')?.focus();
    });
}

function renderCandidates(containerId, entries, fromSuggestion = false) {
    const container = document.getElementById(containerId);
    if (!container) return;

    if (!entries.length) {
        container.className = 'candidate-list empty';
        container.textContent = fromSuggestion ? 'No suggestions' : 'No results';
        return;
    }

    container.className = 'candidate-list';
    container.innerHTML = entries.map((entry) => {
        const item = entry.item || entry;
        const rawScore = entry.score || 0;
        const score = rawScore ? `<span class="score">${Math.round(rawScore)}%</span>` : '';
        return `
            <button class="candidate" data-code="${item.itemCode}" data-score="${rawScore}" type="button">
                <span>
                    <strong>${itemLabel(item)}</strong>
                    <small>ML ${item.ml || '-'} · Pack ${item.packing || '-'}</small>
                </span>
                ${score}
            </button>
        `;
    }).join('');

    container.querySelectorAll('[data-code]').forEach((button) => {
        button.addEventListener('click', () => selectMadhushalaItem(button.dataset.code, button.dataset.score));
    });
}

function applyMadhushalaSelection(itemCode) {
    if (!selectedExciseCode) return;
    selectedMappings.set(String(selectedExciseCode), itemCode);
    showNotification('Selected', 'success');
    renderWorkspace();
}

function selectMadhushalaItem(itemCode, score = null) {
    if (!selectedExciseCode) return;
    const exciseItem = currentExciseItem();
    const madhushalaItem = findMadhushalaItem(itemCode);
    const issues = guardrailIssues(exciseItem, madhushalaItem, score);
    const duplicateIssue = duplicateMappingIssue(itemCode);
    if (duplicateIssue) issues.push(duplicateIssue);

    if (issues.length) {
        showGuardrailModal(issues, () => applyMadhushalaSelection(itemCode));
        return;
    }

    applyMadhushalaSelection(itemCode);
}

function runSearch() {
    const query = document.getElementById('madhushala-search')?.value || '';
    const selectedItem = currentExciseItem();
    if (!query.trim()) {
        renderCandidates('suggestions', selectedItem?.suggestions || [], true);
        return;
    }

    const results = workspace.madhushalaItems
        .map((item) => ({item, score: scoreMadhushalaSearch(item, query)}))
        .filter((entry) => entry.score > 0)
        .sort((left, right) => right.score - left.score || String(left.item.itemName).localeCompare(String(right.item.itemName)))
        .slice(0, 50);
    renderCandidates('suggestions', results);
}

function updateMappingSummary() {
    const pendingCount = workspace.unmappedItems.filter((item) => !selectedMappings.get(String(item.exciseItemCode)) && !item.selectedItemCode).length;
    const selectedCount = selectedMappings.size;
    setText('mapping-summary', `Selected: ${selectedCount} | Left: ${pendingCount}`);
    const submit = document.getElementById('submit-mappings');
    if (submit) submit.disabled = selectedMappings.size === 0;
}

function updateWorkspaceModeButtons() {
    document.getElementById('show-latest-unmapped')?.classList.toggle('selected', workspaceMode === 'latest');
    document.getElementById('show-all-unmapped')?.classList.toggle('selected', workspaceMode === 'all');
}

async function refreshWorkspace(showToast = true) {
    try {
        const latestOnly = workspaceMode === 'latest';
        workspace = await api(`/mapping/workspace?latestOnly=${latestOnly}`);
        selectedMappings.clear();
        selectedExciseCode = null;
        renderWorkspace();
        if (showToast) showNotification(`Loaded ${workspace.unmappedItems.length}`, 'success');
    } catch (error) {
        showNotification(error.message, 'error');
    }
}

async function switchWorkspaceMode(mode) {
    if (workspaceMode === mode) return;
    workspaceMode = mode;
    updateWorkspaceModeButtons();
    await refreshWorkspace(true);
}

document.getElementById('credentials-form')?.addEventListener('submit', async (event) => {
    event.preventDefault();

    const username = document.getElementById('username')?.value.trim();
    const passwordInput = document.getElementById('password');
    const password = passwordInput?.value || '';

    if ((!username || !password) && !appConfig.exciseCredentialsConfigured) {
        showNotification('Enter User ID and Password', 'error');
        return;
    }

    try {
        await api('/automation/start', {
            method: 'POST',
            body: JSON.stringify({username, password})
        });
        passwordInput.value = '';
        showNotification('Portal opened', 'success');
        updateInstructions({browserRunning: true, loginPageDetected: true});
    } catch (error) {
        showNotification(error.message || 'Could not open portal', 'error');
    }
});

document.getElementById('open-browser-view')?.addEventListener('click', () => {
    const browserPath = basePath
        ? `${basePath}/browser/vnc.html?autoconnect=true&resize=remote&path=excise-import/browser/websockify`
        : '/excise-browser/vnc.html?autoconnect=true&resize=remote&path=excise-browser/websockify';
    window.open(browserPath, '_blank', 'noopener,noreferrer');
});

document.getElementById('capture-selected')?.addEventListener('click', async () => {
    try {
        const result = await api('/automation/capture-selected', {method: 'POST'});
        showNotification(`Captured ${result.itemCount}`, 'success');
        await refreshWorkspace(false);
        openMappingModal();
    } catch (error) {
        showNotification(error.message || 'Capture failed', 'error');
    }
});

document.getElementById('token-form')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const input = document.getElementById('api-token');
    const token = input?.value.trim();
    if (!token && !appConfig.tokenConfigured) {
        showNotification('Add API Token', 'error');
        return;
    }

    try {
        if (token) {
            await api('/madhushala/token', {method: 'POST', body: JSON.stringify({token})});
        }
        input.value = '';
        await loadApiStatus();
        const status = await api('/mapping/status');
        await updateMappingAlert(status);
        showNotification('API Ready', 'success');
    } catch (error) {
        showNotification(error.message, 'error');
    }
});

document.getElementById('refresh-workspace')?.addEventListener('click', refreshWorkspace);
document.getElementById('open-mapping')?.addEventListener('click', async () => {
    openMappingModal();
    await refreshWorkspace(false);
});
document.getElementById('close-mapping')?.addEventListener('click', closeMappingModal);
document.querySelector('[data-close-modal]')?.addEventListener('click', closeMappingModal);
document.getElementById('show-latest-unmapped')?.addEventListener('click', () => switchWorkspaceMode('latest'));
document.getElementById('show-all-unmapped')?.addEventListener('click', () => switchWorkspaceMode('all'));
document.getElementById('madhushala-search')?.addEventListener('input', runSearch);
document.getElementById('cancel-guardrail')?.addEventListener('click', closeGuardrailModal);
document.getElementById('confirm-guardrail')?.addEventListener('click', () => {
    const action = pendingGuardrailAction;
    closeGuardrailModal();
    if (action) action();
});

document.getElementById('submit-mappings')?.addEventListener('click', async () => {
    const mappings = Array.from(selectedMappings.entries()).map(([exciseItemCode, itemCode]) => ({
        exciseItemCode: Number(exciseItemCode),
        itemCode
    }));

    const submitIssues = [];
    for (const mapping of mappings) {
        const exciseItem = workspace.unmappedItems.find((item) => Number(item.exciseItemCode) === Number(mapping.exciseItemCode));
        const madhushalaItem = findMadhushalaItem(mapping.itemCode);
        const issues = guardrailIssues(exciseItem, madhushalaItem);
        const duplicateIssue = duplicateMappingIssue(mapping.itemCode, mapping.exciseItemCode);
        if (duplicateIssue) issues.push(duplicateIssue);
        if (issues.length) {
            submitIssues.push(`${exciseItem?.itemName || mapping.exciseItemCode}: ${issues.join(', ')}`);
        }
    }

    if (submitIssues.length) {
        showGuardrailModal(submitIssues, () => submitMappings(mappings));
        return;
    }

    await submitMappings(mappings);
});

async function submitMappings(mappings) {
    try {
        const result = await api('/mapping/submit', {
            method: 'POST',
            body: JSON.stringify({mappings})
        });
        selectedMappings.clear();
        showNotification(`Saved ${result.mappedCount}`, 'success');
        await refreshWorkspace();
    } catch (error) {
        showNotification(error.message, 'error');
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    updateWorkspaceModeButtons();

    if (mappingOnlyMode) {
        document.body.classList.add('mapping-only');
        workspaceMode = 'latest';
        openMappingModal();
        await refreshWorkspace(false);
        return;
    }

    pollStatus();
    loadApiStatus();
});
