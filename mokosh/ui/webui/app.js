// mokosh UI — vanilla JS, no build step. Strings of the interface come from t() (i18n.js);
// paths, task keys, yaml and server messages are data and are never translated.
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}
const errHtml = e => `<span class="err">${esc(e.message)}</span>`;

// ── tabs ─────────────────────────────────────────────
function showTab(name) {
  document.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.panel').forEach(p => p.classList.toggle('active', p.id === 'tab-' + name));
  const u = new URL(location.href);
  u.searchParams.set('tab', name);
  history.replaceState(null, '', u);
  if (name === 'status' && folder()) runStatus();
}
document.querySelectorAll('.tab').forEach(b => b.onclick = () => showTab(b.dataset.tab));

// ── theme & language ─────────────────────────────────
const THEMES = ['mokosh', 'ocean', 'dark', 'light', 'soft', 'summer', 'beach', 'mono-dark', 'mono-light'];
const themeTitle = n => n === 'mokosh' ? t('theme_auto') : n.split('-').map(w => w[0].toUpperCase() + w.slice(1)).join(' ');
function setTheme(name) {
  const link = $('theme-link');
  if (name === 'mokosh') { link.disabled = true; link.removeAttribute('href'); document.documentElement.removeAttribute('data-theme'); }
  else { link.href = `/ui/themes/${name}.css`; link.disabled = false; document.documentElement.setAttribute('data-theme', name); }
  try { localStorage.setItem('mokosh-theme', name === 'mokosh' ? '' : name); } catch {}
  $('theme').value = name;
}
function fillThemes() {
  let cur = '';
  try { cur = localStorage.getItem('mokosh-theme') || ''; } catch {}
  $('theme').innerHTML = THEMES.map(n => `<option value="${n}">${esc(themeTitle(n))}</option>`).join('');
  $('theme').value = THEMES.includes(cur) ? cur : 'mokosh';
}
fillThemes();
$('theme').onchange = () => setTheme($('theme').value);
$('lang').innerHTML = LANGS.map(([c, label]) => `<option value="${c}">${esc(label)}</option>`).join('');
$('lang').value = LANG;
$('lang').onchange = () => setLang($('lang').value);

// ── projects ─────────────────────────────────────────
let projects = [], startDir = '';
const folder = () => $('project').value;
const info = () => projects.find(p => p.folder === folder());

async function loadProjects() {
  const data = await api('/api/projects');
  projects = data.projects;
  startDir = data.start || '';
  renderProjects();
}
function renderProjects() {
  let keep = $('project').value;
  if (!keep) { try { keep = localStorage.getItem('mokosh-project') || ''; } catch {} }
  $('project').innerHTML = `<option value="">${esc(t('select_project'))}</option>` +
    projects.map(p => `<option value="${esc(p.folder)}">${esc(p.name)}</option>`).join('');
  $('project').value = projects.some(p => p.folder === keep) ? keep : (projects.length === 1 ? projects[0].folder : '');
  renderScope();
}
function renderScope() {
  const p = info();
  try { localStorage.setItem('mokosh-project', folder()); } catch {}
  $('scope').innerHTML = p ? `<code>${esc(p.folder)}</code> · ${esc(p.tracker || 'git')} · ${p.db_exists ? `<code>${esc(p.db)}</code>` : esc(t('no_db'))}` : '';
  if (!$('n-folder').value || projects.some(x => x.folder === $('n-folder').value)) $('n-folder').value = p ? p.folder : startDir;
}
$('project').onchange = () => { renderScope(); lastMasks = null; if (!$('n-maskbox').hidden) tryMasks(); };
$('refresh').onclick = () => loadProjects().catch(e => { $('scope').innerHTML = errHtml(e); });

