// Переиспользуемые UI-компоненты
import { http } from './api.js';
import { userName } from './store.js';

export const Avatar = {
  props: { user: Object, size: { type: Number, default: 28 } },
  computed: {
    initials() {
      const n = userName(this.user);
      return n.split(/\s+/).map((p) => p[0]).slice(0, 2).join('').toUpperCase();
    },
    style() {
      return {
        width: `${this.size}px`, height: `${this.size}px`, fontSize: `${Math.round(this.size * 0.4)}px`,
        background: this.user?.color || '#94a3b8',
      };
    },
  },
  template: `<span class="avatar" :style="style" :title="user ? userName(user) + ' (@' + user.username + ')' : ''">{{ initials }}</span>`,
  methods: { userName },
};

export const Modal = {
  props: { title: String, wide: Boolean },
  emits: ['close'],
  mounted() {
    this.onKey = (e) => { if (e.key === 'Escape') this.$emit('close'); };
    document.addEventListener('keydown', this.onKey);
  },
  unmounted() { document.removeEventListener('keydown', this.onKey); },
  template: `
    <div class="modal-backdrop" @mousedown.self="$emit('close')">
      <div class="modal" :class="{ wide }">
        <div class="modal-head">
          <slot name="head"><h2>{{ title }}</h2></slot>
          <button class="btn ghost icon" @click="$emit('close')" title="Закрыть">✕</button>
        </div>
        <div class="modal-body"><slot /></div>
        <div class="modal-foot" v-if="$slots.foot"><slot name="foot" /></div>
      </div>
    </div>`,
};

// Поиск пользователя по логину/имени/почте
export const UserPicker = {
  components: { Avatar },
  props: { placeholder: { type: String, default: 'Найти пользователя…' }, exclude: { type: Array, default: () => [] } },
  emits: ['pick'],
  data: () => ({ q: '', results: [], open: false }),
  watch: {
    q(v) {
      clearTimeout(this.t);
      if (!v.trim()) { this.results = []; return; }
      this.t = setTimeout(async () => {
        const res = await http.get(`/api/users/search?q=${encodeURIComponent(v)}`);
        this.results = res.filter((u) => !this.exclude.includes(u.id));
        this.open = true;
      }, 200);
    },
  },
  methods: {
    pick(u) { this.$emit('pick', u); this.q = ''; this.results = []; this.open = false; },
    blur() { setTimeout(() => { this.open = false; }, 150); },
    userName,
  },
  template: `
    <div class="dropdown grow">
      <input v-model="q" :placeholder="placeholder" @focus="open = true" @blur="blur">
      <div class="dropdown-menu" v-if="open && results.length" style="left:0;right:auto;width:100%">
        <button class="item row" v-for="u in results" :key="u.id" @mousedown.prevent="pick(u)">
          <avatar :user="u" :size="24" /> <span>{{ userName(u) }}</span> <span class="muted small">@{{ u.username }}</span>
        </button>
      </div>
    </div>`,
};

export const RoleBadge = {
  props: { role: String },
  computed: {
    label() {
      return { owner: 'Владелец', admin: 'Админ', member: 'Участник', manager: 'Менеджер', viewer: 'Наблюдатель' }[this.role] || this.role;
    },
  },
  template: `<span class="badge" :class="'role-' + role" v-if="role">{{ label }}</span>`,
};

// Директива v-sortable: тонкая обёртка над SortableJS
export const sortableDirective = {
  mounted(el, binding) {
    // forceFallback: перетаскивание на mouse/pointer-событиях — одинаково ведёт себя во всех браузерах
    el._sortable = Sortable.create(el, { animation: 160, forceFallback: true, fallbackTolerance: 4, ...binding.value });
  },
  updated(el, binding) {
    el._sortable?.option('disabled', !!binding.value.disabled);
  },
  unmounted(el) {
    el._sortable?.destroy();
  },
};

// Возвращает перетащенный элемент на исходное место: DOM остаётся под контролем Vue
export function revertSortable(evt) {
  const { item, from, oldIndex } = evt;
  item.remove();
  from.insertBefore(item, from.children[oldIndex] || null);
}
