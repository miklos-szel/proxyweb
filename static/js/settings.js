/**
 * ProxyWeb Settings Editor JavaScript
 * Handles form management, dynamic server configuration, and UI interactions
 */

let configData = {};
let serverCount = 0;
let nextServerIndex = 0;  // monotonically increasing; never decremented
let nextDsnIndex = {};    // per serverIndex; never decremented
let nextMiscIndex = {};   // per misc type; never decremented
let miscCount = {}; // Dynamic misc section counters

/**
 * Initialize the settings page
 */
document.addEventListener('DOMContentLoaded', function() {
    loadConfig();
    setupFormHandlers();
});

/**
 * Load configuration data from server
 */
function loadConfig() {
    fetch('/settings/load_ui/')
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                configData = data.config;
                populateForm(data.config);
            }
        })
        .catch(error => {
            showSaveStatus('error', 'Error', 'Failed to load configuration');
        });
}

/**
 * Populate the form with config data
 */
function populateForm(config) {
    // Global section — clear first so stale values don't persist on reset
    document.getElementById('global_default_server').value = '';
    document.getElementById('global_read_only').checked = false;
    document.getElementById('global_hide_tables_container').innerHTML = '';

    if (config.global) {
        const global = config.global;

        if (global.default_server) {
            document.getElementById('global_default_server').value = global.default_server;
        }

        if (typeof global.read_only !== 'undefined') {
            document.getElementById('global_read_only').checked = global.read_only;
        }

        if (global.hide_tables && Array.isArray(global.hide_tables)) {
            global.hide_tables.forEach((table, index) => {
                addHideTable('global', table);
            });
        } else {
            addHideTable('global', '');
        }
    }

    // Servers section
    const serversContainer = document.getElementById('servers_container');
    if (serversContainer) serversContainer.innerHTML = '';
    serverCount = 0;
    nextServerIndex = 0;
    nextDsnIndex = {};
    const serverCountInput = document.getElementById('server_count');
    if (serverCountInput) serverCountInput.value = 0;
    if (config.servers) {
        Object.keys(config.servers).forEach((serverName, index) => {
            addServer(serverName, config.servers[serverName]);
        });
    }

    // Auth section — clear first
    document.getElementById('auth_admin_user').value = '';
    document.getElementById('auth_admin_password').value = '';

    if (config.auth) {
        const auth = config.auth;

        if (auth.admin_user) {
            document.getElementById('auth_admin_user').value = auth.admin_user;
        }

        if (auth.admin_password) {
            document.getElementById('auth_admin_password').value = auth.admin_password;
        }
    }

    // Flask section — clear first
    document.getElementById('flask_SECRET_KEY').value = '';
    document.getElementById('flask_SEND_FILE_MAX_AGE_DEFAULT').value = '';
    document.getElementById('flask_TEMPLATES_AUTO_RELOAD').checked = false;

    if (config.flask) {
        const flask = config.flask;

        if (flask.SECRET_KEY) {
            document.getElementById('flask_SECRET_KEY').value = flask.SECRET_KEY;
        }

        if (flask.SEND_FILE_MAX_AGE_DEFAULT !== undefined) {
            document.getElementById('flask_SEND_FILE_MAX_AGE_DEFAULT').value = flask.SEND_FILE_MAX_AGE_DEFAULT;
        }

        if (typeof flask.TEMPLATES_AUTO_RELOAD !== 'undefined') {
            document.getElementById('flask_TEMPLATES_AUTO_RELOAD').checked =
                flask.TEMPLATES_AUTO_RELOAD === 'True' || flask.TEMPLATES_AUTO_RELOAD === true;
        }
    }

    // Misc section - dynamically handle all misc sections
    if (config.misc) {
        const misc = config.misc;

        // First, create the UI sections for each misc type
        const miscContainer = document.getElementById('misc_sections_container');
        if (miscContainer) {
            miscContainer.innerHTML = ''; // Clear existing content

            // Iterate over all keys in the misc object to create sections
            Object.keys(misc).forEach(miscType => {
                if (Array.isArray(misc[miscType])) {
                    createMiscSection(miscType);
                }
            });
        }

        // Then populate with data
        Object.keys(misc).forEach(miscType => {
            if (Array.isArray(misc[miscType])) {
                // Reset counters for this misc type
                miscCount[miscType] = 0;
                nextMiscIndex[miscType] = 0;

                // Add each item
                misc[miscType].forEach(item => {
                    addMiscCommand(miscType, item);
                });
            }
        });
    }
}

/**
 * Create a misc section UI dynamically
 */
