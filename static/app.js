/* ═══════════════════════════════════════════════════════════════════════
   Route Mobile AI Gateway — Portal Logic (app.js)
   All API interaction, state management, and demo modules.
   ═══════════════════════════════════════════════════════════════════════ */

const API = '/api/v1';

// ── State ──────────────────────────────────────────────────────────────
let accessToken  = null;
let refreshToken = null;
let userEmail    = '';
let adminToken   = null;  // separate admin session for inspector
let isBlacklisted = false;

// ── DOM Helpers ────────────────────────────────────────────────────────
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function showError(msg) {
  const el = $('#auth-error');
  el.textContent = msg;
  el.classList.add('visible');
  setTimeout(() => el.classList.remove('visible'), 5000);
}

function showSuccess(msg) {
  const el = $('#auth-success');
  el.textContent = msg;
  el.classList.add('visible');
  setTimeout(() => el.classList.remove('visible'), 4000);
}

function setLoading(btn, loading) {
  if (loading) {
    btn.classList.add('loading');
    btn.disabled = true;
  } else {
    btn.classList.remove('loading');
    btn.disabled = false;
  }
}

// ── API Calls ──────────────────────────────────────────────────────────
async function apiFetch(path, options = {}) {
  const headers = options.headers || {};
  if (accessToken && !isBlacklisted) {
    headers['Authorization'] = `Bearer ${accessToken}`;
  }
  if (options.json) {
    headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(options.json);
    delete options.json;
  }
  options.headers = headers;

  const start = performance.now();
  const resp  = await fetch(`${API}${path}`, options);
  const elapsed = Math.round(performance.now() - start);

  let data;
  try { data = await resp.json(); } catch { data = null; }
  return { status: resp.status, data, elapsed };
}

async function apiForm(path, formData) {
  const resp = await fetch(`${API}${path}`, {
    method: 'POST',
    body: formData,
  });
  let data;
  try { data = await resp.json(); } catch { data = null; }
  return { status: resp.status, data };
}

// ── JWT Decode (client-side, no verification) ──────────────────────────
function decodeJWT(token) {
  try {
    const parts = token.split('.');
    const payload = JSON.parse(atob(parts[1].replace(/-/g,'+').replace(/_/g,'/')));
    return payload;
  } catch { return null; }
}

// ══════════════════════════════════════════════════════════════════════
//  AUTH
// ══════════════════════════════════════════════════════════════════════

// Tab switching
$$('.auth-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    $$('.auth-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    $$('.auth-form').forEach(f => f.classList.remove('active'));
    $(`#${tab.dataset.tab}-form`).classList.add('active');
    $('#auth-error').classList.remove('visible');
    $('#auth-success').classList.remove('visible');
  });
});

// Register
$('#register-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = $('#register-btn');
  setLoading(btn, true);
  $('#auth-error').classList.remove('visible');

  try {
    const resp = await apiFetch('/auth/register', {
      method: 'POST',
      json: {
        email: $('#register-email').value,
        password: $('#register-password').value,
      }
    });

    if (resp.status === 200) {
      showSuccess(`Account created! Your API key: ${resp.data.api_key?.substring(0, 20)}... — Sign in now.`);
      // Switch to login tab
      $$('.auth-tab').forEach(t => t.classList.remove('active'));
      $('#tab-login').classList.add('active');
      $$('.auth-form').forEach(f => f.classList.remove('active'));
      $('#login-form').classList.add('active');
      $('#login-email').value = $('#register-email').value;
    } else {
      showError(resp.data?.detail || 'Registration failed.');
    }
  } catch (err) {
    showError('Network error. Is the server running?');
  }
  setLoading(btn, false);
});

// Login
$('#login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = $('#login-btn');
  setLoading(btn, true);
  $('#auth-error').classList.remove('visible');

  try {
    const formData = new URLSearchParams();
    formData.append('username', $('#login-email').value);
    formData.append('password', $('#login-password').value);

    const resp = await apiForm('/auth/login', formData);

    if (resp.status === 200) {
      accessToken  = resp.data.access_token;
      refreshToken = resp.data.refresh_token;
      userEmail    = $('#login-email').value;
      isBlacklisted = false;
      enterDashboard();
    } else {
      showError(resp.data?.detail || 'Login failed.');
    }
  } catch (err) {
    showError('Network error. Is the server running?');
  }
  setLoading(btn, false);
});

