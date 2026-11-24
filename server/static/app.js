let activeTab = 'networks';
let cachedNetworks = [];
let cachedNodes = [];
let authInterval = null;

document.addEventListener('DOMContentLoaded', () => {
    initAuth();
    initTabs();
    initModals();
});

async function initAuth() {
    try {
        const res = await fetch('/api/auth-status');
        if (res.ok) {
            const data = await res.json();
            if (!data.auth_required || data.authenticated) {
                showDashboard();
            } else {
                showLogin();
            }
        } else {
            showLogin();
        }
    } catch (err) {
        showLogin();
    }

    document.getElementById('form-login').addEventListener('submit', async (e) => {
        e.preventDefault();
        const pass = document.getElementById('login-pass-input').value;
        const errEl = document.getElementById('login-error');
        errEl.style.display = 'none';

        try {
            const res = await fetch('/api/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ password: pass })
            });

            if (res.ok) {
                document.getElementById('login-pass-input').value = '';
                showDashboard();
            } else {
                errEl.innerText = 'Incorrect administrator password';
                errEl.style.display = 'block';
            }
        } catch (err) {
            errEl.innerText = 'Connection error';
            errEl.style.display = 'block';
        }
    });

    document.getElementById('btn-logout').addEventListener('click', async () => {
        await fetch('/api/logout', { method: 'POST' });
        showLogin();
    });
}

function showLogin() {
    if (authInterval) {
        clearInterval(authInterval);
        authInterval = null;
    }
    document.getElementById('login-container').style.display = 'flex';
    document.getElementById('dashboard-container').style.display = 'none';
    document.getElementById('login-pass-input').focus();
}

function showDashboard() {
    document.getElementById('login-container').style.display = 'none';
    document.getElementById('dashboard-container').style.display = 'block';
    loadDashboard();
    if (!authInterval) {
        authInterval = setInterval(loadDashboard, 4000);
    }
}

function initTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            activeTab = btn.dataset.tab;

            document.getElementById('networks-view').style.display = activeTab === 'networks' ? 'block' : 'none';
            document.getElementById('nodes-view').style.display = activeTab === 'nodes' ? 'block' : 'none';
        });
    });
}

function initModals() {
    document.getElementById('btn-new-net').addEventListener('click', () => {
        document.getElementById('modal-create-net').classList.add('open');
    });

    document.getElementById('form-create-net').addEventListener('submit', async (e) => {
        e.preventDefault();
        const name = document.getElementById('net-name-input').value.trim();
        const token = document.getElementById('net-token-input').value.trim();
        if (!name) return;

        try {
            const res = await fetch('/api/networks', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, auth_token: token || undefined })
            });
            if (res.ok) {
                closeModal('modal-create-net');
                document.getElementById('net-name-input').value = '';
                document.getElementById('net-token-input').value = '';
                loadDashboard();
            } else if (res.status === 401) {
                showLogin();
            } else {
                alert('Failed to create network');
            }
        } catch (err) {
            console.error(err);
        }
    });
}

function closeModal(id) {
    document.getElementById(id).classList.remove('open');
}

async function loadDashboard() {
    try {
        const [netRes, nodeRes] = await Promise.all([
            fetch('/api/networks'),
            fetch('/api/nodes')
        ]);

        if (netRes.status === 401 || nodeRes.status === 401) {
            showLogin();
            return;
        }

        if (netRes.ok && nodeRes.ok) {
            cachedNetworks = await netRes.json();
            cachedNodes = await nodeRes.json();
            updateStats();
            renderNetworks();
            renderNodes();
        }
    } catch (err) {
        console.error('Error loading dashboard:', err);
    }
}

function updateStats() {
    document.getElementById('stat-networks').innerText = cachedNetworks.length;
    const hosts = cachedNodes.filter(n => n.role === 'host').length;
    const viewers = cachedNodes.filter(n => n.role === 'viewer').length;
    document.getElementById('stat-hosts').innerText = hosts;
    document.getElementById('stat-viewers').innerText = viewers;
}

