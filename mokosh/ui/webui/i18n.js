// mokosh UI — translations of the interface's own strings (the same languages as vyrii).
// Data is never translated: paths, terms, relations, task titles and server messages stay as
// they are. Each language is ui/lang/<code>.js filling I18N[code]; a missing key falls back to
// English, then to the key itself.
const I18N = {};
const LANGS = [['en', 'EN — English'], ['uk', 'UK — Українська'], ['de', 'DE — Deutsch'],
               ['fr', 'FR — Français'], ['es', 'ES — Español'], ['pt', 'PT — Português']];
let LANG = 'en';
try {
  const q = new URLSearchParams(location.search).get('lang');
  LANG = q || localStorage.getItem('mokosh-lang') || 'en';
  if (q) localStorage.setItem('mokosh-lang', q);
} catch (e) {}
if (!LANGS.some(([c]) => c === LANG)) LANG = 'en';

// t('key') or t('key', {n: 3}) — {n} in the string is replaced; callers escape what they pass
function t(key, vars) {
  let s = (I18N[LANG] && I18N[LANG][key]) ?? (I18N.en && I18N.en[key]) ?? key;
  if (vars) s = s.replace(/\{(\w+)\}/g, (m, k) => (vars[k] ?? m));
  return s;
}

// static strings of the page: data-i18n (text), data-i18n-html (text with markup),
// data-i18n-ph (placeholder), data-i18n-title (tooltip)
function applyLang() {
  document.documentElement.lang = LANG;
  document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll('[data-i18n-html]').forEach(el => { el.innerHTML = t(el.dataset.i18nHtml); });
  document.querySelectorAll('[data-i18n-ph]').forEach(el => { el.placeholder = t(el.dataset.i18nPh); });
  document.querySelectorAll('[data-i18n-title]').forEach(el => { el.title = t(el.dataset.i18nTitle); });
}

const langHooks = [];          // views that draw strings from JS redraw themselves on a change
function setLang(code) {
  LANG = LANGS.some(([c]) => c === code) ? code : 'en';
  try { localStorage.setItem('mokosh-lang', LANG); } catch (e) {}
  applyLang();
  langHooks.forEach(f => { try { f(); } catch (e) { console.error(e); } });
}
