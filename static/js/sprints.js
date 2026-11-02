// Спринты: планирование, старт/завершение, burndown-диаграмма
import { http } from './api.js';
import { bus, can, fmtDate, go, store, toast, toastError, userName } from './store.js';
import { Avatar, Modal } from './ui.js';

const isoPlus = (days) => {
  const d = new Date(); d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};
const todayIso = () => isoPlus(0);

// ---------- burndown (SVG без библиотек) ----------
export const BurndownChart = {
  props: { data: Object, mode: { type: String, default: 'points' } },
  data: () => ({ hover: null }),
  computed: {
    W: () => 640, H: () => 240, pad: () => ({ l: 36, r: 12, t: 12, b: 28 }),
    days() { return this.data?.days || []; },
    key() { return this.mode === 'points' ? 'points' : 'cards'; },
    maxY() {
      const vals = this.days.flatMap((d) => [d[`ideal_${this.key}`], d[`remaining_${this.key}`], d[`total_${this.key}`]]).filter((v) => v != null);
      return Math.max(1, ...vals);
    },
    ticks() {
      const step = Math.max(1, Math.ceil(this.maxY / 4));
      const t = []; for (let v = 0; v <= this.maxY + step / 2; v += step) t.push(v);
      return t;
    },
    top() { return this.ticks[this.ticks.length - 1] || 1; },
    todayIndex() { return this.days.findIndex((d) => d.date === todayIso()); },
    labelEvery() { return Math.max(1, Math.ceil(this.days.length / 10)); },
  },
  methods: {
    x(i) { const n = Math.max(1, this.days.length - 1); return this.pad.l + (this.W - this.pad.l - this.pad.r) * (i / n); },
    y(v) { return this.pad.t + (this.H - this.pad.t - this.pad.b) * (1 - v / this.top); },
    path(field) {
      let d = ''; let pen = false;
      this.days.forEach((p, i) => {
        const v = p[`${field}_${this.key}`];
        if (v == null) { pen = false; return; }
        d += `${pen ? 'L' : 'M'}${this.x(i).toFixed(1)},${this.y(v).toFixed(1)} `;
        pen = true;
      });
      return d;
    },
    short(iso) { return `${iso.slice(8)}.${iso.slice(5, 7)}`; },
    onMove(e) {
      const rect = e.currentTarget.getBoundingClientRect();
      const px = (e.clientX - rect.left) / rect.width * this.W;
      const n = this.days.length - 1;
      const i = Math.round((px - this.pad.l) / (this.W - this.pad.l - this.pad.r) * n);
      this.hover = i >= 0 && i <= n ? i : null;
    },
  },
  template: `
  <div class="burndown" v-if="days.length">
    <svg :viewBox="'0 0 ' + W + ' ' + H" preserveAspectRatio="none" @mousemove="onMove" @mouseleave="hover = null">
      <g v-for="t in ticks" :key="t">
        <line :x1="pad.l" :x2="W - pad.r" :y1="y(t)" :y2="y(t)" class="grid" />
        <text :x="pad.l - 6" :y="y(t) + 4" text-anchor="end" class="lbl">{{ t }}</text>
      </g>
      <template v-for="(d, i) in days" :key="d.date">
        <text v-if="i % labelEvery === 0 || i === days.length - 1" :x="x(i)" :y="H - 8" text-anchor="middle" class="lbl">{{ short(d.date) }}</text>
      </template>
      <line v-if="todayIndex >= 0" :x1="x(todayIndex)" :x2="x(todayIndex)" :y1="pad.t" :y2="H - pad.b" class="today" />
      <path :d="path('total')" class="scope" />
      <path :d="path('ideal')" class="ideal" />
      <path :d="path('remaining')" class="actual" />
      <template v-for="(d, i) in days" :key="'p' + d.date">
        <circle v-if="d['remaining_' + key] != null" :cx="x(i)" :cy="y(d['remaining_' + key])" :r="hover === i ? 5 : 3" class="dot" />
      </template>
      <line v-if="hover !== null" :x1="x(hover)" :x2="x(hover)" :y1="pad.t" :y2="H - pad.b" class="hoverline" />
    </svg>
    <div class="bd-legend">
      <span><i class="lg actual"></i>Осталось</span><span><i class="lg ideal"></i>Идеальная линия</span>
      <span><i class="lg scope"></i>Объём спринта</span><span><i class="lg today"></i>Сегодня</span>
      <span class="spacer"></span>
      <span v-if="hover !== null" class="muted">{{ short(days[hover].date) }}:
        осталось <b>{{ days[hover]['remaining_' + key] ?? '—' }}</b>, идеал {{ days[hover]['ideal_' + key] }}</span>
    </div>
  </div>
  <div v-else class="muted small">Диаграмма появится после старта спринта.</div>`,
};