// ── setup: project.yaml ──────────────────────────────
$('n-tracker').onchange = () => {
  const trk = $('n-tracker').value;
  document.querySelectorAll('.trk').forEach(d => { d.hidden = d.dataset.trk !== trk; });
  $('n-maskbox').hidden = trk === 'none';
  if (trk !== 'none') {
    if (!maskTouched) $('n-mask').value = ['github', 'gitlab'].includes(trk) ? 'generic' : 'simple';
    if (!lastMasks) tryMasks(); else drawMasks();
  }
};
// commit mask: the presets explained right away, each tried on the repository's last commits
let lastMasks = null, maskTouched = false;
const MASKS = ['simple', 'bracketed', 'django', 'generic', 'broad', 'regex-key'];
// a regex of your own — examples shown under the presets (click one to use it)
const RX = [['(OFBIZ-\\d+)', 'mask_rx_one'], ['((?:ABC|ABD|ABK)-\\d+)', 'mask_rx_many'], ['\\b((?:ABC|ABD|ABK)-\\d+)\\b', 'mask_rx_word']];
// the project key(s) regex-key is built from: Jira's or YouTrack's project field
const trackerKeys = () => ({ jira: $('n-jira_project').value, youtrack: $('n-yt_project').value })[$('n-tracker').value] || '';
async function tryMasks() {
  $('n-mask-status').textContent = t('computing');
  try {
    const mask = $('n-mask').value.trim();
    lastMasks = await api('/api/mask_preview', { folder: $('n-folder').value.trim(), repo: $('n-repo').value.trim(),
      branch: $('n-branch').value.trim(), mask: MASKS.includes(mask) ? '' : mask, keys: trackerKeys() });
    const best = lastMasks.rows.reduce((a, r) => (r.linked > (a ? a.linked : 0) ? r : a), null);
    if (best && !maskTouched) $('n-mask').value = best.mask;
    drawMasks();
  } catch (e) { $('n-mask-status').innerHTML = errHtml(e); }
}
function drawMasks() {
  const res = lastMasks || { rows: [], help: MASKS.map(m => ({ mask: m, example: '' })), total: 0 };
  const byMask = Object.fromEntries(res.rows.map(r => [r.mask, r]));
  $('n-mask-status').textContent = res.total ? t('mask_tried', { n: res.total, repo: res.repo }) : (lastMasks ? t('mask_no_git', { repo: res.repo }) : '');
  const cur = $('n-mask').value.trim();
  const names = [...MASKS, ...res.rows.map(r => r.mask).filter(m => !MASKS.includes(m))];
  const ex = Object.fromEntries((res.help || []).map(h => [h.mask, h.example]));
  $('n-masks').innerHTML = `<tr><th>${esc(t('th_mask'))}</th><th>${esc(t('th_finds'))}</th><th>${esc(t('th_example'))}</th>${res.total ? `<th>${esc(t('th_on_repo'))}</th>` : ''}</tr>` +
    names.map(m => { const r = byMask[m];
      return `<tr class="${m === cur ? 'sel' : ''}" data-mask="${esc(m)}" title="${esc(t('mask_pick'))}"><td><code>${esc(m)}</code></td>
        <td style="text-align:left">${esc(MASKS.includes(m) ? t('mask_' + m) : t('mask_own'))}</td>
        <td style="text-align:left">${ex[m] ? `<code>${esc(ex[m])}</code>` : ''}</td>
        ${res.total ? `<td style="text-align:left">${r ? `<b>${r.linked}</b> / ${r.total} (${Math.round(100 * r.linked / r.total)}%) <span class="dim">${esc(r.examples.join(', '))}</span>` : ''}</td>` : ''}</tr>`; }).join('');
  $('n-rx').innerHTML = RX.map(([rx, k]) => `<tr data-mask="${esc(rx)}" title="${esc(t('mask_pick'))}" class="${rx === cur ? 'sel' : ''}"><td style="text-align:left"><code>${esc(rx)}</code></td><td style="text-align:left">${esc(t(k))}</td></tr>`).join('');
  document.querySelectorAll('#n-masks tr[data-mask], #n-rx tr[data-mask]').forEach(tr => tr.onclick = () => { $('n-mask').value = tr.dataset.mask; maskTouched = true; drawMasks(); });
}
$('n-mask').oninput = () => { maskTouched = true; drawMasks(); };
// a new project key changes what regex-key finds; another folder or repository other commits: try again
['n-jira_project', 'n-yt_project', 'n-folder', 'n-repo', 'n-branch'].forEach(id => {
  $(id).onchange = () => { if (!$('n-maskbox').hidden) tryMasks(); };
});
$('n-try').onclick = tryMasks;
$('n-go').onclick = async () => {
  const form = { tracker: $('n-tracker').value };
  document.querySelectorAll('#tab-setup input[id^="n-"], #tab-setup select[id^="n-"]').forEach(i => {
    if (i.type !== 'checkbox') form[i.id.slice(2)] = i.value;
  });
  $('n-status').textContent = '';
  try {
    const r = await api('/api/init', { folder: $('n-folder').value.trim(), overwrite: $('n-overwrite').checked, form });
    $('n-out').hidden = false;
    $('n-out').textContent = r.yaml + ((r.notes || []).length ? '\n# ' + r.notes.join('\n# ') : '');   // where a token went
    $('n-status').textContent = t('init_done', { store: r.store });
    await loadProjects();
    $('project').value = r.folder; renderScope();
  } catch (e) { $('n-status').innerHTML = errHtml(e); }
};

