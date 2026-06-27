// =============================================================================
// OKR Manager — JS común (auth + helpers + topbar + supabase client)
// =============================================================================

(function () {
  'use strict';

  // ─── Supabase client ──────────────────────────────────────────────────
  const cfg = window.OKR_CONFIG || {};
  if (!cfg.supabase_url || !cfg.supabase_anon || cfg.supabase_anon.startsWith('REPLACE')) {
    console.warn('[OKR] Supabase no configurado en config.js');
  }
  const sb = window.supabase && window.supabase.createClient
    ? window.supabase.createClient(cfg.supabase_url, cfg.supabase_anon, {
        auth: { flowType: 'implicit' }
      })
    : null;
  window.OKR_SB = sb;

  // ─── Toast helper ─────────────────────────────────────────────────────
  function ensureToastStack() {
    let s = document.querySelector('.toast-stack');
    if (!s) {
      s = document.createElement('div');
      s.className = 'toast-stack';
      document.body.appendChild(s);
    }
    return s;
  }
  function toast(msg, kind) {
    const stack = ensureToastStack();
    const el = document.createElement('div');
    el.className = 'toast ' + (kind || '');
    el.textContent = msg;
    stack.appendChild(el);
    setTimeout(() => el.remove(), 4500);
  }
  window.OKR_toast = toast;

  // ─── Date helpers ─────────────────────────────────────────────────────
  function timeAgo(ts) {
    const d = new Date(ts);
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return 'hace segundos';
    if (diff < 3600) return `hace ${Math.floor(diff / 60)} min`;
    if (diff < 86400) return `hace ${Math.floor(diff / 3600)} h`;
    if (diff < 604800) return `hace ${Math.floor(diff / 86400)} d`;
    return d.toLocaleDateString('es-ES', { day: '2-digit', month: 'short', year: 'numeric' });
  }
  function fmtDate(d) {
    return new Date(d).toLocaleDateString('es-ES', { day: '2-digit', month: 'short' });
  }
  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  window.OKR_fmt = { timeAgo, fmtDate, escapeHtml };

  // ─── Auth: magic link flow ────────────────────────────────────────────
  // - getUser() devuelve el user actual (o null)
  // - isApprover() true si el email coincide con cfg.approver_email
  // - signIn() abre modal pidiendo email + manda magic link
  // - signOut() cierra sesión

  async function getUser() {
    if (!sb) return null;
    // Leer directo de localStorage para evitar llamadas de red colgadas
    const storageKey = `sb-${cfg.supabase_url.split('//')[1].split('.')[0]}-auth-token`;
    try {
      const raw = localStorage.getItem(storageKey);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      const user = parsed?.user ?? parsed?.session?.user ?? null;
      return user;
    } catch (e) {
      return null;
    }
  }

  async function isApprover() {
    const u = await getUser();
    return !!(u && u.email && u.email.toLowerCase() === (cfg.approver_email || '').toLowerCase());
  }

  function buildAuthModal() {
    const wrap = document.createElement('div');
    wrap.className = 'modal-bg';
    wrap.id = 'okr-auth-modal';
    wrap.innerHTML = `
      <div class="modal">
        <h2>Iniciar sesión</h2>
        <label for="okr-auth-email">Email</label>
        <input id="okr-auth-email" type="email" placeholder="${escapeHtml(cfg.approver_email || '')}" value="${escapeHtml(cfg.approver_email || '')}">
        <label for="okr-auth-pass" style="margin-top:10px;">Contraseña</label>
        <input id="okr-auth-pass" type="password" placeholder="••••••••">
        <div class="actions" style="margin-top:14px;">
          <button class="btn btn-ghost" data-act="cancel">Cancelar</button>
          <button class="btn btn-primary" data-act="send">Entrar</button>
        </div>
      </div>
    `;
    document.body.appendChild(wrap);

    wrap.querySelector('[data-act="cancel"]').addEventListener('click', () => {
      wrap.classList.remove('open');
    });
    wrap.querySelector('[data-act="send"]').addEventListener('click', async () => {
      const email = wrap.querySelector('#okr-auth-email').value.trim().toLowerCase();
      const pass  = wrap.querySelector('#okr-auth-pass').value;
      if (!email || !pass) return;
      const btn = wrap.querySelector('[data-act="send"]');
      btn.disabled = true; btn.textContent = 'Entrando...';
      const { error } = await sb.auth.signInWithPassword({ email, password: pass });
      btn.disabled = false; btn.textContent = 'Entrar';
      if (error) {
        toast('Error: ' + error.message, 'error');
        return;
      }
      wrap.classList.remove('open');
      location.reload();
    });
    wrap.querySelector('#okr-auth-pass').addEventListener('keydown', e => {
      if (e.key === 'Enter') wrap.querySelector('[data-act="send"]').click();
    });
    return wrap;
  }

  async function signIn() {
    let modal = document.getElementById('okr-auth-modal');
    if (!modal) modal = buildAuthModal();
    modal.classList.add('open');
  }

  async function signOut() {
    if (!sb) return;
    await sb.auth.signOut();
    toast('Sesión cerrada', 'success');
    setTimeout(() => location.reload(), 600);
  }

  window.OKR_auth = { getUser, isApprover, signIn, signOut };

  // ─── Topbar render ────────────────────────────────────────────────────
  async function renderTopbar(activeKey) {
    const bar = document.querySelector('.topbar');
    if (!bar) return;
    const links = [
      { key: 'dashboard',    label: 'Dashboard',           href: 'dashboard.html' },
      { key: 'monday',       label: 'Iniciativas planteadas',  href: 'approval-monday.html' },
      { key: 'friday',       label: 'Valores KR a aprobar',   href: 'kr-proposals-friday.html' },
      { key: 'market-intel', label: '🔍 Visión del Mercado', href: 'market-intel.html' },
      { key: 'content',      label: '✦ Contenido Semanal',  href: 'content-queue.html' },
    ];
    const navHtml = links.map(l =>
      `<a href="${l.href}" ${activeKey === l.key ? 'class="active"' : ''}>${l.label}</a>`
    ).join('');
    bar.innerHTML = `
      <div class="brand">OKR Manager <span class="sub">${escapeHtml((cfg.team || '').toUpperCase())}</span></div>
      <nav>${navHtml}</nav>
      <div class="auth" id="okr-auth-slot">
        <span class="muted">Cargando...</span>
      </div>
    `;
    refreshAuthSlot();
  }

  async function refreshAuthSlot() {
    const slot = document.getElementById('okr-auth-slot');
    if (!slot) return;
    const u = await getUser();
    if (u && u.email) {
      const isApp = u.email.toLowerCase() === (cfg.approver_email || '').toLowerCase();
      slot.innerHTML = `
        <span>${escapeHtml(u.email)}${isApp ? ' · aprobador' : ''}</span>
        <button data-act="signout">Salir</button>
      `;
      slot.querySelector('[data-act="signout"]').addEventListener('click', signOut);
    } else {
      slot.innerHTML = `<button data-act="signin">Iniciar sesión</button>`;
      slot.querySelector('[data-act="signin"]').addEventListener('click', signIn);
    }
  }

  if (sb) {
    sb.auth.onAuthStateChange(() => refreshAuthSlot());
  }

  window.OKR_renderTopbar = renderTopbar;
})();

