// Madhushala Excise Bridge - Frontend JavaScript

const ws = new WebSocket(`ws://${window.location.host}/ws/events`);
const selectedItems = {};

// WebSocket event handling
ws.onmessage = function(event) {
    const data = JSON.parse(event.data);
    console.log('Event received:', data);
    
    switch(data.type) {
        case 'BROWSER_STARTED':
            updateBrowserStatus('Connected');
            break;
        case 'PAGE_CHANGED':
            updatePageUrl(data.url);
            break;
        case 'PREPARE_INDENT_DETECTED':
            showNotification('Prepare Indent page detected', 'info');
            break;
        case 'ITEM_SELECTED':
            addItemToSelection(data.item);
            break;
        case 'ITEM_UNSELECTED':
            removeItemFromSelection(data.canonicalKey);
            break;
        case 'BUCKET_COMMITTED':
            showNotification(`Bucket committed: ${data.itemCount} items`, 'info');
            break;
        case 'MAPPING_REQUIRED':
            showMappingModal(data.data);
            break;
        case 'MAPPING_SAVED':
            closeMappingModal();
            showNotification('Mapping saved successfully', 'success');
            break;
        case 'ERROR':
            showNotification(`Error: ${data.message}`, 'error');
            break;
    }
};

ws.onopen = function() {
    console.log('WebSocket connected');
    updateBrowserStatus('Connected');
};

ws.onclose = function() {
    console.log('WebSocket disconnected');
    updateBrowserStatus('Disconnected');
};

// UI Update Functions
function updateBrowserStatus(status) {
    const el = document.getElementById('browser-status');
    if (el) {
        el.textContent = `Browser: ${status}`;
    }
}

function updatePageUrl(url) {
    const el = document.getElementById('page-url');
    if (el) {
        el.textContent = url;
    }
}

function addItemToSelection(item) {
    selectedItems[item.canonicalKey] = item;
    renderSelectedItems();
}

function removeItemFromSelection(canonicalKey) {
    delete selectedItems[canonicalKey];
    renderSelectedItems();
}

function renderSelectedItems() {
    const el = document.getElementById('selected-items');
    if (el) {
        if (Object.keys(selectedItems).length === 0) {
            el.innerHTML = 'Selected Items: None';
            return;
        }
        
        let html = '<div id="selected-items-list">';
        for (const key in selectedItems) {
            const item = selectedItems[key];
            const safeJson = JSON.stringify(item).replace(/"/g, '"').replace(/'/g, '&#x27;');
            html += `
                <div class="item-row">
                    <div class="item-info">
                        <div class="item-brand">${item.brand}</div>
                        <div class="item-details">
                            ${item.measure_ml} ML | ${item.package_type} | MRP: ₹${item.mrp_per_unit || 'N/A'}
                        </div>
                    </div>
                    <div class="item-actions">
                        <button class="btn-map" onclick="showMappingModal('${safeJson}')">Map</button>
                    </div>
                </div>
            `;
        }
        html += '</div>';
        el.innerHTML = html;
    }
}

// Notification System
function showNotification(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);
    
    setTimeout(() => {
        toast.remove();
    }, 3000);
}

// Mapping Modal
function showMappingModal(itemData) {
    // Parse item data if string
    if (typeof itemData === 'string') {
        try {
            itemData = JSON.parse(itemData);
        } catch(e) {
            console.error('Failed to parse itemData:', e);
            return;
        }
    }
    
    const modal = document.getElementById('mapping-modal');
    if (modal) {
        modal.classList.add('active');
        
        // Populate modal with item details
        const content = modal.querySelector('.modal-content');
        content.innerHTML = `
            <h3>Mapping Required</h3>
            <div class="modal-field">
                <label>Excise Item:</label>
                <div><strong>${itemData.brand || 'Unknown'}</strong></div>
                <div>${itemData.measure_ml || 'N/A'} ML | ${itemData.package_type || 'N/A'}</div>
                <div>MRP: ₹${itemData.mrp_per_unit || 'N/A'}</div>
            </div>
            <div class="modal-field">
                <label for="item-code">Madhushala Item Code:</label>
                <input type="text" id="item-code" placeholder="Enter item code">
            </div>
            <div class="modal-actions">
                <button class="btn btn-secondary" onclick="closeMappingModal()">Cancel</button>
                <button class="btn btn-primary" onclick="saveMapping()">Map Item</button>
            </div>
        `;
        
        // Store itemData globally for saveMapping function
        window.currentMappingItem = itemData;
    }
}

