// Экраны приложения (кроме доски — она в board.js)
import { auth, decodeJwt, http, refreshTokens, setTokens } from './api.js';
import {
  ago, bus, can, canTeam, dueState, fmtDate, fmtDateTime, go, isAdmin, PRIORITY_RU, ROLE_RU, store, toast,
  toastError, userName,
} from './store.js';
import { ACCENTS, DEFAULTS, effectiveTheme, resetSettings, settings } from './settings.js';
import { SprintsTab } from './sprints.js';
import { Avatar, Modal, RoleBadge, UserPicker } from './ui.js';

const common = { components: { Avatar, Modal, RoleBadge, UserPicker } };
const helpers = { userName, fmtDate, fmtDateTime, ago, can, canTeam, isAdmin, dueState };

// Подписка компонента на realtime-события с автоотпиской
const realtimeMixin = {
  mounted() { if (this.onEvent) this._off = bus.on((ev) => this.onEvent(ev)); },
  unmounted() { this._off?.(); },
};

// ===================== ВХОД / РЕГИСТРАЦИЯ =====================
export const LoginView = {
  data: () => ({
    mode: 'login', f: { username: '', email: '', full_name: '', password: '' }, err: '', busy: false,
    challenge: null, code: '', useBackup: false,   // второй шаг входа (2FA)
  }),
  methods: {
    async submit() {
      this.err = ''; this.busy = true;
      try {
        const url = this.mode === 'login' ? '/api/auth/login' : '/api/auth/register';
        const body = this.mode === 'login' ? { username: this.f.username, password: this.f.password } : this.f;
        const res = await http.post(url, body);
        if (res.two_factor_required) {
          this.challenge = res.challenge_token; this.code = '';
          this.$nextTick(() => this.$refs.code?.focus());
        } else setTokens(res);
      } catch (e) { this.err = e.message; } finally { this.busy = false; }
    },
    async submitCode() {
      this.err = ''; this.busy = true;
      try {
        setTokens(await http.post('/api/auth/login/2fa', { challenge_token: this.challenge, code: this.code.trim() }));
      } catch (e) {
        this.err = e.message; this.code = '';
        if (e.status === 401 || e.status === 429) this.challenge = null;  // начать вход заново
      } finally { this.busy = false; }
    },
    back() { this.challenge = null; this.err = ''; this.useBackup = false; },
  },
  template: `
  <div class="auth-wrap"><div class="auth-box">
    <div class="logo"><span class="avatar" style="width:38px;height:38px;border-radius:10px;background:var(--accent)">▦</span> Kanban Lite</div>
    <div class="card-panel" v-if="challenge">
      <h2 style="margin-bottom:6px">🔐 Двухфакторная аутентификация</h2>
      <p class="muted small" style="margin:0 0 14px">{{ useBackup ? 'Введите один из резервных кодов (вида abcd-efgh). Каждый код работает один раз.'
        : 'Откройте приложение-аутентификатор и введите 6-значный код для Kanban Lite.' }}</p>
      <form class="form" @submit.prevent="submitCode">
        <input ref="code" v-model="code" class="otp-input" style="max-width:none"
               :inputmode="useBackup ? 'text' : 'numeric'" :autocomplete="useBackup ? 'off' : 'one-time-code'"
               :placeholder="useBackup ? 'xxxx-xxxx' : '000000'" :maxlength="useBackup ? 9 : 6" required>
        <div class="err" v-if="err">{{ err }}</div>
        <button class="btn primary" :disabled="busy" style="justify-content:center">Подтвердить</button>
      </form>
      <div class="row" style="margin-top:12px;justify-content:space-between">
        <button class="btn ghost sm" @click="back">← Назад</button>
        <button class="btn ghost sm" @click="useBackup = !useBackup; code = ''">{{ useBackup ? 'Код из приложения' : 'Нет доступа к телефону?' }}</button>
      </div>
    </div>
    <div class="card-panel" v-else>
      <div class="tabs" style="margin-top:0">
        <button :class="{active: mode==='login'}" @click="mode='login'">Вход</button>
        <button :class="{active: mode==='register'}" @click="mode='register'">Регистрация</button>
      </div>
      <form class="form" @submit.prevent="submit">
        <label class="field"><span>{{ mode==='login' ? 'Логин или email' : 'Логин' }}</span><input v-model="f.username" required autofocus></label>
        <template v-if="mode==='register'">
          <label class="field"><span>Email</span><input v-model="f.email" type="email" required></label>
          <label class="field"><span>Имя</span><input v-model="f.full_name"></label>
        </template>
        <label class="field"><span>Пароль</span><input v-model="f.password" type="password" required minlength="6"></label>
        <div class="err" v-if="err">{{ err }}</div>
        <button class="btn primary" :disabled="busy" style="justify-content:center">{{ mode==='login' ? 'Войти' : 'Создать аккаунт' }}</button>
      </form>
    </div>
  </div></div>`,
};

// ===================== ГЛАВНАЯ =====================
export const DashboardView = {
  ...common,
  mixins: [realtimeMixin],
  data: () => ({ cards: [], showNewTeam: false, team: { name: '', description: '' } }),
  computed: {
    store: () => store,
    overdue() { return this.cards.filter((c) => dueState(c.due_date, c.column_done) === 'overdue').length; },
    openCards() { return this.cards.filter((c) => !c.column_done); },
  },
  async mounted() { this.load(); },
  methods: {
    ...helpers,
    async load() { this.cards = await http.get('/api/me/cards'); },
    onEvent(ev) { if (ev.type === 'board.changed') { clearTimeout(this.t); this.t = setTimeout(this.load, 400); } },
    async createTeam() {
      try {
        const t = await http.post('/api/teams', this.team);
        this.showNewTeam = false; this.team = { name: '', description: '' };
        toast('Команда создана', 'success');
        go(`/team/${t.id}`);
      } catch (e) { toastError(e); }
    },
    openCard(c) { go(`/board/${c.board_id}?card=${c.id}`); },
  },
  template: `
  <div>
    <div class="page-head">
      <div class="titles"><h1>Привет, {{ store.user.full_name || store.user.username }} 👋</h1>
        <p>Здесь собраны ваши команды, проекты и задачи.</p></div>
      <button class="btn primary" @click="showNewTeam = true">＋ Новая команда</button>
    </div>
    <div class="stats-row" style="margin:18px 0">
      <div class="stat"><div class="v">{{ openCards.length }}</div><div class="l">Открытых задач на мне</div></div>
      <div class="stat"><div class="v" :style="{color: overdue ? 'var(--danger)' : ''}">{{ overdue }}</div><div class="l">Просрочено</div></div>
      <div class="stat"><div class="v">{{ store.teams.length }}</div><div class="l">Команд</div></div>
      <div class="stat"><div class="v">{{ store.projects.length }}</div><div class="l">Проектов</div></div>
    </div>

    <h2 style="margin:22px 0 12px">Проекты</h2>
    <div class="grid">
      <div class="tile" v-for="p in store.projects.filter(p => !p.is_archived)" :key="p.id" @click="$router('/project/' + p.id)">
        <div class="stripe" style="background:var(--accent)"></div>
        <div class="row"><h3 class="grow">{{ p.name }}</h3><span class="badge mono">{{ p.key }}</span></div>
        <p class="muted small" style="margin:6px 0 10px">{{ p.team_name }}</p>
        <role-badge :role="p.my_role" />
      </div>
      <div class="empty" v-if="!store.projects.length">Пока нет проектов. Создайте команду, а в ней — проект.</div>
    </div>

    <h2 style="margin:26px 0 12px">Мои ближайшие задачи</h2>
    <div class="card-panel" style="padding:6px 12px">
      <table class="table" v-if="openCards.length">
        <tr v-for="c in openCards.slice(0, 8)" :key="c.id" class="clickable" @click="openCard(c)">
          <td style="width:90px" class="mono muted">{{ c.key }}</td>
          <td><span :class="'prio prio-' + c.priority"></span> {{ c.title }}</td>
          <td class="muted small">{{ c.project_name }} / {{ c.board_name }}</td>
          <td><span class="badge">{{ c.column_name }}</span></td>
          <td style="width:90px" :style="{color: dueState(c.due_date) === 'overdue' ? 'var(--danger)' : ''}">{{ fmtDate(c.due_date) }}</td>
        </tr>
      </table>
      <div class="empty" v-else>На вас нет открытых задач 🎉</div>
    </div>

    <modal v-if="showNewTeam" title="Новая команда" @close="showNewTeam=false">
      <form class="form" @submit.prevent="createTeam" id="nt">
        <label class="field"><span>Название</span><input v-model="team.name" required autofocus></label>
        <label class="field"><span>Описание</span><textarea v-model="team.description"></textarea></label>
      </form>
      <template #foot><button class="btn" @click="showNewTeam=false">Отмена</button><button class="btn primary" form="nt">Создать</button></template>
    </modal>
  </div>`,
};