// ── ingest ───────────────────────────────────────────
async function follow(jid) {
  const log = $('job-log');
  log.hidden = false;
  for (;;) {
    const j = await api('/api/jobs/' + jid);
    log.textContent = j.log.join('\n') + (j.error ? '\n' + j.error : '');
    log.scrollTop = log.scrollHeight;
    if (j.status !== 'running') {
      $('i-status').innerHTML = j.status === 'done' ? esc(t('job_done')) : `<span class="err">${esc(j.error || j.status)}</span>`;
      $('i-go').disabled = false;
      await loadProjects();
      return;
    }
    await new Promise(r => setTimeout(r, 1000));
  }
}
$('i-go').onclick = async () => {
  if (!folder()) { $('i-status').textContent = t('choose_project_first'); return; }
  $('i-status').textContent = t('running');
  $('i-go').disabled = true;
  try {
    const r = await api('/api/ingest', { folder: folder(), phase: $('i-phase').value, force: $('i-force').checked });
    await follow(r.job);
  } catch (e) { $('i-status').innerHTML = errHtml(e); $('i-go').disabled = false; }
};

// ── status ───────────────────────────────────────────
let lastStatus = null;
async function runStatus() {
  if (!folder()) { $('st-status').textContent = t('choose_project_first'); return; }
  $('st-status').textContent = t('computing');
  try { lastStatus = await api('/api/status', { folder: folder() }); drawStatus(); }
  catch (e) { $('st-status').innerHTML = errHtml(e); }
}
function drawStatus() {
  const r = lastStatus;
  $('st-status').textContent = '';
  const kpis = r.stats ? `<div class="kpis">${Object.entries(r.stats).filter(([k]) => k !== 'db')
    .map(([k, v]) => `<span>${esc(k)} <b>${esc(typeof v === 'number' ? v.toLocaleString() : (v ?? ''))}</b></span>`).join('')}</div>`
    : `<p class="dim">${esc(t('no_db'))}</p>`;
  $('st-out').innerHTML = `<h2>${esc(t('st_db'))}</h2><p><code>${esc(r.db)}</code></p>${kpis}
    <h2>project.yaml</h2>${r.yaml ? `<pre class="out">${esc(r.yaml)}</pre>` : `<p class="dim">${esc(t('no_yaml'))}</p>`}
    <h2>ingest_state.yaml</h2>${r.state ? `<pre class="out">${esc(r.state)}</pre>` : `<p class="dim">${esc(t('no_state'))}</p>`}`;
}
$('st-go').onclick = runStatus;

// a new language: redraw what JS drew, from the answers in hand
langHooks.push(() => {
  fillThemes();
  renderProjects();
  if (!$('n-maskbox').hidden) drawMasks();
  if (lastStatus && $('st-out').innerHTML) drawStatus();
});

// ?tab=…&folder=… open straight to a view
loadProjects().then(() => {
  const u = new URLSearchParams(location.search);
  if (u.get('folder') && projects.some(p => p.folder === u.get('folder'))) { $('project').value = u.get('folder'); renderScope(); }
  showTab(['setup', 'ingest', 'status'].includes(u.get('tab')) ? u.get('tab') : 'setup');
}).catch(e => { $('scope').innerHTML = errHtml(e); });
