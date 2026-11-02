// HTTP-клиент, работа с JWT и realtime-подключение (SSE)

const LS_ACCESS = 'kb_access';
const LS_REFRESH = 'kb_refresh';

export const auth = {
  access: localStorage.getItem(LS_ACCESS),
  refresh: localStorage.getItem(LS_REFRESH),
};

const tokenListeners = new Set();
export const onTokenChange = (fn) => { tokenListeners.add(fn); return () => tokenListeners.delete(fn); };
let logoutHandler = () => {};
export const setLogoutHandler = (fn) => { logoutHandler = fn; };

export function decodeJwt(token) {
  if (!token) return null;
  try {
    let b64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    b64 += '='.repeat((4 - b64.length % 4) % 4);
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    return JSON.parse(new TextDecoder().decode(bytes));
  } catch { return null; }
}

export function setTokens(data) {
  auth.access = data?.access_token || null;
  auth.refresh = data?.refresh_token || null;
  if (auth.access) {
    localStorage.setItem(LS_ACCESS, auth.access);
    localStorage.setItem(LS_REFRESH, auth.refresh);
  } else {
    localStorage.removeItem(LS_ACCESS);
    localStorage.removeItem(LS_REFRESH);
  }
  tokenListeners.forEach((fn) => fn(auth.access));
}

// синхронизация токенов между вкладками
window.addEventListener('storage', (e) => {
  if (e.key === LS_ACCESS && e.newValue && e.newValue !== auth.access) {
    auth.access = e.newValue;
    auth.refresh = localStorage.getItem(LS_REFRESH);
    tokenListeners.forEach((fn) => fn(auth.access));
  }
});

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

function detailToText(detail) {
  if (!detail) return 'Ошибка запроса';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map((d) => `${(d.loc || []).slice(1).join('.')}: ${d.msg}`).join('; ');
  return JSON.stringify(detail);
}

let refreshing = null;
// Получить новый access-токен (с актуальными правами). Одновременные вызовы склеиваются.
export function refreshTokens() {
  if (!refreshing) {
    const used = auth.refresh;
    refreshing = fetch('/api/auth/refresh', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: used }),
    }).then(async (r) => {
      if (!r.ok) {
        // другая вкладка могла уже обновить токен
        const stored = localStorage.getItem(LS_REFRESH);
        if (stored && stored !== used) {
          auth.access = localStorage.getItem(LS_ACCESS); auth.refresh = stored;
          tokenListeners.forEach((fn) => fn(auth.access));
          return;
        }
        throw new ApiError(401, 'refresh_invalid');
      }
      setTokens(await r.json());
    }).finally(() => { refreshing = null; });
  }
  return refreshing;
}

const RETRYABLE = ['token_outdated', 'token_expired'];

export async function api(method, url, body, retry = true) {
  const headers = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (auth.access) headers.Authorization = `Bearer ${auth.access}`;
  const r = await fetch(url, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined });
  let data = null;
  try { data = await r.json(); } catch { /* пустой ответ */ }

  if (r.status === 401 && auth.access && !url.startsWith('/api/auth/login')) {
    if (retry && RETRYABLE.includes(data?.detail) && auth.refresh) {
      try { await refreshTokens(); } catch { logoutHandler(); throw new ApiError(401, 'Сессия истекла'); }
      return api(method, url, body, false);
    }
    logoutHandler();
    throw new ApiError(401, 'Сессия истекла, войдите снова');
  }
  if (!r.ok) throw new ApiError(r.status, detailToText(data?.detail));
  return data;
}

// fetch с авторизацией и автоматическим refresh — для файлов (multipart, бинарные ответы)
async function authFetch(url, opts = {}, retry = true) {
  const r = await fetch(url, { ...opts, headers: { ...(opts.headers || {}), Authorization: `Bearer ${auth.access}` } });
  if (r.status === 401 && retry && auth.refresh) {
    const d = await r.clone().json().catch(() => ({}));
    if (RETRYABLE.includes(d.detail)) {
      try { await refreshTokens(); } catch { logoutHandler(); throw new ApiError(401, 'Сессия истекла'); }
      return authFetch(url, opts, false);
    }
  }
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new ApiError(r.status, r.status === 413 ? detailToText(d.detail) || 'Файл слишком большой' : detailToText(d.detail));
  }
  return r;
}

export async function uploadFile(url, file) {
  const fd = new FormData();
  fd.append('file', file);
  return (await authFetch(url, { method: 'POST', body: fd })).json();
}

export async function fetchBlob(url) {
  return (await authFetch(url)).blob();
}

export const http = {
  get: (u) => api('GET', u),
  post: (u, b = {}) => api('POST', u, b),
  patch: (u, b) => api('PATCH', u, b),
  put: (u, b) => api('PUT', u, b),
  del: (u) => api('DELETE', u),
};

// ---------- Realtime (Server-Sent Events) ----------
export class Realtime {
  constructor(onEvent, onStatus) {
    this.onEvent = onEvent;
    this.onStatus = onStatus;
    this.es = null;
    this.connectionId = null;
    this.timer = null;
  }

  connect() {
    this.close();
    if (!auth.access) return;
    const es = new EventSource(`/api/events?token=${encodeURIComponent(auth.access)}`);
    this.es = es;
    es.onopen = () => this.onStatus(true);
    es.onmessage = (e) => {
      const ev = JSON.parse(e.data);
      if (ev.type === 'hello') this.connectionId = ev.data.connection_id;
      this.onEvent(ev);
    };
    es.onerror = () => {
      this.onStatus(false);
      if (es.readyState === EventSource.CLOSED && this.es === es) {
        // сервер отклонил подключение (скорее всего протух токен) — обновим и переподключимся
        clearTimeout(this.timer);
        this.timer = setTimeout(async () => {
          try { await refreshTokens(); } catch { /* разберётся api() */ }
          this.connect();
        }, 2500);
      }
    };
  }

  close() {
    clearTimeout(this.timer);
    if (this.es) { this.es.close(); this.es = null; }
    this.connectionId = null;
    this.onStatus(false);
  }
}