function createMiscSection(miscType) {
    const container = document.getElementById('misc_sections_container');
    if (!container) return;

    // Convert misc type to readable label (e.g., "apply_config" -> "Apply Config")
    const label = miscType.split('_').map(word =>
        word.charAt(0).toUpperCase() + word.slice(1)
    ).join(' ');

    // Determine icon based on misc type
    let icon = 'fas fa-cog';
    if (miscType.includes('apply')) {
        icon = 'fas fa-play';
    } else if (miscType.includes('update')) {
        icon = 'fas fa-edit';
    } else if (miscType.includes('report') || miscType.includes('adhoc')) {
        icon = 'fas fa-chart-line';
    } else if (miscType.includes('config')) {
        icon = 'fas fa-cogs';
    }

    const sectionDiv = document.createElement('div');
    sectionDiv.className = 'form-group';
    sectionDiv.innerHTML = `
        <label>
            <i class="${icon}"></i> ${label}
        </label>
        <div id="misc_${miscType}_container"></div>
        <button type="button" class="btn btn-outline-primary btn-sm" onclick="addMiscCommand('${miscType}')" style="margin-top: 0.5rem;">
            <i class="fas fa-plus"></i> Add Item
        </button>
    `;

    container.appendChild(sectionDiv);
}

/**
 * Switch between UI Editor and Raw YAML modes
 */
function switchMode(mode) {
    const uiEditor = document.getElementById('ui-editor');
    const rawYaml = document.getElementById('raw-yaml');
    const btnUiEditor = document.getElementById('btn-ui-editor');
    const btnRawYaml = document.getElementById('btn-raw-yaml');

    if (mode === 'ui') {
        uiEditor.style.display = 'block';
        rawYaml.style.display = 'none';
        btnUiEditor.classList.add('active');
        btnRawYaml.classList.remove('active');
    } else {
        uiEditor.style.display = 'none';
        rawYaml.style.display = 'block';
        btnRawYaml.classList.add('active');
        btnUiEditor.classList.remove('active');
    }
}

/**
 * Toggle collapsible sections
 */
function toggleSection(element) {
    const content = element.nextElementSibling;
    element.classList.toggle('collapsed');
}

/**
 * Add a server configuration card
 */
function addServer(serverName = '', serverData = null) {
    const container = document.getElementById('servers_container');
    const serverIndex = nextServerIndex++;
    serverCount++;

    const serverCard = document.createElement('div');
    serverCard.className = 'server-card';
    serverCard.id = `server-${serverIndex}`;

    serverCard.innerHTML = `
        <div class="server-card-header">
            <h5 class="server-card-title">
                <i class="fas fa-server"></i>
                Server Configuration
            </h5>
            <div class="server-card-actions">
                <button type="button" class="btn btn-outline-info btn-sm" onclick="expandServer('server-${serverIndex}')">
                    <i class="fas fa-expand"></i> Expand
                </button>
                <button type="button" class="btn btn-outline-danger btn-sm" onclick="removeServer('server-${serverIndex}')">
                    <i class="fas fa-trash"></i> Remove
                </button>
            </div>
        </div>

        <div class="form-group">
            <label for="server_${serverIndex}_name">Server Name</label>
            <input type="text" id="server_${serverIndex}_name" name="server_${serverIndex}_name"
                   class="form-control-ui" placeholder="proxysql" value="${serverName || ''}" />
        </div>

        <div class="form-group">
            <div class="checkbox-group">
                <input type="checkbox" id="server_${serverIndex}_read_only_override" name="server_${serverIndex}_read_only_override" />
                <label for="server_${serverIndex}_read_only_override">Override Read-Only Mode</label>
            </div>
        </div>

        <div class="form-group">
            <label>
                <i class="fas fa-database"></i> DSN Configuration
            </label>
            <div id="server_${serverIndex}_dsn_container"></div>
            <button type="button" class="btn btn-outline-primary btn-sm" onclick="addDSN(${serverIndex})" style="margin-top: 0.5rem;">
                <i class="fas fa-plus"></i> Add DSN
            </button>
        </div>

        <div class="form-group">
            <label>
                <i class="fas fa-eye-slash"></i> Hide Tables (Optional)
            </label>
            <div id="server_${serverIndex}_hide_tables_container"></div>
            <button type="button" class="btn btn-outline-primary btn-sm" onclick="addHideTable('server_${serverIndex}')" style="margin-top: 0.5rem;">
                <i class="fas fa-plus"></i> Add Table
            </button>
        </div>
    `;

    container.appendChild(serverCard);

    // Add hidden input for server count
    let serverCountInput = document.getElementById('server_count');
    if (!serverCountInput) {
        serverCountInput = document.createElement('input');
        serverCountInput.type = 'hidden';
        serverCountInput.id = 'server_count';
        serverCountInput.name = 'server_count';
        serverCountInput.value = '0';
        document.getElementById('settings-form').appendChild(serverCountInput);
    }
    serverCountInput.value = nextServerIndex;

    // Add first DSN by default
    addDSN(serverIndex);

    // Populate with existing data if provided
    if (serverData) {
        document.getElementById(`server_${serverIndex}_name`).value = serverName;

        if (serverData.read_only !== undefined) {
            document.getElementById(`server_${serverIndex}_read_only_override`).checked = serverData.read_only;
        }

        if (serverData.dsn && Array.isArray(serverData.dsn)) {
            const dsnContainer = document.getElementById(`server_${serverIndex}_dsn_container`);
            dsnContainer.innerHTML = '';
            serverData.dsn.forEach((dsn, dsnIndex) => {
                addDSN(serverIndex, dsn, dsnIndex);
            });
        }

        if (serverData.hide_tables && Array.isArray(serverData.hide_tables)) {
            const hideTablesContainer = document.getElementById(`server_${serverIndex}_hide_tables_container`);
            hideTablesContainer.innerHTML = '';
            serverData.hide_tables.forEach(table => {
                addHideTable(`server_${serverIndex}`, table);
            });
        }
    }

    return serverIndex;
}