// ===================== КОМАНДА =====================
export const TeamView = {
  ...common,
  mixins: [realtimeMixin],
  props: ['id'],
  data: () => ({
    team: null, error: '', addRole: 'member', editing: false, edit: {},
    showNewProject: false, np: { name: '', key: '', description: '', visibility: 'team', template: 'basic' },
  }),
  computed: {
    store: () => store,
    canMembers() { return canTeam('team:members', this.id); },
    iAmOwner() { return store.claims?.teams?.[this.id]?.role === 'owner' || isAdmin(); },
  },
  mounted() { this.load(); },
  methods: {
    ...helpers,
    async load() {
      try {
        this.team = await http.get(`/api/teams/${this.id}`); this.error = '';
        store.crumbs = [{ text: 'Команды' }, { text: this.team.name }];
      } catch (e) { this.error = e.message; }
    },
    onEvent(ev) {
      if ((ev.type === 'team.updated' && ev.team_id === this.id) || ev.type === 'auth.refreshed') this.load();
      if (ev.type === 'team.deleted' && ev.team_id === this.id) { toast('Команда удалена'); go('/'); }
    },
    async addMember(u) {
      try { await http.post(`/api/teams/${this.id}/members`, { username: u.username, role: this.addRole }); toast(`${userName(u)} добавлен(а)`, 'success'); this.load(); } catch (e) { toastError(e); }
    },
    async setRole(m, role) {
      try { await http.patch(`/api/teams/${this.id}/members/${m.id}`, { role }); this.load(); } catch (e) { toastError(e); this.load(); }
    },
    async remove(m) {
      const self = m.id === store.user.id;
      if (!confirm(self ? 'Покинуть команду?' : `Удалить ${userName(m)} из команды?`)) return;
      try { await http.del(`/api/teams/${this.id}/members/${m.id}`); if (self) go('/'); else this.load(); } catch (e) { toastError(e); }
    },
    startEdit() { this.edit = { name: this.team.name, description: this.team.description }; this.editing = true; },
    async save() {
      try { await http.patch(`/api/teams/${this.id}`, this.edit); this.editing = false; this.load(); } catch (e) { toastError(e); }
    },
    async destroy() {
      if (!confirm(`Удалить команду «${this.team.name}» со всеми проектами? Это необратимо.`)) return;
      try { await http.del(`/api/teams/${this.id}`); toast('Команда удалена'); go('/'); } catch (e) { toastError(e); }
    },
    suggestKey() {
      if (this.np.key) return;
      this.np.key = this.np.name.replace(/[^A-Za-zА-Яа-я0-9 ]/g, '').split(/\s+/).map((w) => w[0] || '').join('').toUpperCase()
        .replace(/[^A-Z0-9]/g, '').slice(0, 5) || '';
    },
    async createProject() {
      try {
        const p = await http.post(`/api/teams/${this.id}/projects`, this.np);
        this.showNewProject = false;
        toast('Проект создан', 'success');
        go(`/project/${p.id}`);
      } catch (e) { toastError(e); }
    },
  },
  template: `
  <div v-if="error" class="empty">{{ error }}</div>
  <div v-else-if="team">
    <div class="page-head">
      <div class="titles" v-if="!editing">
        <h1>{{ team.name }} <role-badge :role="team.my_role" /></h1>
        <p>{{ team.description || 'Без описания' }}</p>
      </div>
      <form class="titles form" v-else @submit.prevent="save">
        <input v-model="edit.name" required><textarea v-model="edit.description" rows="2"></textarea>
        <div class="row"><button class="btn primary sm">Сохранить</button><button type="button" class="btn sm" @click="editing=false">Отмена</button></div>
      </form>
      <div class="row">
        <button class="btn" v-if="canTeam('team:update', id) && !editing" @click="startEdit">✎ Изменить</button>
        <button class="btn danger" v-if="canTeam('team:delete', id)" @click="destroy">Удалить</button>
        <button class="btn primary" v-if="canTeam('project:create', id)" @click="showNewProject=true">＋ Проект</button>
      </div>
    </div>

    <h2 style="margin:22px 0 12px">Проекты</h2>
    <div class="grid">
      <div class="tile" v-for="p in team.projects" :key="p.id" @click="$router('/project/' + p.id)" :style="{opacity: p.is_archived ? .55 : 1}">
        <div class="stripe" :style="{background: p.visibility==='private' ? '#f59e0b' : 'var(--accent)'}"></div>
        <div class="row"><h3 class="grow">{{ p.name }}</h3><span class="badge mono">{{ p.key }}</span></div>
        <p class="muted small" style="margin:6px 0 10px; min-height:18px">{{ p.description }}</p>
        <div class="row"><role-badge :role="p.my_role" /><span class="badge" v-if="p.visibility==='private'">🔒 приватный</span><span class="badge" v-if="p.is_archived">архив</span></div>
      </div>
      <div class="tile add" v-if="canTeam('project:create', id)" @click="showNewProject=true">＋ Новый проект</div>
    </div>

    <h2 style="margin:26px 0 12px">Участники · {{ team.members.length }}</h2>
    <div class="card-panel">
      <div class="row" v-if="canMembers" style="margin-bottom:12px">
        <user-picker placeholder="Добавить участника: логин, имя или email" :exclude="team.members.map(m => m.id)" @pick="addMember" />
        <select v-model="addRole" style="width:170px">
          <option value="member">Участник</option><option value="admin">Администратор</option>
          <option value="owner" v-if="iAmOwner">Владелец</option>
        </select>
      </div>
      <table class="table">
        <tr><th></th><th>Пользователь</th><th>Роль в команде</th><th>С нами с</th><th></th></tr>
        <tr v-for="m in team.members" :key="m.id">
          <td style="width:40px"><avatar :user="m" /></td>
          <td><b>{{ userName(m) }}</b> <span class="muted">@{{ m.username }}</span></td>
          <td style="width:200px">
            <select v-if="canMembers && (iAmOwner || m.role !== 'owner')" :value="m.role" @change="setRole(m, $event.target.value)">
              <option value="member">Участник</option><option value="admin">Администратор</option>
              <option value="owner" :disabled="!iAmOwner">Владелец</option>
            </select>
            <role-badge v-else :role="m.role" />
          </td>
          <td class="muted small">{{ fmtDate(m.joined_at) }}</td>
          <td style="width:110px;text-align:right">
            <button class="btn sm danger" v-if="m.id === store.user.id" @click="remove(m)">Выйти</button>
            <button class="btn sm danger" v-else-if="canMembers && (iAmOwner || m.role !== 'owner')" @click="remove(m)">Удалить</button>
          </td>
        </tr>
      </table>
      <p class="muted small" style="margin:12px 0 0">
        Владелец и администратор команды автоматически получают роль <b>менеджера</b> во всех её проектах.
        Участники команды видят «командные» проекты как <b>наблюдатели</b>; более высокую роль выдаёт менеджер проекта.
      </p>
    </div>

    <modal v-if="showNewProject" title="Новый проект" @close="showNewProject=false">
      <form class="form" @submit.prevent="createProject" id="np">
        <label class="field"><span>Название</span><input v-model="np.name" required autofocus @blur="suggestKey"></label>
        <label class="field"><span>Ключ (префикс задач, например WEB → WEB-12)</span><input v-model="np.key" required maxlength="10" pattern="[A-Za-z][A-Za-z0-9]*"></label>
        <label class="field"><span>Описание</span><textarea v-model="np.description" rows="2"></textarea></label>
        <label class="field"><span>Видимость</span>
          <select v-model="np.visibility"><option value="team">Вся команда (как наблюдатели)</option><option value="private">Приватный — только приглашённые</option></select></label>
        <label class="field"><span>Шаблон доски</span>
          <select v-model="np.template"><option value="basic">Базовый: К выполнению → В работе → Готово</option>
          <option value="scrum">Scrum: Бэклог → … → Ревью → Тест → Готово</option><option value="empty">Пустая доска</option></select></label>
      </form>
      <template #foot><button class="btn" @click="showNewProject=false">Отмена</button><button class="btn primary" form="np">Создать</button></template>
    </modal>
  </div>`,
};