function renderNetworks() {
    const tbody = document.getElementById('networks-tbody');
    if (!cachedNetworks.length) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-secondary); padding: 24px;">No networks created yet. Click "New Network" above.</td></tr>';
        return;
    }

    tbody.innerHTML = cachedNetworks.map(net => `
        <tr>
            <td><strong>${escapeHtml(net.name)}</strong></td>
            <td><code style="font-size: 12px; color: var(--accent);">${escapeHtml(net.id)}</code></td>
            <td>
                <span class="token-badge" onclick="toggleToken(this, '${escapeHtml(net.auth_token)}')">••••••••</span>
            </td>
            <td><span class="badge badge-host">${net.hosts_count || 0} hosts</span></td>
            <td><span class="badge badge-viewer">${net.viewers_count || 0} viewers</span></td>
            <td class="actions-cell">
                <button class="btn btn-primary btn-sm" onclick="showConnectModal('${escapeHtml(net.id)}')">Connect</button>
                <button class="btn btn-danger btn-sm" onclick="deleteNetwork('${escapeHtml(net.id)}')">Delete</button>
            </td>
        </tr>
    `).join('');
}

function renderNodes() {
    const tbody = document.getElementById('nodes-tbody');
    if (!cachedNodes.length) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-secondary); padding: 24px;">No active nodes connected right now.</td></tr>';
        return;
    }

    tbody.innerHTML = cachedNodes.map(node => `
        <tr>
            <td><strong>${escapeHtml(node.name)}</strong></td>
            <td>${escapeHtml(node.network_name || node.network_id)}</td>
            <td><span class="badge ${node.role === 'host' ? 'badge-host' : 'badge-viewer'}">${node.role}</span></td>
            <td><code style="font-size: 12px;">${escapeHtml(node.remote_ip)}</code></td>
            <td><span class="badge badge-online">online</span></td>
            <td>
                ${node.role === 'host' ? `<button class="btn btn-primary btn-sm" onclick="showConnectModal('${escapeHtml(node.network_id)}', '${escapeHtml(node.name)}')">RDP Connect</button>` : '-'}
            </td>
        </tr>
    `).join('');
}

function toggleToken(el, token) {
    if (el.innerText === '••••••••') {
        el.innerText = token;
        el.title = 'Click to mask';
    } else {
        el.innerText = '••••••••';
        el.title = 'Click to reveal';
    }
}

async function showConnectModal(netId, hostName = '') {
    try {
        const url = `/api/connect-snippet?net=${encodeURIComponent(netId)}${hostName ? '&host=' + encodeURIComponent(hostName) : ''}`;
        const res = await fetch(url);
        if (!res.ok) {
            if (res.status === 401) showLogin();
            return;
        }

        const data = await res.json();
        document.getElementById('snippet-host-cmd').innerText = data.host_cmd;
        document.getElementById('snippet-viewer-cmd').innerText = data.viewer_cmd;

        const dlBtn = document.getElementById('snippet-dl-ini');
        dlBtn.onclick = () => {
            const blob = new Blob([data.ini_content], { type: 'text/plain' });
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = 'rdp-agent.ini';
            a.click();
        };

        document.getElementById('modal-connect').classList.add('open');
    } catch (err) {
        console.error(err);
    }
}

function copyCode(id) {
    const text = document.getElementById(id).innerText;
    navigator.clipboard.writeText(text);
    alert('Copied to clipboard!');
}

async function deleteNetwork(id) {
    if (!confirm('Are you sure you want to delete this network? Connected devices will be disconnected.')) return;
    try {
        const res = await fetch(`/api/networks/${encodeURIComponent(id)}`, { method: 'DELETE' });
        if (res.ok) {
            loadDashboard();
        } else if (res.status === 401) {
            showLogin();
        }
    } catch (err) {
        console.error(err);
    }
}

function escapeHtml(text) {
    if (!text) return '';
    return text.toString()
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}