function closeMappingModal() {
    const modal = document.getElementById('mapping-modal');
    if (modal) {
        modal.classList.remove('active');
    }
    window.currentMappingItem = null;
}

function saveMapping() {
    const itemCode = document.getElementById('item-code').value;
    if (!itemCode) {
        showNotification('Please enter an item code', 'error');
        return;
    }
    
    const itemData = window.currentMappingItem;
    if (!itemData) {
        showNotification('No item data available', 'error');
        return;
    }
    
    fetch('/automation/map', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            exciseItemCode: itemData.exciseItemCode,
            itemCode: itemCode
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            showNotification(data.error, 'error');
        } else {
            closeMappingModal();
            showNotification('Mapping saved successfully', 'success');
        }
    })
    .catch(error => {
        console.error('Save mapping error:', error);
        showNotification('Failed to save mapping', 'error');
    });
}

// Button Event Handlers
document.getElementById('start-automation')?.addEventListener('click', async () => {
    try {
        const response = await fetch('/automation/start', { method: 'POST' });
        const data = await response.json();
        if (data.sessionId) {
            showNotification('Automation started', 'success');
        } else {
            showNotification(data.error || 'Failed to start automation', 'error');
        }
    } catch (error) {
        console.error('Start automation error:', error);
        showNotification('Failed to start automation', 'error');
    }
});

document.getElementById('open-excise')?.addEventListener('click', async () => {
    try {
        const response = await fetch('/automation/open-excise', { method: 'POST' });
        const data = await response.json();
        if (data.status === 'browser_opened') {
            showNotification('Excise portal opened', 'success');
        } else {
            showNotification(data.error || 'Failed to open Excise portal', 'error');
        }
    } catch (error) {
        console.error('Open excise error:', error);
        showNotification('Failed to open Excise portal', 'error');
    }
});

document.getElementById('stop-automation')?.addEventListener('click', async () => {
    try {
        const response = await fetch('/automation/stop', { method: 'POST' });
        const data = await response.json();
        if (data.status === 'stopped') {
            showNotification('Automation stopped', 'success');
            updateBrowserStatus('Disconnected');
        } else {
            showNotification(data.error || 'Failed to stop automation', 'error');
        }
    } catch (error) {
        console.error('Stop automation error:', error);
        showNotification('Failed to stop automation', 'error');
    }
});

// Health check
async function checkHealth() {
    try {
        const response = await fetch('/health');
        const data = await response.json();
        console.log('Health check:', data);
    } catch (error) {
        console.error('Health check failed:', error);
    }
}

// Initial load
checkHealth();
</arg_value>
<task_progress>
- [x] Create main application file
- [x] Implement Playwright automation for manual consent
- [x] Create static HTML demo page
- [x] Implement Madhushala API client
- [x] Create configuration file
- [x] Create database models
- [x] Create database connection module
- [x] Initialize git repository
- [x] Create requirements.txt
- [x] Create .env.example
- [x] Create README.md
- [x] Create style.css
- [x] Create app.js
- [ ] Create test files
- [ ] Run syntax checks
- [ ] Run pytest
- [ ] Verify server starts
- [ ] Verify /health endpoint
- [ ] Verify demo page loads
- [ ] Verify Playwright launches
- [ ] Create IMPLEMENTATION_STATUS.md
</task_progress>
</write_to_file>