// ===================== ПРОЕКТ =====================
export const ProjectView = {
  ...common,
  components: { ...common.components, SprintsTab },
  mixins: [realtimeMixin],
  props: ['id', 'tab'],
  data: () => ({
    project: null, error: '', activity: [], stats: null, newIds: new Set(),
    showNewBoard: false, nb: { name: '', description: '', color: '#0ea5e9', with_default_columns: true },
    addRole: 'member', label: { name: '', color: '#3b82f6' }, settings: {},
  }),
  computed: {
    store: () => store,
    current() { return this.tab || 'boards'; },
    tabs() {
      const t = [['boards', 'Доски'], ['sprints', 'Спринты'], ['members', 'Участники'], ['labels', 'Метки'], ['activity', 'Активность'], ['stats', 'Статистика']];
      if (can('project:update', this.id)) t.push(['settings', 'Настройки']);
      return t;
    },
  },
  watch: { current() { this.loadTab(); } },
  mounted() { this.load(); },
  methods: {
    ...helpers,

    async load() {
      try {
        this.project = await http.get(`/api/projects/${this.id}`);
        this.settings = { name: this.project.name, description: this.project.description, visibility: this.project.visibility };
        store.crumbs = [{ text: this.project.team_name, href: `#/team/${this.project.team_id}` }, { text: this.project.name }];
        this.error = '';
        this.loadTab();
      } catch (e) { this.error = e.message; }
    },
    async loadTab() {
      if (this.current === 'activity') this.activity = await http.get(`/api/projects/${this.id}/activity?limit=100`);
      if (this.current === 'stats') this.stats = await http.get(`/api/projects/${this.id}/stats`);
    },
    setTab(t) { go(`/project/${this.id}/${t}`); },
    onEvent(ev) {
      if (ev.project_id !== this.id && ev.type !== 'auth.refreshed') return;
      if (ev.type === 'activity' && this.current === 'activity') {
        this.activity.unshift(ev.data);
        this.newIds.add(ev.data.id);
      }
      if (ev.type === 'project.deleted') { toast('Проект удалён'); go('/'); return; }
      if (['project.updated', 'project.members', 'project.labels', 'auth.refreshed'].includes(ev.type)) {
        clearTimeout(this.t); this.t = setTimeout(this.load, 200);
      }
      if (ev.type === 'board.changed' && this.current === 'stats') {
        clearTimeout(this.ts); this.ts = setTimeout(this.loadTab, 500);
      }
    },
    async createBoard() {
      try {
        const b = await http.post(`/api/projects/${this.id}/boards`, this.nb);
        this.showNewBoard = false; go(`/board/${b.id}`);
      } catch (e) { toastError(e); }
    },
    async addMember(u) {
      try { await http.post(`/api/projects/${this.id}/members`, { username: u.username, role: this.addRole }); toast('Готово', 'success'); this.load(); } catch (e) { toastError(e); }
    },
    async setRole(m, role) {
      try { await http.patch(`/api/projects/${this.id}/members/${m.id}`, { role }); this.load(); } catch (e) { toastError(e); }
    },
    async removeExplicit(m) {
      try { await http.del(`/api/projects/${this.id}/members/${m.id}`); this.load(); } catch (e) { toastError(e); }
    },
    async addLabel() {
      try { await http.post(`/api/projects/${this.id}/labels`, this.label); this.label = { name: '', color: '#3b82f6' }; this.load(); } catch (e) { toastError(e); }
    },
    async saveLabel(l) { try { await http.patch(`/api/labels/${l.id}`, { name: l.name, color: l.color }); toast('Метка сохранена', 'success'); } catch (e) { toastError(e); } },
    async delLabel(l) { if (!confirm(`Удалить метку «${l.name}»?`)) return; try { await http.del(`/api/labels/${l.id}`); this.load(); } catch (e) { toastError(e); } },
    async saveSettings() {
      try { await http.patch(`/api/projects/${this.id}`, this.settings); toast('Сохранено', 'success'); this.load(); } catch (e) { toastError(e); }
    },
    async toggleArchive() {
      try { await http.patch(`/api/projects/${this.id}`, { is_archived: !this.project.is_archived }); this.load(); } catch (e) { toastError(e); }
    },
    async destroy() {
      if (!confirm(`Удалить проект «${this.project.name}» со всеми досками и карточками?`)) return;
      try { await http.del(`/api/projects/${this.id}`); go(`/team/${this.project.team_id}`); } catch (e) { toastError(e); }
    },
    maxDay() { return Math.max(1, ...this.stats.activity_per_day.map((d) => d.count)); },
  },
  template: `
  <div v-if="error" class="empty">{{ error }}</div>
  <div v-else-if="project">
    <div class="page-head">
      <div class="titles">
        <h1>{{ project.name }} <span class="badge mono">{{ project.key }}</span> <role-badge :role="project.my_role" />
          <span class="badge" v-if="project.visibility==='private'">🔒 приватный</span><span class="badge" v-if="project.is_archived">в архиве</span></h1>
        <p>Команда <a :href="'#/team/' + project.team_id">{{ project.team_name }}</a> · {{ project.description || 'без описания' }}</p>
      </div>
    </div>
    <div class="tabs"><button v-for="[k, l] in tabs" :key="k" :class="{active: current===k}" @click="setTab(k)">{{ l }}</button></div>

    <!-- Доски -->
    <div v-if="current==='boards'" class="grid">
      <div class="tile" v-for="b in project.boards" :key="b.id" @click="$router('/board/' + b.id)" :style="{opacity: b.is_archived ? .5 : 1}">
        <div class="stripe" :style="{background: b.color}"></div>
        <h3>{{ b.name }}</h3>
        <p class="muted small" style="margin:6px 0 0">{{ b.description || '—' }}</p>
        <span class="badge" v-if="b.is_archived" style="margin-top:8px">архив</span>
      </div>
      <div class="tile add" v-if="can('board:manage', id)" @click="showNewBoard=true">＋ Новая доска</div>
    </div>

    <!-- Спринты -->
    <sprints-tab v-if="current==='sprints'" :project-id="id" />

    <!-- Участники -->
    <div v-if="current==='members'" class="card-panel">
      <div class="row" v-if="can('project:members', id)" style="margin-bottom:12px">
        <user-picker placeholder="Пригласить в проект…" @pick="addMember" />
        <select v-model="addRole" style="width:160px"><option value="viewer">Наблюдатель</option><option value="member">Участник</option><option value="manager">Менеджер</option></select>
      </div>
      <table class="table">
        <tr><th></th><th>Пользователь</th><th>Итоговая роль</th><th>Откуда</th><th>Явная роль в проекте</th><th></th></tr>
        <tr v-for="m in project.members" :key="m.id">
          <td style="width:40px"><avatar :user="m" /></td>
          <td><b>{{ userName(m) }}</b> <span class="muted">@{{ m.username }}</span></td>
          <td><role-badge :role="m.role" /></td>
          <td class="small muted">{{ m.team_role ? 'команда: ' + ROLE_RU[m.team_role] : 'приглашён в проект' }}</td>
          <td style="width:170px">
            <select v-if="can('project:members', id)" :value="m.explicit_role || ''" @change="$event.target.value ? setRole(m, $event.target.value) : removeExplicit(m)">
              <option value="">— нет —</option><option value="viewer">Наблюдатель</option><option value="member">Участник</option><option value="manager">Менеджер</option>
            </select>
            <span v-else class="muted">{{ m.explicit_role ? ROLE_RU[m.explicit_role] : '—' }}</span>
          </td>
        </tr>
      </table>
      <p class="muted small" style="margin:12px 0 0">Итоговая роль = максимум из явной роли в проекте и роли, наследуемой от команды. Права из неё попадают в JWT.</p>
    </div>

    <!-- Метки -->
    <div v-if="current==='labels'" class="card-panel" style="max-width:620px">
      <div v-for="l in project.labels" :key="l.id" class="row" style="margin-bottom:8px">
        <template v-if="can('label:manage', id)">
          <input type="color" v-model="l.color"><input v-model="l.name" class="grow">
          <button class="btn sm" @click="saveLabel(l)">Сохранить</button><button class="btn sm danger" @click="delLabel(l)">✕</button>
        </template>
        <span v-else class="chip" :style="{background: l.color}">{{ l.name }}</span>
      </div>
      <form class="row" v-if="can('label:manage', id)" @submit.prevent="addLabel" style="margin-top:14px;padding-top:14px;border-top:1px solid var(--border)">
        <input type="color" v-model="label.color"><input v-model="label.name" placeholder="Новая метка" required class="grow"><button class="btn primary sm">Добавить</button>
      </form>
    </div>

    <!-- Активность -->
    <div v-if="current==='activity'" class="card-panel" style="max-width:820px">
      <div class="muted small" style="margin-bottom:6px">Лента обновляется в реальном времени.</div>
      <div v-for="a in activity" :key="a.id" class="feed-item" :class="{new: newIds.has(a.id)}">
        <avatar :user="a.user" :size="30" />
        <div class="grow"><b>{{ userName(a.user) }}</b> {{ a.message }}
          <div class="muted small">{{ ago(a.created_at) }}<template v-if="a.board_id && a.card_id"> · <a :href="'#/board/' + a.board_id + '?card=' + a.card_id">открыть</a></template></div></div>
      </div>
      <div class="empty" v-if="!activity.length">Пока пусто</div>
    </div>

    <!-- Статистика -->
    <div v-if="current==='stats' && stats">
      <div class="stats-row">
        <div class="stat"><div class="v">{{ stats.total }}</div><div class="l">Всего карточек</div></div>
        <div class="stat"><div class="v" style="color:var(--success)">{{ stats.done }}</div><div class="l">Выполнено</div></div>
        <div class="stat"><div class="v">{{ stats.open }}</div><div class="l">В работе / ожидают</div></div>
        <div class="stat"><div class="v" style="color:var(--danger)">{{ stats.overdue }}</div><div class="l">Просрочено</div></div>
        <div class="stat"><div class="v">{{ stats.story_points_done }}/{{ stats.story_points }}</div><div class="l">Story points</div></div>
      </div>
      <div class="card-panel" style="margin-top:14px">
        <div class="row"><b>Прогресс</b><span class="spacer"></span><span class="muted">{{ stats.total ? Math.round(stats.done / stats.total * 100) : 0 }}%</span></div>
        <div class="progress" style="margin-top:8px;height:10px"><div :style="{width: (stats.total ? stats.done / stats.total * 100 : 0) + '%'}"></div></div>
      </div>
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(340px,1fr));margin-top:14px">
        <div class="card-panel">
          <h3 style="margin-bottom:12px">По приоритету</h3>
          <div class="bars">
            <div class="bar-row" v-for="(n, k) in stats.by_priority" :key="k">
              <span><span :class="'prio prio-' + k"></span> {{ PRIORITY_RU[k] }}</span>
              <div class="bar"><div :class="'prio-' + k" :style="{width: (stats.total ? n / stats.total * 100 : 0) + '%'}"></div></div>
              <span class="muted">{{ n }}</span>
            </div>
          </div>
        </div>
        <div class="card-panel">
          <h3 style="margin-bottom:12px">Нагрузка по исполнителям</h3>
          <div class="bars">
            <div class="bar-row" v-for="u in stats.by_assignee" :key="u.id">
              <span class="row" style="gap:6px"><avatar :user="u" :size="20" />{{ userName(u) }}</span>
              <div class="bar"><div style="background:var(--success)" :style="{width: u.done / (stats.by_assignee[0].open + stats.by_assignee[0].done) * 100 + '%'}"></div>
                <div style="background:var(--accent)" :style="{width: u.open / (stats.by_assignee[0].open + stats.by_assignee[0].done) * 100 + '%'}"></div></div>
              <span class="muted">{{ u.done }}/{{ u.open + u.done }}</span>
            </div>
            <div class="muted" v-if="!stats.by_assignee.length">Нет назначенных задач</div>
          </div>
        </div>
      </div>
      <div class="card-panel" style="margin-top:14px">
        <h3>Активность за 14 дней</h3>
        <div class="histogram">
          <div class="col" v-for="d in stats.activity_per_day" :key="d.date" :title="d.date + ': ' + d.count">
            <span>{{ d.count || '' }}</span><div class="b" :style="{height: d.count / maxDay() * 100 + '%'}"></div><span>{{ d.date.slice(8) }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Настройки -->
    <div v-if="current==='settings'" class="card-panel" style="max-width:620px">
      <form class="form" @submit.prevent="saveSettings">
        <label class="field"><span>Название</span><input v-model="settings.name" required></label>
        <label class="field"><span>Описание</span><textarea v-model="settings.description"></textarea></label>
        <label class="field"><span>Видимость</span><select v-model="settings.visibility"><option value="team">Вся команда</option><option value="private">Приватный</option></select></label>
        <div class="row"><button class="btn primary">Сохранить</button></div>
      </form>
      <div class="row" style="margin-top:22px;padding-top:16px;border-top:1px solid var(--border)">
        <button class="btn" @click="toggleArchive">{{ project.is_archived ? 'Восстановить из архива' : 'Архивировать проект' }}</button>
        <button class="btn danger" v-if="can('project:delete', id)" @click="destroy">Удалить проект</button>
      </div>
    </div>

    <modal v-if="showNewBoard" title="Новая доска" @close="showNewBoard=false">
      <form class="form" id="nb" @submit.prevent="createBoard">
        <label class="field"><span>Название</span><input v-model="nb.name" required autofocus></label>
        <label class="field"><span>Описание</span><input v-model="nb.description"></label>
        <label class="field"><span>Цвет</span><input type="color" v-model="nb.color"></label>
        <label class="row"><input type="checkbox" v-model="nb.with_default_columns"> Создать стандартные колонки</label>
      </form>
      <template #foot><button class="btn" @click="showNewBoard=false">Отмена</button><button class="btn primary" form="nb">Создать</button></template>
    </modal>
  </div>`,
};

