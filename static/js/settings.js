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
 * Fetches the current settings from the server and applies them to the UI.
 *
 * On success, stores the loaded configuration in the module-level state and updates the form to reflect it.
 * On failure, updates the UI with an error status indicating the configuration could not be loaded.
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
 * Populate the settings editor UI from a configuration object.
 *
 * Resets relevant form sections and fills inputs for global settings, servers
 * (including DSNs and hide-tables), authentication, Flask settings, and misc
 * sections according to the provided config.
 *
 * @param {Object} config - Configuration object. Recognized optional keys:
 *   `global` (default_server, read_only, hide_tables), `servers` (map of
 *   serverName -> serverData), `auth` (admin_user, admin_password),
 *   `flask` (SECRET_KEY, SEND_FILE_MAX_AGE_DEFAULT, TEMPLATES_AUTO_RELOAD),
 *   and `misc` (map of miscType -> array of items).
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
    document.getElementById('auth_readonly_user').value = '';
    document.getElementById('auth_readonly_password').value = '';

    if (config.auth) {
        const auth = config.auth;

        if (auth.admin_user) {
            document.getElementById('auth_admin_user').value = auth.admin_user;
        }

        if (auth.admin_password) {
            document.getElementById('auth_admin_password').value = auth.admin_password;
        }

        if (auth.readonly_user) {
            document.getElementById('auth_readonly_user').value = auth.readonly_user;
        }

        if (auth.readonly_password) {
            document.getElementById('auth_readonly_password').value = auth.readonly_password;
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
 * Add a new miscellaneous-items section to the settings UI for the given misc type.
 *
 * Appends a labeled section with an item container and an "Add Item" button to the
 * element with id "misc_sections_container". The section includes an icon chosen
 * from the misc type and a container with id `misc_{miscType}_container`.
 *
 * @param {string} miscType - The miscellaneous item category (e.g., "apply_config", "adhoc_report"); used to derive the section label, icon, and container id.
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
 * Toggle the visible editor between the UI editor and the raw YAML editor.
 * @param {string} mode - If `'ui'`, show the UI editor; any other value shows the raw YAML editor.
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
        // Refresh the textarea with the current saved config
        fetch('/settings/export/')
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    document.getElementById('settings-raw').value = data.yaml;
                }
            });
    }
}

/**
 * Toggle visibility of the collapsible section immediately after a header element.
 * This flips the header's `collapsed` CSS class so the associated content can be shown or hidden.
 * @param {HTMLElement} element - The header element whose next sibling is the collapsible content.
 */
function toggleSection(element) {
    const content = element.nextElementSibling;
    element.classList.toggle('collapsed');
}

/**
 * Create and insert a new server configuration card into the settings UI.
 *
 * If provided, `serverData` populates the card's fields and any DSN or hide-table entries.
 *
 * @param {string} [serverName] - Optional initial server name to set in the card.
 * @param {Object|null} [serverData] - Optional existing server configuration to populate the card.
 *   @param {boolean} [serverData.read_only] - If present, sets the read-only override checkbox.
 *   @param {Array<Object>} [serverData.dsn] - Array of DSN objects to populate DSN entries.
 *   @param {Array<string>} [serverData.hide_tables] - Array of table names to populate hide-table entries.
 * @returns {number} The assigned monotonic server index for the newly created card.
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
 * Remove the DOM card for a server and update the UI server count.
 *
 * If a server element with the provided id exists, it is removed from the DOM
 * and the global `serverCount` is decremented; no action is taken if the
 * element is not found.
 *
 * @param {string} serverId - The DOM id of the server card to remove.
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
 * Toggle visibility of the form-group fields inside a server card.
 *
 * Shows hidden `.form-group` elements or hides visible ones within the server card
 * element identified by `serverId`.
 * @param {string} serverId - The DOM element id of the server card to toggle.
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
 * Create and insert a DSN configuration block for the specified server and return its assigned index.
 *
 * Appends a new DSN card to the server's DSN container in the DOM, ensures the server's monotonic
 * DSN index counter is updated, and refreshes the server's DSN count tracking.
 *
 * @param {number} serverIndex - The server's index identifying which server to attach the DSN to.
 * @param {Object|null} [dsnData=null] - Optional initial values for the DSN fields (keys: `host`, `port`, `user`, `passwd`, `db`).
 * @param {number|null} [dsnIndex=null] - Optional explicit index to assign to this DSN; when omitted a monotonic index is used.
 * @returns {number} The index assigned to the newly created DSN block.
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
 * Ensure the hidden DSN-count input exists for a server and set it to the server's monotonic DSN index.
 *
 * Creates a hidden input named `server_<serverIndex>_dsn_count` if missing and sets its value to the
 * monotonic next DSN index for the given server (or `0` if none), so backend range-processing covers
 * non-sequential DSN indices.
 *
 * @param {number|string} serverIndex - The server's index used to build the hidden input's id and name.
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
 * Append a new hide-table input row to the specified section's hide-tables container.
 *
 * If a container with id `${section}_hide_tables_container` exists, this creates an `.array-item`
 * containing a text input (pre-filled with `value`) and a remove button, then appends it to the container.
 *
 * @param {string} section - The section prefix whose hide-tables container to target (used to build the container id).
 * @param {string} [value=''] - Initial value for the new hide-table input.
 */
