// Глобальное состояние приложения и хелперы
const { reactive } = Vue;

export const store = reactive({
  user: null,
  claims: null,          // расшифрованный payload JWT — источник прав для UI
  teams: [],
  projects: [],
  notifications: [],
  unread: 0,
  toasts: [],
  online: false,
  connectionId: null,    // id SSE-соединения (для presence)
  crumbs: [],            // «хлебные крошки» в шапке
  route: { name: '', params: {}, query: {} },
});

// ---------- права (читаются прямо из JWT) ----------
export const isAdmin = () => !!store.claims?.sys?.includes('sys:admin');

export function can(perm, projectId) {
  if (!store.claims) return false;
  if (isAdmin()) return true;
  return !!store.claims.projects?.[projectId]?.perms.includes(perm);
}

export function canTeam(perm, teamId) {
  if (!store.claims) return false;
  if (isAdmin()) return true;
  return !!store.claims.teams?.[teamId]?.perms.includes(perm);
}

export const projectRole = (pid) => store.claims?.projects?.[pid]?.role || (isAdmin() ? 'admin' : null);
export const teamRole = (tid) => store.claims?.teams?.[tid]?.role || null;

// ---------- шина событий realtime ----------
const handlers = new Set();
export const bus = {
  on(fn) { handlers.add(fn); return () => handlers.delete(fn); },
  emit(ev) { handlers.forEach((fn) => { try { fn(ev); } catch (e) { console.error(e); } }); },
};

// ---------- уведомления-тосты ----------
let toastId = 0;
export function toast(text, type = 'info', ms = 3500) {
  const id = ++toastId;
  store.toasts.push({ id, text, type });
  setTimeout(() => {
    const i = store.toasts.findIndex((t) => t.id === id);
    if (i >= 0) store.toasts.splice(i, 1);
  }, ms);
}
export const toastError = (e) => toast(e.message || String(e), 'error', 5000);

// ---------- роутер (hash) ----------
export function parseHash() {
  const raw = location.hash.replace(/^#/, '') || '/';
  const [path, qs] = raw.split('?');
  const query = Object.fromEntries(new URLSearchParams(qs || ''));
  const parts = path.split('/').filter(Boolean);
  const routes = {
    '': 'dashboard', team: 'team', project: 'project', board: 'board', my: 'my',
    admin: 'admin', token: 'token', profile: 'profile', settings: 'settings', security: 'security',
  };
  const name = routes[parts[0] || ''] || 'dashboard';
  return { name, params: { id: parts[1] ? Number(parts[1]) : null, tab: parts[2] || null }, query };
}
export const go = (hash) => { location.hash = hash; };
export function setQuery(patch) {
  const r = parseHash();
  const q = { ...r.query, ...patch };
  Object.keys(q).forEach((k) => (q[k] === null || q[k] === undefined) && delete q[k]);
  const base = location.hash.replace(/^#/, '').split('?')[0] || '/';
  const qs = new URLSearchParams(q).toString();
  history.replaceState(null, '', `#${base}${qs ? `?${qs}` : ''}`);
  store.route = parseHash();
}

// ---------- форматирование ----------
export const ROLE_RU = {
  owner: 'Владелец', admin: 'Администратор', member: 'Участник', manager: 'Менеджер', viewer: 'Наблюдатель', user: 'Пользователь',
};
export const PRIORITY_RU = { low: 'Низкий', medium: 'Средний', high: 'Высокий', critical: 'Критический' };

export function fmtDate(d) {
  if (!d) return '';
  return new Date(d.length === 10 ? `${d}T00:00:00` : `${d}Z`).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
}
export function fmtDateTime(d) {
  if (!d) return '';
  return new Date(`${d}Z`).toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}
export function ago(d) {
  if (!d) return '';
  const s = (Date.now() - new Date(`${d}Z`).getTime()) / 1000;
  if (s < 45) return 'только что';
  if (s < 3600) return `${Math.round(s / 60)} мин назад`;
  if (s < 86400) return `${Math.round(s / 3600)} ч назад`;
  if (s < 86400 * 7) return `${Math.round(s / 86400)} дн назад`;
  return fmtDateTime(d);
}
export function dueState(due, done) {
  if (!due || done) return '';
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const d = new Date(`${due}T00:00:00`);
  const diff = (d - today) / 86400000;
  if (diff < 0) return 'overdue';
  if (diff <= 2) return 'soon';
  return '';
}
export const userName = (u) => (u ? (u.full_name || u.username) : '—');
export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
export const highlightMentions = (text) => escapeHtml(text).replace(/@([a-zA-Z0-9_.-]+)/g, '<span class="mention">@$1</span>');