// ===================== МОИ ЗАДАЧИ =====================
export const MyTasksView = {
  ...common,
  mixins: [realtimeMixin],
  data: () => ({ cards: [], filter: 'open' }),
  computed: {
    shown() {
      if (this.filter === 'open') return this.cards.filter((c) => !c.column_done);
      if (this.filter === 'overdue') return this.cards.filter((c) => dueState(c.due_date, c.column_done) === 'overdue');
      if (this.filter === 'done') return this.cards.filter((c) => c.column_done);
      return this.cards;
    },
  },
  mounted() { this.load(); },
  methods: {
    ...helpers,
    async load() { this.cards = await http.get('/api/me/cards'); },
    onEvent(ev) { if (ev.type === 'board.changed' || ev.type === 'auth.refreshed') { clearTimeout(this.t); this.t = setTimeout(this.load, 300); } },
  },
  template: `
  <div>
    <div class="page-head"><div class="titles"><h1>Мои задачи</h1><p>Карточки, где вы исполнитель, по всем проектам.</p></div></div>
    <div class="tabs">
      <button :class="{active: filter==='open'}" @click="filter='open'">Открытые</button>
      <button :class="{active: filter==='overdue'}" @click="filter='overdue'">Просроченные</button>
      <button :class="{active: filter==='done'}" @click="filter='done'">Выполненные</button>
      <button :class="{active: filter==='all'}" @click="filter='all'">Все</button>
    </div>
    <div class="card-panel" style="padding:6px 12px">
      <table class="table" v-if="shown.length">
        <tr><th>Ключ</th><th>Задача</th><th>Проект / доска</th><th>Статус</th><th>Приоритет</th><th>Срок</th></tr>
        <tr v-for="c in shown" :key="c.id" class="clickable" @click="$router('/board/' + c.board_id + '?card=' + c.id)">
          <td class="mono muted">{{ c.key }}</td>
          <td><b>{{ c.title }}</b> <span class="muted small" v-if="c.checklist.total">☑ {{ c.checklist.done }}/{{ c.checklist.total }}</span></td>
          <td class="muted small">{{ c.project_name }} / {{ c.board_name }}</td>
          <td><span class="badge" :style="c.column_done ? 'color:var(--success)' : ''">{{ c.column_name }}</span></td>
          <td><span :class="'prio prio-' + c.priority"></span> {{ PRIORITY_RU[c.priority] }}</td>
          <td :style="{color: dueState(c.due_date, c.column_done)==='overdue' ? 'var(--danger)' : ''}">{{ fmtDate(c.due_date) || '—' }}</td>
        </tr>
      </table>
      <div class="empty" v-else>Ничего нет</div>
    </div>
  </div>`,
};