function enterDashboard() {
  $('#auth-view').style.display = 'none';
  $('#dashboard-view').style.display = 'flex';
  $('#user-email').textContent = userEmail;

  // Try getting admin token for inspector
  obtainAdminToken();
  // Load health tab data
  loadHealth();
}

async function obtainAdminToken() {
  try {
    const formData = new URLSearchParams();
    formData.append('username', 'admin@route.com');
    formData.append('password', 'adminpassword');
    const resp = await apiForm('/auth/login', formData);
    if (resp.status === 200) {
      adminToken = resp.data.access_token;
    }
  } catch {}
}

// Full logout (topbar button)
$('#btn-full-logout').addEventListener('click', async () => {
  try {
    await apiFetch('/auth/logout', { method: 'POST' });
  } catch {}
  accessToken = null;
  refreshToken = null;
  adminToken = null;
  isBlacklisted = false;
  $('#dashboard-view').style.display = 'none';
  $('#auth-view').style.display = 'flex';
});

// ══════════════════════════════════════════════════════════════════════
//  SIDEBAR NAVIGATION
// ══════════════════════════════════════════════════════════════════════
$$('.sidebar-item').forEach(item => {
  item.addEventListener('click', () => {
    $$('.sidebar-item').forEach(i => i.classList.remove('active'));
    item.classList.add('active');
    $$('.tab-panel').forEach(p => p.classList.remove('active'));
    $(`#panel-${item.dataset.panel}`).classList.add('active');
  });
});

// ══════════════════════════════════════════════════════════════════════
//  TAB 1: CACHE DEMO
// ══════════════════════════════════════════════════════════════════════
$('#btn-cache-test').addEventListener('click', async () => {
  const btn = $('#btn-cache-test');
  const prompt = $('#cache-prompt').value.trim();
  if (!prompt) return;

  btn.disabled = true;
  $('#cache-status').textContent = 'Step 1/2: Sending first request (cache MISS)...';
  $('#cache-results').style.display = 'none';
  $('#cache-meta').style.display = 'none';

  // Request 1: Cache MISS
  const r1 = await apiFetch('/chat', { method: 'POST', json: { prompt } });
  if (r1.status !== 200) {
    $('#cache-status').textContent = `Error: ${r1.data?.detail || 'Chat request failed'}`;
    btn.disabled = false;
    return;
  }
  const missTime = r1.elapsed;

  $('#cache-status').textContent = `Step 2/2: Sending same prompt again (cache HIT)...`;

  // Small delay for visual effect
  await new Promise(r => setTimeout(r, 300));

  // Request 2: Cache HIT
  const r2 = await apiFetch('/chat', { method: 'POST', json: { prompt } });
  const hitTime = r2.elapsed;

  // Render results
  $('#cache-results').style.display = 'block';

  const maxTime = Math.max(missTime, hitTime, 1);
  const missHeight = Math.max((missTime / maxTime) * 160, 8);
  const hitHeight  = Math.max((hitTime / maxTime) * 160, 8);

  // Animate bars
  setTimeout(() => {
    $('#bar-miss').style.height = missHeight + 'px';
    $('#bar-hit').style.height  = hitHeight + 'px';
  }, 50);

  $('#val-miss').textContent = missTime + 'ms';
  $('#val-hit').textContent  = hitTime + 'ms';

  // Speedup
  if (missTime > 0 && hitTime > 0) {
    const speedup = (missTime / hitTime).toFixed(1);
    $('#speedup-value').textContent = speedup + '×';
    $('#speedup-badge').style.display = 'block';
  }

  // Cache key meta
  $('#cache-meta').style.display = 'block';
  const hash = await sha256(prompt);
  $('#cache-key-display').innerHTML = `
    <div class="token-field"><span class="token-key">redis_key</span><span class="token-val">cache:${hash}</span></div>
    <div class="token-field"><span class="token-key">miss_time</span><span class="token-val">${missTime}ms (Mistral API)</span></div>
    <div class="token-field"><span class="token-key">hit_time</span><span class="token-val">${hitTime}ms (Redis GET)</span></div>
    <div class="token-field"><span class="token-key">cached</span><span class="token-val">${r2.data?.cached ? 'true ✓' : 'false'}</span></div>
    <div class="token-field"><span class="token-key">model</span><span class="token-val">${r1.data?.model || 'unknown'}</span></div>
    <div class="token-field"><span class="token-key">ttl</span><span class="token-val">600 seconds</span></div>
    <div class="token-field"><span class="token-key">structure</span><span class="token-val">STRING (SETEX/GET)</span></div>
  `;

  $('#cache-status').textContent = `✓ Done — Cache MISS: ${missTime}ms, Cache HIT: ${hitTime}ms`;
  btn.disabled = false;
});