/**
 * Remove a server configuration card
 */
function removeServer(serverId) {
    const serverCard = document.getElementById(serverId);
    if (serverCard) {
        serverCard.remove();
        serverCount--;
        // nextServerIndex is not decremented; server_count input keeps the
        // highest-ever-assigned index + 1 so the backend's range() covers all
        // remaining entries even when they are non-sequential
    }
}

/**
 * Expand/collapse server card
 */
function expandServer(serverId) {
    const serverCard = document.getElementById(serverId);
    if (serverCard) {
        const allFormGroups = serverCard.querySelectorAll('.form-group');
        allFormGroups.forEach(group => {
            group.style.display = group.style.display === 'none' ? 'block' : 'none';
        });
    }
}

/**
 * Add a DSN configuration to a server
 */
function addDSN(serverIndex, dsnData = null, dsnIndex = null) {
    const container = document.getElementById(`server_${serverIndex}_dsn_container`);
    if (!(serverIndex in nextDsnIndex)) nextDsnIndex[serverIndex] = 0;
    const index = dsnIndex !== null ? dsnIndex : nextDsnIndex[serverIndex];
    nextDsnIndex[serverIndex] = Math.max(nextDsnIndex[serverIndex], index + 1);

    const dsnCard = document.createElement('div');
    dsnCard.className = 'dsn-card';
    dsnCard.id = `server_${serverIndex}_dsn_${index}`;

    dsnCard.innerHTML = `
        <div class="dsn-card-header">
            <h6 class="dsn-card-title">DSN Configuration #${index + 1}</h6>
            <button type="button" class="btn btn-sm" onclick="removeDSN('server_${serverIndex}_dsn_${index}', ${serverIndex})"
                    style="background: var(--danger-color); color: white;">
                <i class="fas fa-times"></i>
            </button>
        </div>

        <div class="row">
            <div class="col-md-6">
                <div class="form-group">
                    <label for="server_${serverIndex}_dsn_${index}_host">Host</label>
                    <input type="text" id="server_${serverIndex}_dsn_${index}_host" name="server_${serverIndex}_dsn_${index}_host"
                           class="form-control-ui" placeholder="host.docker.internal" />
                </div>
            </div>
            <div class="col-md-6">
                <div class="form-group">
                    <label for="server_${serverIndex}_dsn_${index}_port">Port</label>
                    <input type="text" id="server_${serverIndex}_dsn_${index}_port" name="server_${serverIndex}_dsn_${index}_port"
                           class="form-control-ui" placeholder="16032" />
                </div>
            </div>
        </div>

        <div class="row">
            <div class="col-md-6">
                <div class="form-group">
                    <label for="server_${serverIndex}_dsn_${index}_user">User</label>
                    <input type="text" id="server_${serverIndex}_dsn_${index}_user" name="server_${serverIndex}_dsn_${index}_user"
                           class="form-control-ui" placeholder="radmin" />
                </div>
            </div>
            <div class="col-md-6">
                <div class="form-group">
                    <label for="server_${serverIndex}_dsn_${index}_passwd">Password</label>
                    <input type="password" id="server_${serverIndex}_dsn_${index}_passwd" name="server_${serverIndex}_dsn_${index}_passwd"
                           class="form-control-ui" placeholder="********" />
                </div>
            </div>
        </div>

        <div class="form-group">
            <label for="server_${serverIndex}_dsn_${index}_db">Database</label>
            <input type="text" id="server_${serverIndex}_dsn_${index}_db" name="server_${serverIndex}_dsn_${index}_db"
                   class="form-control-ui" placeholder="main" />
        </div>
    `;

    container.appendChild(dsnCard);

    // Update DSN count
    updateDSNCount(serverIndex);

    // Populate with existing data if provided
    if (dsnData) {
        Object.keys(dsnData).forEach(key => {
            const element = document.getElementById(`server_${serverIndex}_dsn_${index}_${key}`);
            if (element) {
                element.value = dsnData[key];
            }
        });
    }

    return index;
}