// ===================== АДМИНКА =====================
export const AdminView = {
  ...common,
  data: () => ({ users: [], stats: null }),
  computed: { store: () => store },
  mounted() { this.load(); },
  methods: {
    ...helpers,
    async load() {
      [this.users, this.stats] = await Promise.all([http.get('/api/admin/users'), http.get('/api/admin/stats')]);
    },
    confirm: (t) => window.confirm(t),
    async update(u, patch) {
      try { await http.patch(`/api/admin/users/${u.id}`, patch); toast('Сохранено. Токен пользователя отозван — права обновятся сразу.', 'success'); } catch (e) { toastError(e); }
      this.load();
    },
  },
  template: `
  <div v-if="!isAdmin()" class="empty">Доступ только для администраторов</div>
  <div v-else>
    <div class="page-head"><div class="titles"><h1>Администрирование</h1><p>Системные роли и блокировка пользователей.</p></div>
      <button class="btn" @click="load">⟳ Обновить</button></div>
    <div class="stats-row" style="margin:18px 0" v-if="stats">
      <div class="stat"><div class="v">{{ stats.users }}</div><div class="l">Пользователей</div></div>
      <div class="stat"><div class="v">{{ stats.teams }}</div><div class="l">Команд</div></div>
      <div class="stat"><div class="v">{{ stats.projects }}</div><div class="l">Проектов</div></div>
      <div class="stat"><div class="v">{{ stats.cards }}</div><div class="l">Карточек</div></div>
      <div class="stat"><div class="v" style="color:var(--success)">{{ stats.online_users }}</div><div class="l">Онлайн ({{ stats.online_connections }} SSE-соединений)</div></div>
    </div>
    <div class="card-panel" style="padding:6px 12px">
      <table class="table">
        <tr><th></th><th>Пользователь</th><th>Email</th><th>Зарегистрирован</th><th>2FA</th><th>Системная роль</th><th>Статус</th></tr>
        <tr v-for="u in users" :key="u.id" :style="{opacity: u.is_active ? 1 : .5}">
          <td style="width:40px"><avatar :user="u" /></td>
          <td><b>{{ userName(u) }}</b> <span class="muted">@{{ u.username }}</span></td>
          <td class="muted">{{ u.email }}</td>
          <td class="muted small">{{ fmtDate(u.created_at) }}</td>
          <td style="width:120px"><template v-if="u.totp_enabled"><span class="badge ok">вкл</span>
            <button class="btn sm ghost" title="Сбросить 2FA (пользователь потерял телефон)" @click="confirm('Сбросить 2FA у ' + u.username + '?') && update(u, {reset_2fa: true})">↺</button></template>
            <span v-else class="muted small">—</span></td>
          <td style="width:170px"><select :value="u.system_role" :disabled="u.id === store.user.id" @change="update(u, {system_role: $event.target.value})">
            <option value="user">Пользователь</option><option value="admin">Администратор</option></select></td>
          <td style="width:150px"><button class="btn sm" :class="{danger: u.is_active}" :disabled="u.id === store.user.id" @click="update(u, {is_active: !u.is_active})">{{ u.is_active ? 'Заблокировать' : 'Разблокировать' }}</button></td>
        </tr>
      </table>
    </div>
  </div>`,
};