async function sha256(message) {
  const msgBuffer = new TextEncoder().encode(message);
  const hashBuffer = await crypto.subtle.digest('SHA-256', msgBuffer);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
}

// ══════════════════════════════════════════════════════════════════════
//  TAB 2: RATE LIMITER
// ══════════════════════════════════════════════════════════════════════
$('#btn-rate-test').addEventListener('click', async () => {
  const btn = $('#btn-rate-test');
  btn.disabled = true;
  $('#rate-status').textContent = 'Firing 15 rapid requests...';
  $('#rate-summary').style.display = 'none';

  const grid = $('#rate-grid');
  grid.innerHTML = '';

  // Create 15 empty cells
  for (let i = 0; i < 15; i++) {
    const cell = document.createElement('div');
    cell.className = 'rate-cell';
    cell.id = `rate-cell-${i}`;
    cell.textContent = i + 1;
    grid.appendChild(cell);
  }

  let passed = 0, blocked = 0;

  for (let i = 0; i < 15; i++) {
    const resp = await apiFetch('/test/rate-limit', { method: 'GET' });
    const cell = $(`#rate-cell-${i}`);

    if (resp.status === 200) {
      cell.classList.add('ok');
      cell.textContent = '✓';
      passed++;
    } else {
      cell.classList.add('blocked');
      cell.textContent = '✕';
      blocked++;
    }

    $('#rate-status').textContent = `Request ${i + 1}/15 → ${resp.status}`;
  }

  // Summary
  $('#rate-summary').style.display = 'flex';
  $('#rate-passed').textContent = passed;
  $('#rate-blocked').textContent = blocked;
  $('#rate-status').textContent = `✓ Done — ${passed} passed, ${blocked} blocked by Redis sliding window`;
  btn.disabled = false;
});

// ══════════════════════════════════════════════════════════════════════
//  TAB 3: CONTEXT INSPECTOR
// ══════════════════════════════════════════════════════════════════════
const contextMessages = [];

$('#btn-context-send').addEventListener('click', sendContextMessage);
$('#context-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') sendContextMessage();
});

async function sendContextMessage() {
  const input = $('#context-input');
  const msg = input.value.trim();
  if (!msg) return;
  input.value = '';

  // Add user bubble
  addChatBubble('user', msg);

  const btn = $('#btn-context-send');
  btn.disabled = true;

  const resp = await apiFetch('/chat', { method: 'POST', json: { prompt: msg } });

  if (resp.status === 200) {
    addChatBubble('assistant', resp.data.response);
  } else {
    addChatBubble('assistant', `Error: ${resp.data?.detail || 'Request failed'}`);
  }

  btn.disabled = false;

  // Fetch context from Redis
  await refreshContext();
}

function addChatBubble(role, content) {
  const container = $('#chat-container');
  // Clear placeholder
  if (container.querySelector('.text-muted')) {
    container.innerHTML = '';
  }

  const bubble = document.createElement('div');
  bubble.className = `chat-bubble ${role}`;
  bubble.innerHTML = `<div class="chat-role">${role}</div>${escapeHtml(content)}`;
  container.appendChild(bubble);
  container.scrollTop = container.scrollHeight;
}

async function refreshContext() {
  const resp = await apiFetch('/me/context', { method: 'GET' });
  if (resp.status === 200) {
    const data = resp.data;
    $('#ctx-msg-count').textContent = data.message_count;
    $('#ctx-redis-key').textContent = `context:${data.user_id}`;

    // Show raw data
    let rawHtml = '';
    data.messages.forEach((m, i) => {
      const roleColor = m.role === 'user' ? 'var(--accent-cyan)' : 'var(--accent-violet)';
      rawHtml += `<div class="token-field">
        <span class="token-key" style="color:${roleColor}">[${i}] ${m.role}</span>
        <span class="token-val">${escapeHtml(m.content?.substring(0, 80))}${m.content?.length > 80 ? '...' : ''}</span>
      </div>`;
    });
    $('#ctx-raw').innerHTML = rawHtml || '<span class="text-muted">No messages in context</span>';
  }
}