function addHideTable(section, value = '') {
    const containerId = `${section}_hide_tables_container`;
    const container = document.getElementById(containerId);

    if (!container) return;

    const index = container.children.length;

    const item = document.createElement('div');
    item.className = 'array-item';
    item.innerHTML = `
        <input type="text" name="${section}_hide_tables_${index}" class="form-control-ui"
               placeholder="table_name" value="${value}" style="padding-right: 40px;" />
        <button type="button" class="array-item-remove" onclick="this.parentElement.remove()">
            <i class="fas fa-times"></i>
        </button>
    `;

    container.appendChild(item);
}

/**
 * Add a misc command/query block to the UI for the given misc type.
 *
 * Updates internal counters for that misc type and the hidden `misc_{type}_count` input so new items use a monotonic index.
 * If `data` is provided, prefills the block's Title, Info, and SQL fields.
 *
 * @param {string} type - The misc type key used to find `misc_{type}_container` and name inputs.
 * @param {{title?: string, info?: string, sql?: string}=} data - Optional initial values to populate the new item's fields.
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
 * Remove a misc command/query UI block and update its counters.
 *
 * Removes the misc item that contains the given remove button from the DOM
 * and decrements the visible item count for the specified misc `type`.
 * Note: `nextMiscIndex[type]` is intentionally not decremented; the hidden
 * counter preserves the highest-ever-assigned index + 1 so the backend's
 * range() covers any non-sequential remaining entries.
 *
 * @param {HTMLElement} button - The remove button element inside the misc item to delete.
 * @param {string} type - The misc command type whose count should be decremented.
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
 * Submit the current settings form to the server and update the UI status based on the response.
 *
 * Sends the data from the form with id "settings-form" to the server endpoint for saving and
 * displays success or error messages using the page's status UI.
 */
function saveSettings() {
    const form = document.getElementById('settings-form');
    const formData = new FormData(form);

    // Show loading state
    showSaveStatus('info', 'Saving...', 'Please wait while we save your settings');

    fetch('/settings/ui_save/', {
        method: 'POST',
        headers: {
            'X-CSRF-Token': (document.querySelector('meta[name="csrf-token"]') || {}).content || '',
        },
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
 * Display a save status banner with an appropriate icon, title, and message.
 *
 * Updates the visible status area to reflect `type` (`success`, `error`, or other for info),
 * sets the icon and color, and places `title` and `message` text into the banner.
 * For `success` and `error` types the banner is automatically hidden after 5 seconds;
 * `info` type remains visible until hidden by other UI actions.
 *
 * @param {string} type - One of `'success'`, `'error'`, or any other value interpreted as `'info'`.
 * @param {string} title - Short title text shown in the status banner.
 * @param {string} message - Detailed message text shown in the status banner.
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
 * Fetches the server-side configuration as YAML and initiates a browser download.
 *
 * If the export succeeds, a file named `config.yml` is downloaded containing the YAML.
 * On failure, a UI error status is shown.
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
 * Displays the import modal dialog.
 *
 * Makes the DOM element with id "importModal" visible.
 */
function showImportModal() {
    const modal = document.getElementById('importModal');
    modal.style.display = 'flex';
}

/**
 * Hide the import modal and clear its YAML input.
 *
 * Hides the element with id "importModal" and resets the value of the textarea/input
 * with id "importYamlContent" to an empty string.
 */
function closeImportModal() {
    const modal = document.getElementById('importModal');
    modal.style.display = 'none';
    document.getElementById('importYamlContent').value = '';
}

/**
 * Import YAML configuration provided in the import modal and apply it on the server.
 *
 * Reads YAML from the import textarea, validates it is not empty, and posts it to /settings/import/
 * (including an `X-CSRF-Token` header if a meta token is present). On success, shows a success status,
 * closes the import modal, and reloads the page after 1.5 seconds. On failure, shows an error status
 * using the server-provided message or the network error message.
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
        headers: {
            'X-CSRF-Token': (document.querySelector('meta[name="csrf-token"]') || {}).content || '',
        },
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
 * Restore the form to the originally loaded configuration after user confirmation.
 *
 * Prompts the user to confirm; if confirmed, reloads the original configuration into the form and displays a success status message.
 */
function resetForm() {
    if (confirm('Are you sure you want to reset all changes? This will reload the original configuration.')) {
        loadConfig();
        showSaveStatus('success', 'Reset Complete', 'Form has been reset to original values');
    }
}