// ===================== JWT =====================
export const TokenView = {
  ...common,
  mixins: [realtimeMixin],
  data: () => ({ server: null, now: Date.now() }),
  computed: {
    store: () => store,
    token() { return store.claims ? auth.access : ''; },
    parts() { return (this.token || '').split('.'); },
    header() { return this.parts[0] ? decodeJwt(`${this.parts[0]}.${this.parts[0]}`) : null; },
    payload() { return store.claims; },
    left() { return this.payload ? Math.max(0, Math.round(this.payload.exp - this.now / 1000)) : 0; },
  },
  mounted() { this.timer = setInterval(() => { this.now = Date.now(); }, 1000); this.loadServer(); },
  unmounted() { clearInterval(this.timer); },
  methods: {
    ...helpers,
    async loadServer() { this.server = await http.get('/api/auth/token-info'); },
    async refresh() { try { await refreshTokens(); toast('Токен обновлён', 'success'); this.loadServer(); } catch (e) { toastError(e); } },
    onEvent(ev) { if (ev.type === 'auth.refreshed') this.loadServer(); },
    projectName(pid) { return store.projects.find((p) => p.id === Number(pid))?.name || `#${pid}`; },
    teamName(tid) { return store.teams.find((t) => t.id === Number(tid))?.name || `#${tid}`; },
    fmt(o) { return JSON.stringify(o, null, 2); },
  },
  template: `
  <div v-if="payload">
    <div class="page-head">
      <div class="titles"><h1>Мой JWT</h1>
        <p>Все права пользователя «зашиты» в access-токен. Сервер проверяет доступ, не обращаясь к таблицам ролей.</p></div>
      <button class="btn primary" @click="refresh">⟳ Обновить токен</button>
    </div>
    <div class="stats-row" style="margin:18px 0">
      <div class="stat"><div class="v">{{ Math.floor(left / 60) }}:{{ String(left % 60).padStart(2, '0') }}</div><div class="l">До истечения access-токена</div></div>
      <div class="stat"><div class="v">{{ payload.pv }}</div><div class="l">Версия прав (pv)</div></div>
      <div class="stat"><div class="v">{{ Object.keys(payload.teams).length }}</div><div class="l">Команд в токене</div></div>
      <div class="stat"><div class="v">{{ Object.keys(payload.projects).length }}</div><div class="l">Проектов в токене</div></div>
      <div class="stat"><div class="v">{{ token.length }}</div><div class="l">Размер токена, байт</div></div>
    </div>
    <div class="card-panel">
      <h3 style="margin-bottom:10px">Закодированный токен</h3>
      <div class="jwt mono"><span class="h">{{ parts[0] }}</span>.<span class="p">{{ parts[1] }}</span>.<span class="s">{{ parts[2] }}</span></div>
    </div>
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(380px,1fr));margin-top:14px">
      <div class="card-panel">
        <h3 style="margin-bottom:10px">Права по проектам</h3>
        <table class="table">
          <tr><th>Проект</th><th>Роль</th><th>Права</th></tr>
          <tr v-for="(p, pid) in payload.projects" :key="pid">
            <td><a :href="'#/project/' + pid">{{ projectName(pid) }}</a></td>
            <td><role-badge :role="p.role" /></td>
            <td><span class="perm" v-for="x in p.perms" :key="x">{{ x }}</span></td>
          </tr>
        </table>
        <h3 style="margin:18px 0 10px">Права по командам</h3>
        <table class="table">
          <tr v-for="(t, tid) in payload.teams" :key="tid">
            <td><a :href="'#/team/' + tid">{{ teamName(tid) }}</a></td><td><role-badge :role="t.role" /></td>
            <td><span class="perm" v-for="x in t.perms" :key="x">{{ x }}</span></td>
          </tr>
        </table>
        <p class="small muted" style="margin-top:14px">Системная роль: <b>{{ ROLE_RU[payload.role] || payload.role }}</b>
          <span class="perm" v-for="x in payload.sys" :key="x">{{ x }}</span></p>
      </div>
      <div class="card-panel">
        <h3 style="margin-bottom:10px">Payload</h3>
        <pre class="json mono">{{ fmt(payload) }}</pre>
      </div>
    </div>
    <div class="card-panel" style="margin-top:14px">
      <h3 style="margin-bottom:8px">Как это работает</h3>
      <ol class="small" style="line-height:1.7;margin:0;padding-left:18px">
        <li>При входе сервер собирает роли пользователя (системную, в командах, в проектах), раскрывает их в список прав и подписывает в JWT (HS256).</li>
        <li>Каждый запрос проверяется по правам из токена: <code>me.require_project(id, "card:move")</code>.</li>
        <li>Токен живёт 15 минут, затем клиент молча получает новый по refresh-токену (refresh ротируется).</li>
        <li>Если права меняются (добавили в команду, сменили роль, заблокировали) — у пользователя растёт <code>pv</code>, старые токены сразу отвергаются (<code>401 token_outdated</code>), а через SSE приходит событие <code>auth.refresh</code> — клиент сам перевыпускает токен, без перезагрузки страницы.</li>
      </ol>
    </div>
  </div>`,
};