// ---------- вкладка «Спринты» проекта ----------
export const SprintsTab = {
  components: { Avatar, Modal, BurndownChart },
  props: ['projectId'],
  data: () => ({
    sprints: [], details: {}, backlog: [], open: {}, mode: 'points', loading: true,
    form: null,            // создание/редактирование спринта
    startFor: null, startEnd: '',
    completeFor: null, complete: { move_to: 'new', target_sprint_id: null, new_sprint_name: '' },
  }),
  computed: {
    canManage() { return can('board:manage', this.projectId); },
    canPlan() { return can('card:update', this.projectId); },
    active() { return this.sprints.find((s) => s.status === 'active'); },
    planned() { return this.sprints.filter((s) => s.status === 'planned'); },
    completed() { return this.sprints.filter((s) => s.status === 'completed'); },
    assignable() { return this.sprints.filter((s) => s.status !== 'completed'); },
  },
  mounted() {
    this.load();
    this.off = bus.on((ev) => {
      if (ev.project_id !== this.projectId) return;
      if (['sprint.changed', 'board.changed', 'auth.refreshed'].includes(ev.type)) { clearTimeout(this.t); this.t = setTimeout(this.load, 250); }
    });
  },
  unmounted() { this.off?.(); },
  methods: {
    userName, fmtDate, todayIso,
    async load() {
      try {
        const [sprints, backlog] = await Promise.all([
          http.get(`/api/projects/${this.projectId}/sprints`), http.get(`/api/projects/${this.projectId}/backlog`)]);
        this.sprints = sprints; this.backlog = backlog;
        const ids = sprints.filter((s) => s.status !== 'completed' || this.open[s.id]).map((s) => s.id);
        const res = await Promise.all(ids.map((id) => http.get(`/api/sprints/${id}`)));
        res.forEach((d) => { this.details[d.id] = d; });
      } catch (e) { toastError(e); } finally { this.loading = false; }
    },
    async toggle(s) {
      this.open[s.id] = !this.open[s.id];
      if (this.open[s.id] && !this.details[s.id]) this.details[s.id] = await http.get(`/api/sprints/${s.id}`);
    },
    pct(done, total) { return total ? Math.round(done / total * 100) : 0; },
    daysLeft(s) {
      if (!s.end_date) return null;
      return Math.round((new Date(`${s.end_date}T00:00:00`) - new Date(`${todayIso()}T00:00:00`)) / 86400000);
    },
    plural(n, one, few, many) {
      const m10 = n % 10; const m100 = n % 100;
      if (m10 === 1 && m100 !== 11) return one;
      if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
      return many;
    },
    // ----- CRUD -----
    newSprint() { this.form = { name: `Спринт ${this.sprints.length + 1}`, goal: '', start_date: '', end_date: '' }; },
    editSprint(s) { this.form = { id: s.id, name: s.name, goal: s.goal, start_date: s.start_date || '', end_date: s.end_date || '', status: s.status }; },
    async saveSprint() {
      const f = this.form;
      const body = { name: f.name, goal: f.goal, end_date: f.end_date || null };
      if (f.status !== 'active') body.start_date = f.start_date || null;
      try {
        if (f.id) await http.patch(`/api/sprints/${f.id}`, body);
        else await http.post(`/api/projects/${this.projectId}/sprints`, body);
        this.form = null; this.load();
      } catch (e) { toastError(e); }
    },
    async remove(s) {
      if (!confirm(`Удалить «${s.name}»? Его задачи вернутся в бэклог.`)) return;
      try { await http.del(`/api/sprints/${s.id}`); this.load(); } catch (e) { toastError(e); }
    },
    openStart(s) { this.startFor = s; this.startEnd = s.end_date && s.end_date > todayIso() ? s.end_date : isoPlus(14); },
    async start() {
      try {
        await http.post(`/api/sprints/${this.startFor.id}/start`, { end_date: this.startEnd });
        toast(`«${this.startFor.name}» начат`, 'success'); this.startFor = null; this.load();
      } catch (e) { toastError(e); }
    },
    openComplete(s) {
      const next = this.planned[0];
      this.complete = { move_to: next ? 'sprint' : 'new', target_sprint_id: next?.id || null, new_sprint_name: `Спринт ${this.sprints.length + 1}` };
      this.completeFor = s;
    },
    async finish() {
      try {
        const r = await http.post(`/api/sprints/${this.completeFor.id}/complete`, this.complete);
        toast(`«${r.name}» завершён: выполнено ${r.completed_cards}, перенесено ${r.carried_cards}`, 'success');
        this.completeFor = null; this.load();
      } catch (e) { toastError(e); }
    },
    // ----- планирование -----
    async assign(card, sprintId) {
      try { await http.patch(`/api/cards/${card.id}`, { sprint_id: sprintId ? Number(sprintId) : null }); this.load(); } catch (e) { toastError(e); }
    },
    openCard(c) { go(`/board/${c.board_id}?card=${c.id}`); },
  },
  template: `
  <div class="sprints">
    <div class="row" style="margin-bottom:14px">
      <div class="seg">
        <button :class="{on: mode === 'points'}" @click="mode = 'points'">Story points</button>
        <button :class="{on: mode === 'cards'}" @click="mode = 'cards'">Задачи</button>
      </div>
      <span class="spacer"></span>
      <button class="btn primary" v-if="canManage" @click="newSprint">＋ Новый спринт</button>
    </div>

    <div v-if="loading" class="empty">Загрузка…</div>

    <!-- Активный спринт -->
    <div class="card-panel sprint-active" v-if="active && details[active.id]">
      <div class="row wrap">
        <div class="grow">
          <div class="row" style="gap:8px"><span class="badge ok">● Идёт</span><h2>{{ active.name }}</h2></div>
          <div class="muted small" style="margin-top:4px">{{ fmtDate(active.start_date) }} — {{ fmtDate(active.end_date) }} ·
            <b :style="{color: daysLeft(active) < 0 ? 'var(--danger)' : ''}">{{ daysLeft(active) >= 0
              ? 'осталось ' + daysLeft(active) + ' ' + plural(daysLeft(active), 'день', 'дня', 'дней')
              : 'просрочен на ' + (-daysLeft(active)) + ' ' + plural(-daysLeft(active), 'день', 'дня', 'дней') }}</b></div>
          <p v-if="active.goal" style="margin:8px 0 0">🎯 {{ active.goal }}</p>
        </div>
        <template v-if="canManage">
          <button class="btn sm" @click="editSprint(active)">✎ Изменить</button>
          <button class="btn primary sm" @click="openComplete(active)">Завершить спринт</button>
        </template>
      </div>
      <div class="stats-row" style="margin:14px 0">
        <div class="stat"><div class="v">{{ active.done_points }}/{{ active.total_points }}</div><div class="l">Story points выполнено</div></div>
        <div class="stat"><div class="v">{{ active.done_cards }}/{{ active.total_cards }}</div><div class="l">Задач выполнено</div></div>
        <div class="stat"><div class="v">{{ pct(mode === 'points' ? active.done_points : active.done_cards, mode === 'points' ? active.total_points : active.total_cards) }}%</div><div class="l">Прогресс</div></div>
      </div>
      <burndown-chart :data="details[active.id].burndown" :mode="mode" />
      <h3 style="margin:16px 0 8px">Задачи спринта</h3>
      <table class="table" v-if="details[active.id].cards.length">
        <tr v-for="c in details[active.id].cards" :key="c.id" class="clickable" @click="openCard(c)" :style="{opacity: c.done ? .6 : 1}">
          <td class="mono muted" style="width:80px">{{ c.key }}</td>
          <td><span :class="'prio prio-' + c.priority"></span> <span :style="{textDecoration: c.done ? 'line-through' : ''}">{{ c.title }}</span></td>
          <td class="small muted">{{ c.board_name }}</td>
          <td><span class="badge" :class="{ok: c.done}">{{ c.column_name }}</span></td>
          <td><div class="avatars"><avatar v-for="a in c.assignees" :key="a.id" :user="a" :size="22" /></div></td>
          <td style="width:60px">{{ c.estimate != null ? c.estimate + ' SP' : '—' }}</td>
          <td style="width:40px" @click.stop><button class="btn sm ghost" v-if="canPlan && !c.done" title="Убрать в бэклог" @click="assign(c, null)">✕</button></td>
        </tr>
      </table>
      <div v-else class="muted small">В спринте нет задач — добавьте их из бэклога ниже.</div>
    </div>
    <div class="card-panel empty" v-else-if="!loading">
      Сейчас нет активного спринта.<template v-if="canManage && planned.length"> Запустите один из запланированных ниже.</template>
      <template v-else-if="canManage && !planned.length"> Создайте спринт кнопкой «Новый спринт».</template>
    </div>

    <!-- Запланированные -->
    <template v-if="planned.length">
      <h2 style="margin:22px 0 10px">Запланированы</h2>
      <div class="card-panel sprint-row" v-for="s in planned" :key="s.id">
        <div class="row wrap">
          <div class="grow"><b>{{ s.name }}</b>
            <span class="muted small"> · {{ s.total_cards }} {{ plural(s.total_cards, 'задача', 'задачи', 'задач') }}, {{ s.total_points }} SP
              <template v-if="s.start_date || s.end_date"> · {{ fmtDate(s.start_date) || '…' }} — {{ fmtDate(s.end_date) || '…' }}</template></span>
            <div v-if="s.goal" class="small" style="margin-top:3px">🎯 {{ s.goal }}</div></div>
          <template v-if="canManage">
            <button class="btn sm primary" :disabled="!!active" :title="active ? 'Сначала завершите активный спринт' : ''" @click="openStart(s)">▶ Начать</button>
            <button class="btn sm" @click="editSprint(s)">✎</button>
            <button class="btn sm danger" @click="remove(s)">✕</button>
          </template>
        </div>
        <div v-if="details[s.id] && details[s.id].cards.length" style="margin-top:8px">
          <div class="plan-card" v-for="c in details[s.id].cards" :key="c.id">
            <span class="mono muted small">{{ c.key }}</span><span :class="'prio prio-' + c.priority"></span>
            <a class="grow" @click.prevent="openCard(c)" href="#">{{ c.title }}</a>
            <span class="muted small">{{ c.estimate != null ? c.estimate + ' SP' : '' }}</span>
            <button class="btn sm ghost" v-if="canPlan" title="Убрать в бэклог" @click="assign(c, null)">✕</button>
          </div>
        </div>
      </div>
    </template>

    <!-- Бэклог -->
    <h2 style="margin:22px 0 10px">Бэклог <span class="muted small">— незавершённые задачи вне спринтов · {{ backlog.length }}</span></h2>
    <div class="card-panel">
      <div class="plan-card" v-for="c in backlog" :key="c.id">
        <span class="mono muted small">{{ c.key }}</span><span :class="'prio prio-' + c.priority"></span>
        <a class="grow" @click.prevent="openCard(c)" href="#">{{ c.title }}</a>
        <span class="muted small">{{ c.board_name }} · {{ c.column_name }}</span>
        <span class="muted small" style="width:50px;text-align:right">{{ c.estimate != null ? c.estimate + ' SP' : '—' }}</span>
        <select v-if="canPlan && assignable.length" style="width:170px" @change="assign(c, $event.target.value)">
          <option value="">В спринт…</option>
          <option v-for="s in assignable" :key="s.id" :value="s.id">{{ s.name }}{{ s.status === 'active' ? ' (идёт)' : '' }}</option>
        </select>
      </div>
      <div class="muted small" v-if="!backlog.length">Бэклог пуст.</div>
    </div>

    <!-- Завершённые -->
    <template v-if="completed.length">
      <h2 style="margin:22px 0 10px">Завершённые</h2>
      <div class="card-panel sprint-row" v-for="s in completed" :key="s.id">
        <div class="row wrap" style="cursor:pointer" @click="toggle(s)">
          <span>{{ open[s.id] ? '▾' : '▸' }}</span>
          <div class="grow"><b>{{ s.name }}</b> <span class="muted small">{{ fmtDate(s.start_date) }} — {{ fmtDate(s.end_date) }}</span></div>
          <span class="badge ok">✓ {{ s.completed_cards }} {{ plural(s.completed_cards, 'задача', 'задачи', 'задач') }} · {{ s.completed_points }} SP</span>
          <span class="badge" v-if="s.carried_cards">↪ перенесено {{ s.carried_cards }}</span>
        </div>
        <div v-if="open[s.id] && details[s.id]" style="margin-top:12px">
          <p v-if="s.goal" class="small" style="margin:0 0 8px">🎯 {{ s.goal }}</p>
          <burndown-chart :data="details[s.id].burndown" :mode="mode" />
        </div>
      </div>
    </template>

    <!-- модалки -->
    <modal v-if="form" :title="form.id ? 'Спринт' : 'Новый спринт'" @close="form = null">
      <form class="form" id="sf" @submit.prevent="saveSprint">
        <label class="field"><span>Название</span><input v-model="form.name" required autofocus></label>
        <label class="field"><span>Цель спринта</span><textarea v-model="form.goal" rows="2" placeholder="Чего хотим достичь"></textarea></label>
        <div class="row">
          <label class="field grow" v-if="form.status !== 'active'"><span>Начало (план)</span><input type="date" v-model="form.start_date"></label>
          <label class="field grow"><span>Окончание</span><input type="date" v-model="form.end_date" :required="form.status === 'active'"></label>
        </div>
      </form>
      <template #foot><button class="btn" @click="form = null">Отмена</button><button class="btn primary" form="sf">Сохранить</button></template>
    </modal>

    <modal v-if="startFor" :title="'Начать «' + startFor.name + '»'" @close="startFor = null">
      <form class="form" id="ss" @submit.prevent="start">
        <p class="muted small" style="margin:0">Спринт начнётся сегодня. В нём {{ startFor.total_cards }} {{ plural(startFor.total_cards, 'задача', 'задачи', 'задач') }}
          ({{ startFor.total_points }} SP) — это станет стартовой точкой burndown.</p>
        <label class="field"><span>Дата окончания</span><input type="date" v-model="startEnd" :min="todayIso()" required></label>
      </form>
      <template #foot><button class="btn" @click="startFor = null">Отмена</button><button class="btn primary" form="ss">▶ Начать</button></template>
    </modal>

    <modal v-if="completeFor" :title="'Завершить «' + completeFor.name + '»'" @close="completeFor = null">
      <form class="form" id="sc" @submit.prevent="finish">
        <p style="margin:0">Выполнено <b>{{ completeFor.done_cards }}</b> из {{ completeFor.total_cards }} задач
          ({{ completeFor.done_points }} из {{ completeFor.total_points }} SP).</p>
        <template v-if="completeFor.remaining_cards">
          <p class="muted small" style="margin:0">Незавершённые задачи ({{ completeFor.remaining_cards }}) перенести:</p>
          <label class="row"><input type="radio" value="new" v-model="complete.move_to"> в новый спринт
            <input v-if="complete.move_to === 'new'" v-model="complete.new_sprint_name" style="max-width:200px"></label>
          <label class="row" v-if="planned.length"><input type="radio" value="sprint" v-model="complete.move_to"> в запланированный
            <select v-if="complete.move_to === 'sprint'" v-model="complete.target_sprint_id" style="max-width:200px">
              <option v-for="s in planned" :key="s.id" :value="s.id">{{ s.name }}</option></select></label>
          <label class="row"><input type="radio" value="backlog" v-model="complete.move_to"> в бэклог</label>
        </template>
        <p v-else class="muted small" style="margin:0">Все задачи выполнены 🎉</p>
      </form>
      <template #foot><button class="btn" @click="completeFor = null">Отмена</button><button class="btn primary" form="sc">Завершить</button></template>
    </modal>
  </div>`,
};