/**
 * Remove a DSN configuration
 */
function removeDSN(dsnId, serverIndex) {
    const dsnCard = document.getElementById(dsnId);
    if (dsnCard) {
        dsnCard.remove();
        updateDSNCount(serverIndex);
    }
}

/**
 * Update DSN count for a server
 */
function updateDSNCount(serverIndex) {
    let countInput = document.getElementById(`server_${serverIndex}_dsn_count`);
    if (!countInput) {
        countInput = document.createElement('input');
        countInput.type = 'hidden';
        countInput.id = `server_${serverIndex}_dsn_count`;
        countInput.name = `server_${serverIndex}_dsn_count`;
        document.getElementById('settings-form').appendChild(countInput);
    }
    // Use the monotonic counter so the backend's range() covers non-sequential indices
    countInput.value = nextDsnIndex[serverIndex] || 0;
}

/**
 * Add a hide table entry
 */
function addHideTable(section, value = '') {
    const containerId = `${section}_hide_tables_container`;
    const container = document.getElementById(containerId);

    if (!container) return;

    const index = container.children.length;

    const item = document.createElement('div');
    item.className = 'array-item';
    item.innerHTML = `
        <input type="text" name="global_hide_tables_${index}" class="form-control-ui"
               placeholder="table_name" value="${value}" style="padding-right: 40px;" />
        <button type="button" class="array-item-remove" onclick="this.parentElement.remove()">
            <i class="fas fa-times"></i>
        </button>
    `;

    container.appendChild(item);
}

/**
 * Add a misc command/query entry
 */
function addMiscCommand(type, data = null) {
    const containerId = `misc_${type}_container`;
    const container = document.getElementById(containerId);

    if (!container) {
        return;
    }

    // Initialize counters for this type if they don't exist
    if (!miscCount[type]) miscCount[type] = 0;
    if (!nextMiscIndex[type]) nextMiscIndex[type] = 0;

    const index = nextMiscIndex[type]++;
    miscCount[type]++;

    // Create hidden counter if it doesn't exist
    let counter = document.getElementById(`misc_${type}_count`);
    if (!counter) {
        counter = document.createElement('input');
        counter.type = 'hidden';
        counter.id = `misc_${type}_count`;
        counter.name = `misc_${type}_count`;
        counter.value = '0';
        document.getElementById('settings-form').appendChild(counter);
    }
    counter.value = nextMiscIndex[type];

    const item = document.createElement('div');
    item.className = 'array-item';
    item.innerHTML = `
        <div class="form-group">
            <label for="misc_${type}_${index}_title">Title</label>
            <input type="text" id="misc_${type}_${index}_title" name="misc_${type}_${index}_title"
                   class="form-control-ui" placeholder="Command Title" />
        </div>

        <div class="form-group">
            <label for="misc_${type}_${index}_info">Info</label>
            <textarea id="misc_${type}_${index}_info" name="misc_${type}_${index}_info"
                      class="form-control-ui" placeholder="Description (use \\n for new lines)" rows="2"></textarea>
        </div>

        <div class="form-group">
            <label for="misc_${type}_${index}_sql">SQL Command</label>
            <textarea id="misc_${type}_${index}_sql" name="misc_${type}_${index}_sql"
                      class="form-control-ui" placeholder="SQL command or query" rows="3"></textarea>
        </div>

        <button type="button" class="array-item-remove" onclick="removeMiscCommand(this, '${type}')">
            <i class="fas fa-times"></i>
        </button>
    `;

    container.appendChild(item);

    // Populate with existing data if provided
    if (data) {
        document.getElementById(`misc_${type}_${index}_title`).value = data.title || '';
        document.getElementById(`misc_${type}_${index}_info`).value = data.info || '';
        document.getElementById(`misc_${type}_${index}_sql`).value = data.sql || '';
    }
}