// ===================== ПРОФИЛЬ =====================
export const ProfileView = {
  ...common,
  data() {
    return { f: { full_name: store.user.full_name, email: store.user.email, color: store.user.color }, pw: { old_password: '', new_password: '' } };
  },
  computed: { store: () => store },
  methods: {
    async save() {
      try { store.user = await http.patch('/api/me', this.f); toast('Профиль сохранён', 'success'); } catch (e) { toastError(e); }
    },
    async changePw() {
      try { await http.post('/api/me/password', this.pw); this.pw = { old_password: '', new_password: '' }; toast('Пароль изменён', 'success'); } catch (e) { toastError(e); }
    },
  },
  template: `
  <div style="max-width:560px">
    <div class="page-head"><div class="titles"><h1>Профиль</h1><p>@{{ store.user.username }}</p></div></div>
    <div class="card-panel" style="margin-top:16px">
      <form class="form" @submit.prevent="save">
        <div class="row"><avatar :user="{...store.user, ...f}" :size="54" /><label class="field grow"><span>Цвет аватара</span><input type="color" v-model="f.color"></label></div>
        <label class="field"><span>Имя</span><input v-model="f.full_name"></label>
        <label class="field"><span>Email</span><input v-model="f.email" type="email"></label>
        <div><button class="btn primary">Сохранить</button></div>
      </form>
    </div>
    <div class="card-panel" style="margin-top:16px">
      <h3 style="margin-bottom:12px">Смена пароля</h3>
      <form class="form" @submit.prevent="changePw">
        <label class="field"><span>Текущий пароль</span><input type="password" v-model="pw.old_password" required></label>
        <label class="field"><span>Новый пароль</span><input type="password" v-model="pw.new_password" required minlength="6"></label>
        <div><button class="btn">Изменить пароль</button></div>
      </form>
    </div>
  </div>`,
};

// ===================== НАСТРОЙКИ =====================
const Switch = {
  props: { modelValue: Boolean },
  emits: ['update:modelValue'],
  template: `<button type="button" class="switch" :class="{on: modelValue}" role="switch" :aria-checked="modelValue"
    @click="$emit('update:modelValue', !modelValue)"></button>`,
};

export const SettingsView = {
  components: { ToggleSwitch: Switch },
  data: () => ({ settings, ACCENTS }),
  computed: {
    themes: () => [
      { k: 'light', l: 'Светлая', c: ['#f4f5f9', '#ffffff', '#ebedf3'] },
      { k: 'dark', l: 'Тёмная', c: ['#0f1320', '#181d2c', '#141927'] },
      { k: 'system', l: 'Как в системе', c: ['#f4f5f9', '#181d2c', '#ebedf3'] },
    ],
    current: () => effectiveTheme(),
  },
  methods: {
    reset() { if (confirm('Сбросить все настройки интерфейса?')) { resetSettings(); toast('Настройки сброшены'); } },
    testNotify() { toast('🔔 Так выглядит уведомление'); },
    isDefaultAccent() { return settings.accent === DEFAULTS.accent; },
  },
  template: `
  <div style="max-width:720px">
    <div class="page-head"><div class="titles"><h1>Настройки</h1>
      <p>Применяются сразу и сохраняются в этом браузере.</p></div>
      <button class="btn" @click="reset">Сбросить</button></div>

    <h2 style="margin:22px 0 10px">Оформление</h2>
    <div class="card-panel" style="padding:4px 18px">
      <div class="setting">
        <div class="txt"><b>Тема</b><span>Сейчас: {{ current === 'dark' ? 'тёмная' : 'светлая' }}</span></div>
        <div class="theme-preview">
          <button v-for="t in themes" :key="t.k" :class="{on: settings.theme === t.k}" @click="settings.theme = t.k">
            <div class="mini" :style="{background: t.c[0]}"><i :style="{background: t.c[2]}"></i><i :style="{background: t.c[1]}"></i><i :style="{background: t.c[2]}"></i></div>
            <span>{{ t.l }}</span>
          </button>
        </div>
      </div>
      <div class="setting">
        <div class="txt"><b>Цвет акцента</b><span>Кнопки, ссылки, выделения</span></div>
        <div class="swatches">
          <button v-for="c in ACCENTS" :key="c" :style="{background: c}" :class="{on: settings.accent === c}" @click="settings.accent = c" :title="c"></button>
          <input type="color" v-model="settings.accent" title="Свой цвет">
        </div>
      </div>
      <div class="setting">
        <div class="txt"><b>Компактные карточки</b><span>Больше карточек помещается на экране доски</span></div>
        <toggle-switch v-model="settings.compactCards" />
      </div>
      <div class="setting">
        <div class="txt"><b>Фон доски в цвет доски</b><span>Лёгкий оттенок помогает различать доски</span></div>
        <toggle-switch v-model="settings.boardBackground" />
      </div>
    </div>

    <h2 style="margin:22px 0 10px">Realtime и уведомления</h2>
    <div class="card-panel" style="padding:4px 18px">
      <div class="setting">
        <div class="txt"><b>Подсвечивать чужие изменения</b><span>Карточка, которую изменил другой участник, мигнёт на доске</span></div>
        <toggle-switch v-model="settings.liveHighlight" />
      </div>
      <div class="setting">
        <div class="txt"><b>Всплывающие уведомления</b><span>Назначения, упоминания, комментарии. В колокольчике они остаются в любом случае</span></div>
        <toggle-switch v-model="settings.notifyToasts" />
      </div>
      <div class="setting">
        <div class="txt"><b>Звук уведомлений</b><span>Короткий сигнал при новом уведомлении</span></div>
        <toggle-switch v-model="settings.notifySound" />
      </div>
      <div class="setting">
        <div class="txt"><b>Проверка</b><span>Показать пример уведомления</span></div>
        <button class="btn sm" @click="testNotify">Показать</button>
      </div>
    </div>
  </div>`,
};

