// Точка входа: корневой компонент, роутинг, сессия, realtime
import {
  auth, decodeJwt, http, onTokenChange, Realtime, refreshTokens, setLogoutHandler, setTokens,
} from './api.js';
import { BoardView } from './board.js';
import {
  ago, bus, go, isAdmin, parseHash, PRIORITY_RU, ROLE_RU, store, toast, userName,
} from './store.js';
import { resendPresence } from './presence.js';
import { effectiveTheme, playBeep, settings, toggleTheme } from './settings.js';
import { Avatar, RoleBadge } from './ui.js';
import {
  AdminView, DashboardView, LoginView, MyTasksView, ProfileView, ProjectView, SecurityView, SettingsView, TeamView, TokenView,
} from './views.js';

const VIEWS = {
  dashboard: DashboardView, team: TeamView, project: ProjectView, board: BoardView,
  my: MyTasksView, admin: AdminView, token: TokenView, profile: ProfileView, settings: SettingsView, security: SecurityView,
};
const TITLES = { dashboard: 'Главная', my: 'Мои задачи', admin: 'Администрирование', token: 'JWT и права', profile: 'Профиль', settings: 'Настройки', security: 'Безопасность' };

// ---------- сессия ----------
let refreshTimer = null;

function scheduleRefresh() {
  clearTimeout(refreshTimer);
  const exp = store.claims?.exp;
  if (!exp) return;
  // обновляем access-токен за минуту до истечения
  const ms = Math.max(5000, exp * 1000 - Date.now() - 60000);
  refreshTimer = setTimeout(() => refreshTokens().catch(() => logout()), ms);
}

const realtime = new Realtime(onServerEvent, (online) => { store.online = online; });

let navTimer = null;
async function loadNav() {
  const [teams, projects] = await Promise.all([http.get('/api/teams'), http.get('/api/projects')]);
  store.teams = teams;
  store.projects = projects;
}
const loadNavSoon = () => { clearTimeout(navTimer); navTimer = setTimeout(() => loadNav().catch(() => {}), 200); };

async function loadNotifications() {
  const n = await http.get('/api/me/notifications');
  store.notifications = n.items;
  store.unread = n.unread;
}

async function startSession() {
  store.claims = decodeJwt(auth.access);
  scheduleRefresh();
  try {
    store.user = await http.get('/api/me');
    await Promise.all([loadNav(), loadNotifications()]);
    realtime.connect();
  } catch (e) {
    console.error(e);
  }
}

function onTokenUpdated(token) {
  if (!token) return;
  const wasLoggedIn = !!store.user;
  const oldPv = store.claims?.pv;
  const permsKey = (c) => JSON.stringify([c?.sys, c?.teams, c?.projects]);
  const oldPerms = permsKey(store.claims);
  store.claims = decodeJwt(token);
  if (wasLoggedIn && oldPerms !== permsKey(store.claims)) toast('Ваши права доступа обновлены', 'info');
  scheduleRefresh();
  if (!wasLoggedIn) { startSession(); return; }
  // новый токен -> переподключаем SSE, чтобы сервер знал актуальный набор проектов
  realtime.connect();
  if (oldPv !== store.claims.pv) {
    loadNavSoon();
    bus.emit({ type: 'auth.refreshed', data: {} });
  }
}

async function logout() {
  const rt = auth.refresh;
  realtime.close();
  clearTimeout(refreshTimer);
  setTokens(null);
  store.user = null; store.claims = null; store.teams = []; store.projects = []; store.notifications = [];
  if (rt) fetch('/api/auth/logout', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ refresh_token: rt }) }).catch(() => {});
}

function onServerEvent(ev) {
  switch (ev.type) {
    case 'hello':
      store.connectionId = ev.data.connection_id;
      resendPresence();
      break;
    case 'auth.refresh':
      // права изменились на сервере -> перевыпускаем JWT без перезагрузки страницы
      refreshTokens().catch(() => logout());
      break;
    case 'notification':
      store.notifications.unshift(ev.data);
      store.unread += 1;
      if (settings.notifyToasts) toast(`🔔 ${ev.data.text}`);
      if (settings.notifySound) playBeep();
      break;
    case 'team.updated': case 'team.deleted': case 'project.updated': case 'project.deleted':
      loadNavSoon();
      break;
    default:
  }
  bus.emit(ev);
}