/**
 * Remove a misc command/query entry
 */
function removeMiscCommand(button, type) {
    button.parentElement.remove();
    miscCount[type]--;
    // nextMiscIndex[type] is not decremented; the hidden counter keeps the
    // highest-ever-assigned index + 1 so the backend's range() covers all
    // remaining entries even when they are non-sequential
}

/**
 * Setup form submission handlers
 */
function setupFormHandlers() {
    const form = document.getElementById('settings-form');
    if (form) {
        form.addEventListener('submit', function(e) {
            e.preventDefault();
            saveSettings();
        });
    }
}

/**
 * Save settings to server
 */
function saveSettings() {
    const form = document.getElementById('settings-form');
    const formData = new FormData(form);

    // Show loading state
    showSaveStatus('info', 'Saving...', 'Please wait while we save your settings');

    fetch('/settings/ui_save/', {
        method: 'POST',
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showSaveStatus('success', 'Success!', 'Your settings have been saved successfully');
        } else {
            showSaveStatus('error', 'Error', data.error || 'Failed to save settings');
        }
    })
    .catch(error => {
        showSaveStatus('error', 'Error', 'Failed to save settings: ' + error.message);
    });
}

/**
 * Show save status message
 */
function showSaveStatus(type, title, message) {
    const statusDiv = document.getElementById('saveStatus');
    const icon = document.getElementById('saveStatusIcon');
    const titleEl = document.getElementById('saveStatusTitle');
    const msgEl = document.getElementById('saveStatusMessage');

    // Set icon based on type
    if (type === 'success') {
        icon.className = 'fas fa-check-circle';
        icon.style.color = 'var(--success-color)';
    } else if (type === 'error') {
        icon.className = 'fas fa-exclamation-circle';
        icon.style.color = 'var(--danger-color)';
    } else {
        icon.className = 'fas fa-spinner fa-spin';
        icon.style.color = 'var(--info-color)';
    }

    titleEl.textContent = title;
    msgEl.textContent = message;

    statusDiv.className = `save-status show ${type}`;

    // Auto-hide after 5 seconds for success/error messages
    if (type !== 'info') {
        setTimeout(() => {
            statusDiv.classList.remove('show');
        }, 5000);
    }
}

/**
 * Export configuration as YAML
 */
function exportConfig() {
    fetch('/settings/export/')
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const blob = new Blob([data.yaml], { type: 'text/yaml' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'config.yml';
                a.click();
                URL.revokeObjectURL(url);

                showSaveStatus('success', 'Export Complete', 'Configuration has been exported to config.yml');
            } else {
                showSaveStatus('error', 'Export Failed', data.error || 'Failed to export configuration');
            }
        })
        .catch(error => {
            showSaveStatus('error', 'Export Failed', 'Failed to export configuration');
        });
}

/**
 * Show import modal
 */
function showImportModal() {
    const modal = document.getElementById('importModal');
    modal.style.display = 'flex';
}

/**
 * Close import modal
 */
function closeImportModal() {
    const modal = document.getElementById('importModal');
    modal.style.display = 'none';
    document.getElementById('importYamlContent').value = '';
}

/**
 * Import configuration from YAML
 */
function importConfig() {
    const yamlContent = document.getElementById('importYamlContent').value;

    if (!yamlContent.trim()) {
        showSaveStatus('error', 'Error', 'Please paste YAML content to import');
        return;
    }

    const formData = new FormData();
    formData.append('yaml_content', yamlContent);

    showSaveStatus('info', 'Importing...', 'Please wait while we import your configuration');

    fetch('/settings/import/', {
        method: 'POST',
        body: formData
    })
    .then(response => response.json())
        .then(data => {
            if (data.success) {
                showSaveStatus('success', 'Import Complete', 'Configuration has been imported successfully');
                closeImportModal();

                // Reload the page to refresh all data
                setTimeout(() => {
                    window.location.reload();
                }, 1500);
            } else {
                showSaveStatus('error', 'Import Failed', data.error || 'Failed to import configuration');
            }
        })
        .catch(error => {
            showSaveStatus('error', 'Import Failed', 'Failed to import configuration: ' + error.message);
        });
}

/**
 * Reset form to original values
 */
function resetForm() {
    if (confirm('Are you sure you want to reset all changes? This will reload the original configuration.')) {
        loadConfig();
        showSaveStatus('success', 'Reset Complete', 'Form has been reset to original values');
    }
}