// ===================== БЕЗОПАСНОСТЬ =====================
export const SecurityView = {
  ...common,
  mixins: [realtimeMixin],
  data: () => ({
    tfa: null, setup: null, code: '', codes: null, sessions: [], busy: false,
    disableForm: null, regenCode: '',
  }),
  mounted() { this.load(); },
  methods: {
    ...helpers,
    async load() {
      [this.tfa, this.sessions] = await Promise.all([http.get('/api/me/2fa'), http.get('/api/me/sessions')]);
    },
    onEvent(ev) { if (ev.type === 'auth.refreshed') this.load(); },
    async startSetup() {
      try { this.setup = await http.post('/api/me/2fa/setup'); this.code = ''; } catch (e) { toastError(e); }
    },
    async enable() {
      this.busy = true;
      try {
        const r = await http.post('/api/me/2fa/enable', { code: this.code.trim() });
        this.codes = r.backup_codes; this.setup = null; this.code = '';
        toast('Двухфакторная аутентификация включена', 'success');
        this.load();
      } catch (e) { toastError(e); } finally { this.busy = false; }
    },
    async disable() {
      try {
        await http.post('/api/me/2fa/disable', this.disableForm);
        this.disableForm = null; this.codes = null;
        toast('2FA отключена'); this.load();
      } catch (e) { toastError(e); }
    },
    async regenerate() {
      try {
        this.codes = (await http.post('/api/me/2fa/backup-codes', { code: this.regenCode.trim() })).backup_codes;
        this.regenCode = ''; this.load();
      } catch (e) { toastError(e); }
    },
    copyCodes() { navigator.clipboard?.writeText(this.codes.join('\n')); toast('Коды скопированы'); },
    downloadCodes() {
      const text = `Резервные коды Kanban Lite (${store.user.username})\nКаждый код можно использовать один раз.\n\n${this.codes.join('\n')}\n`;
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
      a.download = 'kanban-backup-codes.txt'; a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    },
    copySecret() { navigator.clipboard?.writeText(this.setup.secret); toast('Ключ скопирован'); },
    async revoke(s) {
      if (!confirm(`Завершить сессию «${s.browser}, ${s.os}»?`)) return;
      try { await http.del(`/api/me/sessions/${s.id}`); toast('Сессия завершена', 'success'); this.load(); } catch (e) { toastError(e); }
    },
    async revokeOthers() {
      if (!confirm('Выйти на всех остальных устройствах?')) return;
      try {
        const r = await http.post('/api/me/sessions/revoke-others');
        toast(`Завершено сессий: ${r.revoked}`, 'success'); this.load();
      } catch (e) { toastError(e); }
    },
    async revokeAll() {
      if (!confirm('Выйти на всех устройствах, включая это? Потребуется войти заново.')) return;
      try { await http.post('/api/me/sessions/revoke-all'); } catch (e) { toastError(e); return; }
      // токены этого устройства уже отозваны — просто чистим их локально
      setTokens(null);
      location.reload();
    },
    deviceIcon(s) { return s.mobile ? '📱' : s.browser === 'python-httpx' ? '🤖' : '💻'; },
  },
  template: `
  <div style="max-width:760px">
    <div class="page-head"><div class="titles"><h1>Безопасность</h1>
      <p>Двухфакторная аутентификация и устройства, на которых выполнен вход.</p></div></div>

    <h2 style="margin:22px 0 10px">Двухфакторная аутентификация (2FA)</h2>
    <div class="card-panel" v-if="tfa">
      <!-- показываем резервные коды один раз после включения/перевыпуска -->
      <div v-if="codes" style="margin-bottom:18px">
        <div class="presence-note warn"><b>Сохраните резервные коды.</b> Они понадобятся, если телефон потеряется.
          Каждый код срабатывает один раз. Больше мы их не покажем.</div>
        <div class="codes"><span v-for="c in codes" :key="c">{{ c }}</span></div>
        <div class="row" style="margin-top:10px">
          <button class="btn sm" @click="copyCodes">📋 Скопировать</button>
          <button class="btn sm" @click="downloadCodes">⬇ Скачать .txt</button>
          <button class="btn sm primary" @click="codes = null">Я сохранил(а)</button>
        </div>
      </div>

      <template v-if="tfa.enabled">
        <div class="row"><span class="badge ok">✓ Включена</span>
          <span class="muted small">Резервных кодов осталось: <b>{{ tfa.backup_codes_left }}</b></span></div>
        <p class="muted small">При входе после пароля нужно ввести код из приложения-аутентификатора.</p>
        <form class="row wrap" @submit.prevent="regenerate" style="margin-top:8px">
          <input v-model="regenCode" placeholder="Код из приложения" style="max-width:200px" required>
          <button class="btn sm">Выпустить новые резервные коды</button>
        </form>
        <div style="margin-top:14px">
          <button class="btn danger sm" v-if="!disableForm" @click="disableForm = { password: '', code: '' }">Отключить 2FA</button>
          <form v-else class="row wrap" @submit.prevent="disable">
            <input type="password" v-model="disableForm.password" placeholder="Пароль" style="max-width:180px" required>
            <input v-model="disableForm.code" placeholder="Код или резервный код" style="max-width:200px" required>
            <button class="btn danger sm">Отключить</button><button type="button" class="btn sm" @click="disableForm = null">Отмена</button>
          </form>
        </div>
      </template>

      <template v-else-if="setup">
        <ol class="small" style="line-height:1.7;padding-left:18px;margin-top:0">
          <li>Установите приложение-аутентификатор: Google Authenticator, Яндекс Ключ, Microsoft Authenticator и т.п.</li>
          <li>Отсканируйте QR-код или введите ключ вручную.</li>
          <li>Введите 6-значный код из приложения.</li>
        </ol>
        <div class="qr-box">
          <img :src="setup.qr" alt="QR-код для приложения-аутентификатора">
          <div class="form grow" style="min-width:220px">
            <label class="field"><span>Ключ для ручного ввода</span>
              <div class="row"><code style="word-break:break-all">{{ setup.secret.match(/.{1,4}/g).join(' ') }}</code>
                <button class="btn sm ghost" @click="copySecret">📋</button></div></label>
            <form class="form" @submit.prevent="enable">
              <label class="field"><span>Код из приложения</span>
                <input v-model="code" class="otp-input" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="000000" required autofocus></label>
              <div class="row"><button class="btn primary" :disabled="busy">Включить 2FA</button>
                <button type="button" class="btn" @click="setup = null">Отмена</button></div>
            </form>
          </div>
        </div>
      </template>

      <template v-else>
        <div class="row"><span class="badge">Выключена</span></div>
        <p class="muted small">Защитите аккаунт: даже если пароль утечёт, без телефона войти не получится.</p>
        <button class="btn primary" @click="startSetup">Настроить 2FA</button>
      </template>
    </div>

    <h2 style="margin:26px 0 10px">Активные сессии</h2>
    <div class="card-panel">
      <div class="session" v-for="s in sessions" :key="s.id">
        <span class="ico">{{ deviceIcon(s) }}</span>
        <div class="grow">
          <b>{{ s.browser }}, {{ s.os }}</b> <span class="badge ok" v-if="s.current">Это устройство</span>
          <div class="muted small">IP {{ s.ip || '—' }} · вход {{ fmtDateTime(s.started_at) }} · активность {{ ago(s.last_active_at) }}</div>
        </div>
        <button class="btn sm danger" v-if="!s.current" @click="revoke(s)">Завершить</button>
      </div>
      <div class="empty" v-if="!sessions.length">Нет активных сессий</div>
      <div class="row wrap" style="margin-top:12px">
        <button class="btn" v-if="sessions.length > 1" @click="revokeOthers">Выйти на всех других устройствах</button>
        <button class="btn danger" @click="revokeAll">Выйти на всех устройствах</button>
      </div>
      <p class="muted small" style="margin:12px 0 0">Завершённая сессия теряет доступ сразу: её refresh-токен отзывается,
        а access-токены перестают приниматься (растёт версия прав <code>pv</code>).</p>
    </div>
  </div>`,
};