setLogoutHandler(logout);
onTokenChange(onTokenUpdated);

// ---------- корневой компонент ----------
const App = {
  components: { Avatar, RoleBadge, LoginView },
  data: () => ({ store, settings, showNotif: false, showUser: false }),
  computed: {
    view() { return VIEWS[store.route.name] || DashboardView; },
    viewKey() { return `${store.route.name}:${store.route.params.id || ''}`; },
    viewProps() { return { id: store.route.params.id, tab: store.route.params.tab }; },
    teamsTree() {
      return store.teams.map((t) => ({ ...t, projects: store.projects.filter((p) => p.team_id === t.id && !p.is_archived) }));
    },
    orphanProjects() {
      const teamIds = new Set(store.teams.map((t) => t.id));
      return store.projects.filter((p) => !teamIds.has(p.team_id) && !p.is_archived);
    },
    crumbs() { return store.crumbs.length ? store.crumbs : [{ text: TITLES[store.route.name] || '' }]; },
  },
  methods: {
    isAdmin, userName, ago, logout, toggleTheme,
    isDark: () => effectiveTheme() === 'dark',
    active(name, id) { return store.route.name === name && (id === undefined || store.route.params.id === id); },
    async openNotif(n) {
      this.showNotif = false;
      if (!n.is_read) { n.is_read = true; store.unread = Math.max(0, store.unread - 1); http.post(`/api/me/notifications/${n.id}/read`).catch(() => {}); }
      const l = n.link || {};
      if (l.card_id) go(`/board/${l.board_id}?card=${l.card_id}`);
      else if (l.project_id) go(`/project/${l.project_id}`);
      else if (l.team_id) go(`/team/${l.team_id}`);
    },
    async readAll() {
      await http.post('/api/me/notifications/read-all');
      store.notifications.forEach((n) => { n.is_read = true; });
      store.unread = 0;
    },
    async newTeam() {
      const name = prompt('Название новой команды:');
      if (!name?.trim()) return;
      const t = await http.post('/api/teams', { name: name.trim() });
      go(`/team/${t.id}`);
    },
  },
  mounted() {
    this.closeAll = () => { this.showNotif = false; this.showUser = false; };
    document.addEventListener('click', this.closeAll);
  },
  template: `
  <login-view v-if="!store.user && !store.claims" />
  <div v-else-if="store.user" class="layout">
    <aside class="sidebar">
      <div class="logo"><span class="mark">▦</span> Kanban Lite</div>
      <nav>
        <a href="#/" :class="{active: active('dashboard')}">🏠 Главная</a>
        <a href="#/my" :class="{active: active('my')}">✅ Мои задачи</a>
        <a href="#/token" :class="{active: active('token')}">🔑 JWT и права</a>
        <a href="#/admin" v-if="isAdmin()" :class="{active: active('admin')}">🛡 Администрирование</a>
      </nav>
      <div class="section">Команды <button title="Новая команда" @click="newTeam">＋</button></div>
      <div class="tree">
        <template v-for="t in teamsTree" :key="t.id">
          <a :href="'#/team/' + t.id" :class="{active: active('team', t.id)}"><span class="team-name">👥 {{ t.name }}</span></a>
          <a v-for="p in t.projects" :key="p.id" class="project" :href="'#/project/' + p.id" :class="{active: active('project', p.id)}">
            <span class="dot" :style="{background: p.visibility === 'private' ? '#f59e0b' : 'color-mix(in srgb, var(--accent) 60%, #fff)'}"></span>{{ p.name }}</a>
        </template>
        <template v-if="orphanProjects.length">
          <div class="section">Проекты (гость)</div>
          <a v-for="p in orphanProjects" :key="p.id" class="project" :href="'#/project/' + p.id" :class="{active: active('project', p.id)}">
            <span class="dot"></span>{{ p.name }}</a>
        </template>
        <div v-if="!store.teams.length" style="padding:6px 18px;font-size:12.5px;color:#7d84a3">Создайте первую команду кнопкой ＋</div>
      </div>
      <div class="foot">Курсовой проект · FastAPI + Vue<br>JWT-RBAC · SSE realtime</div>
    </aside>

    <div class="main">
      <header class="topbar">
        <button class="btn ghost icon" @click="settings.sidebarCollapsed = !settings.sidebarCollapsed" :title="settings.sidebarCollapsed ? 'Показать меню' : 'Скрыть меню'">☰</button>
        <div class="crumbs">
          <template v-for="(c, i) in crumbs" :key="i">
            <span v-if="i" class="muted">/</span>
            <a v-if="c.href" :href="c.href">{{ c.text }}</a><b v-else>{{ c.text }}</b>
          </template>
        </div>
        <span class="spacer"></span>
        <span class="conn" :class="{on: store.online}" :title="store.online ? 'Изменения приходят в реальном времени (SSE)' : 'Нет соединения, переподключаемся…'">
          <span class="led"></span>{{ store.online ? 'Live' : 'Offline' }}</span>
        <div class="dropdown bell">
          <button class="btn ghost icon" @click.stop="showNotif = !showNotif; showUser = false" title="Уведомления">🔔
            <span class="count" v-if="store.unread">{{ store.unread > 99 ? '99+' : store.unread }}</span></button>
          <div class="dropdown-menu notif-menu" v-if="showNotif" @click.stop>
            <div class="row" style="padding:6px 8px 8px"><b class="grow">Уведомления</b>
              <button class="btn sm ghost" v-if="store.unread" @click="readAll">Прочитать все</button></div>
            <div v-for="n in store.notifications" :key="n.id" class="notif" :class="{unread: !n.is_read}" @click="openNotif(n)">
              <span class="dot" v-if="!n.is_read"></span><span v-else style="width:8px"></span>
              <div><div>{{ n.text }}</div><div class="muted small">{{ ago(n.created_at) }}</div></div>
            </div>
            <div class="empty" v-if="!store.notifications.length">Уведомлений нет</div>
          </div>
        </div>
        <div class="dropdown">
          <button class="btn ghost" style="padding:4px 6px" @click.stop="showUser = !showUser; showNotif = false">
            <avatar :user="store.user" :size="30" /></button>
          <div class="dropdown-menu" v-if="showUser">
            <div style="padding:8px 10px"><b>{{ userName(store.user) }}</b><div class="muted small">@{{ store.user.username }} · {{ isAdmin() ? 'админ' : 'пользователь' }}</div></div>
            <a href="#/profile"><span class="mi">👤</span>Профиль</a>
            <a href="#/settings"><span class="mi">⚙️</span>Настройки</a>
            <a href="#/security"><span class="mi">🔒</span>Безопасность</a>
            <button class="menu-switch" @click.stop="toggleTheme"><span class="mi">🌙</span>Тёмная тема <span class="switch" :class="{on: isDark()}"></span></button>
            <hr>
            <a href="#/token"><span class="mi">🔑</span>Мой JWT</a>
            <button class="item" style="color:var(--danger)" @click="logout">Выйти</button>
          </div>
        </div>
      </header>
      <div class="content" :class="{'board-mode': store.route.name === 'board'}">
        <component :is="view" :key="viewKey" v-bind="viewProps" />
      </div>
    </div>
  </div>
  <div class="toasts"><div v-for="t in store.toasts" :key="t.id" class="toast" :class="t.type">{{ t.text }}</div></div>`,
};

// ---------- запуск ----------
function onRoute() {
  const prev = store.route;
  store.route = parseHash();
  if (prev.name !== store.route.name || prev.params.id !== store.route.params.id) store.crumbs = [];
}
window.addEventListener('hashchange', onRoute);
onRoute();

const app = Vue.createApp(App);
app.config.globalProperties.$router = go;
app.config.globalProperties.PRIORITY_RU = PRIORITY_RU;
app.config.globalProperties.ROLE_RU = ROLE_RU;
app.mount('#app');

if (auth.access) startSession();