// ─── GitHub Actions trigger ───────────────────────────────────────────────────
// Reemplaza las llamadas a localhost:8081 — dispara un workflow_dispatch en GitHub
// job: 'monday' | 'friday' | 'generate_backlog' | 'daily_sweep' | 'writeback' | 'market_intel'
// inputs: objeto opcional, ej. { next_week: 'true' }
window.OKR_triggerJob = async function(job, inputs) {
  const cfg   = window.OKR_CONFIG || {};
  const token = cfg.github_token;
  const repo  = cfg.github_repo;

  if (!token || token === 'REEMPLAZAR_CON_TOKEN') {
    console.warn('[OKR] github_token no configurado en config.js');
    return { ok: false, error: 'token_missing' };
  }

  const body = {
    ref: 'main',
    inputs: Object.assign({ job }, inputs || {}),
  };

  try {
    const res = await fetch(
      `https://api.github.com/repos/${repo}/actions/workflows/cron.yml/dispatches`,
      {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Accept':        'application/vnd.github+json',
          'Content-Type':  'application/json',
        },
        body: JSON.stringify(body),
      }
    );
    // 204 = disparado OK; cualquier otro código es error
    return { ok: res.status === 204, status: res.status };
  } catch(e) {
    return { ok: false, error: e.message };
  }
};