$('#btn-clear-context').addEventListener('click', async () => {
  await apiFetch('/me/context', { method: 'DELETE' });
  $('#chat-container').innerHTML = '<div class="text-muted text-sm text-center" style="padding:2rem;">Context cleared. Send a message to start fresh.</div>';
  $('#ctx-msg-count').textContent = '0';
  $('#ctx-raw').innerHTML = '<span class="text-muted">No messages in context</span>';
});

function escapeHtml(str) {
  if (!str) return '';
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// ══════════════════════════════════════════════════════════════════════
//  TAB 4: TOKEN LIFECYCLE
// ══════════════════════════════════════════════════════════════════════
function renderJWT() {
  if (!accessToken) {
    $('#jwt-display').innerHTML = '<span class="text-muted">No active token</span>';
    return;
  }

  const payload = decodeJWT(accessToken);
  if (!payload) {
    $('#jwt-display').textContent = accessToken;
    return;
  }

  let html = '';
  for (const [key, val] of Object.entries(payload)) {
    let displayVal = val;
    if (key === 'exp' || key === 'iat') {
      displayVal = `${val} (${new Date(val * 1000).toLocaleTimeString()})`;
    }
    html += `<div class="token-field"><span class="token-key">${key}</span><span class="token-val">${displayVal}</span></div>`;
  }
  html += `<div class="token-field"><span class="token-key">raw (first 60)</span><span class="token-val">${accessToken.substring(0, 60)}...</span></div>`;
  $('#jwt-display').innerHTML = html;
}

// Observe when token tab becomes visible
const tokenPanelObserver = new MutationObserver(() => {
  if ($('#panel-token').classList.contains('active')) {
    renderJWT();
  }
});
tokenPanelObserver.observe($('#panel-token'), { attributes: true, attributeFilter: ['class'] });

// Also render when sidebar item clicked
$$('.sidebar-item').forEach(item => {
  item.addEventListener('click', () => {
    if (item.dataset.panel === 'token') setTimeout(renderJWT, 50);
    if (item.dataset.panel === 'inspector') setTimeout(loadInspector, 50);
    if (item.dataset.panel === 'health') setTimeout(loadHealth, 50);
  });
});

function addLifecycleLog(status, message) {
  const log = $('#lifecycle-log');
  // Clear placeholder
  if (log.querySelector('.text-muted')) log.innerHTML = '';

  const time = new Date().toLocaleTimeString();
  const statusClass = `s${status}`;
  const entry = document.createElement('div');
  entry.className = 'log-entry';
  entry.innerHTML = `
    <span class="log-time">${time}</span>
    <span class="log-status ${statusClass}">${status}</span>
    <span>${escapeHtml(message)}</span>
  `;
  log.prepend(entry);
}

$('#btn-verify-token').addEventListener('click', async () => {
  const resp = await apiFetch('/auth/check', { method: 'GET' });
  addLifecycleLog(resp.status, resp.status === 200
    ? `Token valid. user_id=${resp.data?.user_id}`
    : `Token invalid: ${resp.data?.detail || 'unknown'}`
  );
});

$('#btn-blacklist-token').addEventListener('click', async () => {
  if (!accessToken) return;
  const resp = await apiFetch('/auth/logout', { method: 'POST' });
  isBlacklisted = true;
  addLifecycleLog(resp.status, resp.status === 200
    ? 'Token blacklisted in Redis (SETEX blacklist:<jwt> <ttl> "revoked")'
    : `Logout failed: ${resp.data?.detail || 'unknown'}`
  );
  renderJWT();
});

$('#btn-check-blacklisted').addEventListener('click', async () => {
  // Use the blacklisted token
  const resp = await fetch(`${API}/auth/check`, {
    headers: { 'Authorization': `Bearer ${accessToken}` }
  });
  const data = await resp.json().catch(() => null);
  addLifecycleLog(resp.status, resp.status === 401
    ? `Correctly rejected: ${data?.detail} — Redis blacklist working!`
    : `Unexpected: status=${resp.status}`
  );
});

// ══════════════════════════════════════════════════════════════════════
//  TAB 5: REDIS INSPECTOR
// ══════════════════════════════════════════════════════════════════════
async function loadInspector() {
  const token = adminToken || accessToken;
  if (!token) return;

  const headers = { 'Authorization': `Bearer ${token}` };

  // Key distribution
  try {
    const resp = await fetch(`${API}/admin/redis/keys`, { headers });
    if (resp.status === 200) {
      const data = await resp.json();
      const grid = $('#key-grid');
      const keyTypes = [
        { key: 'session_keys', label: 'Sessions', icon: '👤' },
        { key: 'cache_keys', label: 'Cache', icon: '⚡' },
        { key: 'rate_limit_keys', label: 'Rate Limits', icon: '🛡️' },
        { key: 'quota_keys', label: 'Quotas', icon: '📊' },
        { key: 'blacklist_keys', label: 'Blacklist', icon: '🔒' },
        { key: 'stats_keys', label: 'Stats', icon: '📈' },
        { key: 'other_keys', label: 'Other', icon: '📦' },
        { key: 'total_keys', label: 'Total', icon: '∑' },
      ];
      grid.innerHTML = keyTypes.map(t => `
        <div class="key-card">
          <div style="font-size:1.5rem;margin-bottom:0.375rem;">${t.icon}</div>
          <div class="key-count">${data[t.key] ?? 0}</div>
          <div class="key-label">${t.label}</div>
        </div>
      `).join('');
    }
  } catch {}

  // Cache stats
  try {
    const resp = await fetch(`${API}/admin/cache/stats`, { headers });
    if (resp.status === 200) {
      const data = await resp.json();
      $('#stat-hits').textContent = data.hits ?? 0;
      $('#stat-misses').textContent = data.misses ?? 0;
      $('#stat-ratio').textContent = data.hit_ratio ?? '0%';
    }
  } catch {}

  // Rate limit keys
  try {
    const resp = await fetch(`${API}/admin/redis/rate-limit`, { headers });
    if (resp.status === 200) {
      const data = await resp.json();
      $('#stat-rate-active').textContent = data.active_keys ?? 0;
    }
  } catch {}
}

$('#btn-refresh-inspector').addEventListener('click', loadInspector);

// ══════════════════════════════════════════════════════════════════════
//  TAB 6: SYSTEM HEALTH
// ══════════════════════════════════════════════════════════════════════
async function loadHealth() {
  // Redis health
  try {
    const resp = await apiFetch('/health/redis', { method: 'GET' });
    if (resp.status === 200) {
      $('#health-badge').innerHTML = '<span class="badge badge-green"><span class="status-dot green"></span>Connected</span>';
      $('#health-grid').innerHTML = `
        <div class="health-item">
          <div class="health-label">Status</div>
          <div class="health-value" style="color:var(--accent-green)">Connected</div>
        </div>
        <div class="health-item">
          <div class="health-label">Ping Latency</div>
          <div class="health-value">${resp.elapsed}ms</div>
        </div>
        <div class="health-item">
          <div class="health-label">Endpoint</div>
          <div class="health-value">/health/redis</div>
        </div>
      `;
    } else {
      $('#health-badge').innerHTML = '<span class="badge badge-red"><span class="status-dot red"></span>Disconnected</span>';
    }
  } catch {
    $('#health-badge').innerHTML = '<span class="badge badge-red"><span class="status-dot red"></span>Error</span>';
  }

  // Session info
  try {
    const resp = await apiFetch('/me/session', { method: 'GET' });
    if (resp.status === 200) {
      let html = '';
      for (const [key, val] of Object.entries(resp.data)) {
        let displayVal = val;
        if (key === 'login_time' || key === 'expires') {
          displayVal = `${val} (${new Date(parseInt(val) * 1000).toLocaleTimeString()})`;
        }
        html += `<div class="token-field"><span class="token-key">${key}</span><span class="token-val">${displayVal}</span></div>`;
      }
      $('#session-display').innerHTML = html;
    } else {
      $('#session-display').innerHTML = '<span class="text-muted">No session data</span>';
    }
  } catch {
    $('#session-display').innerHTML = '<span class="text-muted">Failed to load session</span>';
  }
}
