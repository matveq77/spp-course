// Канбан-доска и модальное окно карточки
import { fetchBlob, http, uploadFile } from './api.js';
import { updatePresence } from './presence.js';
import {
  ago, bus, can, dueState, fmtDate, fmtDateTime, go, highlightMentions, PRIORITY_RU, setQuery, store, toast,
  toastError, userName,
} from './store.js';
import { settings } from './settings.js';
import { Avatar, Modal, revertSortable, RoleBadge, sortableDirective } from './ui.js';

// ===================== КАРТОЧКА (модалка) =====================
const MAX_UPLOAD_MB = 10;

export const CardModal = {
  components: { Avatar, Modal },
  props: ['cardId', 'board', 'viewers'],
  emits: ['close'],
  data: () => ({
    card: null, activity: [], editDesc: false, desc: '', title: '', newItem: '', comment: '',
    editingComment: null, editText: '', tab: 'comments',
    descBase: '', theirDesc: null,                  // для обнаружения одновременной правки описания
    previews: {}, lightbox: null, uploading: 0, dragOver: false,
  }),
  computed: {
    pid() { return this.board.project.id; },
    canEdit() { return can('card:update', this.pid); },
    canAssign() { return can('card:assign', this.pid); },
    canMove() { return can('card:move', this.pid); },
    canDelete() { return can('card:delete', this.pid); },
    canComment() { return can('comment:create', this.pid); },
    canModerate() { return can('comment:moderate', this.pid); },
    assigneeIds() { return this.card.assignees.map((a) => a.id); },
    labelIds() { return this.card.labels.map((l) => l.id); },
    checkPct() { const c = this.card.checklist; return c.total ? Math.round(c.done / c.total * 100) : 0; },
    isDone() { return this.board.columns.find((c) => c.id === this.card.column_id)?.is_done; },
    store: () => store,
    MAX_UPLOAD_MB: () => MAX_UPLOAD_MB,
    // кто ещё (кроме меня) сейчас открыл эту карточку
    others() {
      return (this.viewers || []).filter((v) => v.card_id === this.cardId && v.id !== store.user.id)
        .map((v) => ({ ...v, user: this.board.members.find((m) => m.id === v.id) || { full_name: v.name } }));
    },
    descEditors() { return this.others.filter((v) => v.editing === 'description'); },
    namesOf() { return (list) => list.map((v) => userName(v.user)).join(', '); },
  },
  watch: {
    cardId() { this.load(); updatePresence({ card_id: this.cardId, editing: null }); },
    editDesc(v) { updatePresence({ editing: v ? 'description' : null }); },
  },
  mounted() {
    updatePresence({ card_id: this.cardId, editing: null });
    this.load();
    this.off = bus.on((ev) => {
      if (ev.type !== 'board.changed' || ev.data.card_id !== this.cardId) return;
      if (ev.data.deleted) { toast('Карточку удалили'); this.$emit('close'); return; }
      if (ev.actor_id !== store.user.id) { clearTimeout(this.t); this.t = setTimeout(() => this.load(true), 150); }
    });
  },
  unmounted() {
    this.off?.();
    updatePresence({ card_id: null });
    Object.values(this.previews).forEach((u) => URL.revokeObjectURL(u));
  },
  methods: {
    userName, fmtDate, fmtDateTime, ago, highlightMentions, dueState,
    async load(silent = false) {
      try {
        const card = await http.get(`/api/cards/${this.cardId}`);
        this.card = card;
        this.title = card.title;
        if (!this.editDesc) this.desc = card.description;
        this.loadActivity();
        this.loadPreviews();
      } catch (e) { if (!silent) toastError(e); this.$emit('close'); }
    },
    async loadActivity() {
      this.activity = await http.get(`/api/projects/${this.pid}/activity?card_id=${this.cardId}&limit=50`);
    },
    async patch(body) {
      try { this.card = await http.patch(`/api/cards/${this.cardId}`, body); this.loadActivity(); } catch (e) { toastError(e); this.load(); }
    },
    saveTitle() { if (this.title.trim() && this.title !== this.card.title) this.patch({ title: this.title.trim() }); },
    startDesc() {
      if (!this.canEdit || this.editDesc) return;
      this.descBase = this.card.description;
      this.desc = this.card.description;
      this.theirDesc = null;
      this.editDesc = true;
    },
    cancelDesc() { this.editDesc = false; this.desc = this.card.description; this.theirDesc = null; },
    async saveDesc() {
      // перед сохранением проверяем, не изменил ли описание кто-то другой, пока мы печатали
      let latest;
      try { latest = await http.get(`/api/cards/${this.cardId}`); } catch (e) { toastError(e); return; }
      const changedByOther = latest.description !== this.descBase && latest.description !== this.desc;
      if (changedByOther && !confirm('Пока вы редактировали, описание изменил другой участник.\n\n'
        + 'OK — сохранить вашу версию поверх.\nОтмена — посмотреть их версию и объединить вручную.')) {
        this.theirDesc = latest.description;
        this.descBase = latest.description;
        return;
      }
      this.editDesc = false;
      this.theirDesc = null;
      if (this.desc !== latest.description) await this.patch({ description: this.desc });
      else this.card = { ...this.card, description: latest.description };
    },
    setDue(v) { this.patch(v ? { due_date: v } : { clear_due_date: true }); },
    setEstimate(v) { this.patch({ estimate: v === '' ? null : Number(v) }); },
    async move(columnId) {
      const col = this.board.columns.find((c) => c.id === Number(columnId));
      try {
        await http.post(`/api/cards/${this.cardId}/move`, { column_id: col.id, index: col.cards.length });
        this.load();
      } catch (e) { toastError(e); this.load(); }
    },
    async toggleAssignee(uid) {
      const ids = this.assigneeIds.includes(uid) ? this.assigneeIds.filter((x) => x !== uid) : [...this.assigneeIds, uid];
      try { this.card = await http.put(`/api/cards/${this.cardId}/assignees`, { ids }); } catch (e) { toastError(e); }
    },
    async toggleLabel(lid) {
      const ids = this.labelIds.includes(lid) ? this.labelIds.filter((x) => x !== lid) : [...this.labelIds, lid];
      try { this.card = await http.put(`/api/cards/${this.cardId}/labels`, { ids }); } catch (e) { toastError(e); }
    },
    async addItem() {
      if (!this.newItem.trim()) return;
      try { this.card = await http.post(`/api/cards/${this.cardId}/checklist`, { text: this.newItem.trim() }); this.newItem = ''; } catch (e) { toastError(e); }
    },
    async toggleItem(it) {
      try { this.card = await http.patch(`/api/checklist/${it.id}`, { done: !it.done }); } catch (e) { toastError(e); }
    },
    async delItem(it) { try { this.card = await http.del(`/api/checklist/${it.id}`); } catch (e) { toastError(e); } },
    async addComment() {
      if (!this.comment.trim()) return;
      try { await http.post(`/api/cards/${this.cardId}/comments`, { text: this.comment }); this.comment = ''; this.load(true); } catch (e) { toastError(e); }
    },
    startEditComment(c) { this.editingComment = c.id; this.editText = c.text; },
    async saveComment(c) {
      try { await http.patch(`/api/comments/${c.id}`, { text: this.editText }); this.editingComment = null; this.load(true); } catch (e) { toastError(e); }
    },
    async delComment(c) {
      if (!confirm('Удалить комментарий?')) return;
      try { await http.del(`/api/comments/${c.id}`); this.load(true); } catch (e) { toastError(e); }
    },
    async archive() { await this.patch({ is_archived: !this.card.is_archived }); if (this.card.is_archived) this.$emit('close'); },
    async destroy() {
      if (!confirm(`Удалить карточку ${this.card.key} безвозвратно?`)) return;
      try { await http.del(`/api/cards/${this.cardId}`); this.$emit('close'); } catch (e) { toastError(e); }
    },
    // ----- вложения -----
    async loadPreviews() {
      for (const a of this.card.attachments) {
        if (a.is_image && !this.previews[a.id]) {
          try { this.previews[a.id] = URL.createObjectURL(await fetchBlob(`/api/attachments/${a.id}`)); } catch { /* нет доступа */ }
        }
      }
    },
    pickFiles(e) { this.uploadAll([...e.target.files]); e.target.value = ''; },
    onDrop(e) { this.dragOver = false; if (this.canEdit) this.uploadAll([...e.dataTransfer.files]); },
    async uploadAll(files) {
      for (const f of files) {
        if (f.size > MAX_UPLOAD_MB * 1024 * 1024) { toast(`«${f.name}» больше ${MAX_UPLOAD_MB} МБ`, 'error'); continue; }
        this.uploading += 1;
        try { await uploadFile(`/api/cards/${this.cardId}/attachments`, f); } catch (e) { toastError(e); } finally { this.uploading -= 1; }
      }
      this.load(true);
    },
    async download(a) {
      try {
        const url = URL.createObjectURL(await fetchBlob(`/api/attachments/${a.id}`));
        const link = document.createElement('a');
        link.href = url; link.download = a.filename;
        document.body.appendChild(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 2000);
      } catch (e) { toastError(e); }
    },
    async removeAttachment(a) {
      if (!confirm(`Удалить файл «${a.filename}»?`)) return;
      try { await http.del(`/api/attachments/${a.id}`); this.load(true); } catch (e) { toastError(e); }
    },
    canRemove(a) { return (a.uploader?.id === store.user.id && this.canEdit) || this.canDelete; },
    fileSize(n) { return n < 1024 ? `${n} Б` : n < 1048576 ? `${Math.round(n / 1024)} КБ` : `${(n / 1048576).toFixed(1)} МБ`; },
    fileIcon(a) {
      const ext = a.filename.split('.').pop().toLowerCase();
      if (['pdf'].includes(ext)) return '📕';
      if (['doc', 'docx', 'odt', 'rtf', 'txt', 'md'].includes(ext)) return '📄';
      if (['xls', 'xlsx', 'csv', 'ods'].includes(ext)) return '📊';
      if (['zip', 'rar', '7z', 'tar', 'gz'].includes(ext)) return '🗜';
      if (['ppt', 'pptx'].includes(ext)) return '📽';
      return '📎';
    },
    copyLink() {
      navigator.clipboard?.writeText(location.href);
      toast('Ссылка скопирована');
    },
  },
  template: `
  <modal wide @close="$emit('close')">
    <template #head>
      <div class="grow" v-if="card">
        <div class="row small muted" style="gap:8px"><span class="mono">{{ card.key }}</span> · в колонке <b>{{ card.column_name }}</b>
          <span class="badge" v-if="card.is_archived">в архиве</span></div>
        <input v-if="canEdit" v-model="title" @blur="saveTitle" @keydown.enter="$event.target.blur()"
               style="font-size:18px;font-weight:650;border-color:transparent;padding:4px 6px;margin-left:-6px">
        <h2 v-else style="margin-top:4px">{{ card.title }}</h2>
      </div>
      <div class="grow" v-else>Загрузка…</div>
      <div class="avatars" v-if="others.length" :title="'Сейчас открыта также у: ' + namesOf(others)">
        <avatar v-for="v in others" :key="v.id" :user="v.user" :size="26" /></div>
      <button class="btn ghost icon" @click="copyLink" title="Скопировать ссылку">🔗</button>
    </template>

    <div class="card-layout" v-if="card">
      <div>
        <div class="row wrap" v-if="card.labels.length" style="margin-bottom:12px">
          <span class="chip" v-for="l in card.labels" :key="l.id" :style="{background: l.color}">{{ l.name }}</span>
        </div>

        <div class="section-title">Описание
          <button class="btn sm ghost" v-if="canEdit && !editDesc" @click="startDesc">✎</button></div>
        <div class="presence-note" :class="{warn: editDesc}" v-if="descEditors.length">
          ✏️ <b>{{ namesOf(descEditors) }}</b> сейчас {{ descEditors.length > 1 ? 'редактируют' : 'редактирует' }} описание<template v-if="editDesc"> —
          возможен конфликт: при сохранении мы проверим, не изменилось ли оно</template>.
        </div>
        <template v-if="editDesc">
          <div class="presence-note warn" v-if="theirDesc !== null">
            <b>Версия другого участника</b> (уже сохранена). Объедините с вашей и нажмите «Сохранить»:
            <div class="desc" style="margin-top:6px;cursor:default">{{ theirDesc || '(пусто)' }}</div>
          </div>
          <textarea v-model="desc" rows="6" autofocus placeholder="Подробности задачи…"></textarea>
          <div class="row" style="margin-top:6px"><button class="btn primary sm" @click="saveDesc">Сохранить</button>
            <button class="btn sm" @click="cancelDesc">Отмена</button></div>
        </template>
        <div v-else class="desc" @click="startDesc">{{ card.description || (canEdit ? 'Нажмите, чтобы добавить описание…' : 'Нет описания') }}</div>

        <div class="section-title">Чек-лист <span class="muted small" v-if="card.checklist.total">{{ card.checklist.done }}/{{ card.checklist.total }}</span></div>
        <div class="progress" v-if="card.checklist.total" style="margin-bottom:8px"><div :style="{width: checkPct + '%'}"></div></div>
        <div v-for="it in card.checklist_items" :key="it.id" class="check-item">
          <input type="checkbox" :checked="it.done" :disabled="!canEdit" @change="toggleItem(it)">
          <span class="txt grow" :class="{done: it.done}">{{ it.text }}</span>
          <button class="btn sm ghost del" v-if="canEdit" @click="delItem(it)">✕</button>
        </div>
        <form class="row" v-if="canEdit" @submit.prevent="addItem" style="margin-top:6px">
          <input v-model="newItem" placeholder="Новый пункт…"><button class="btn sm">Добавить</button>
        </form>

        <div class="section-title">Вложения <span class="muted small" v-if="card.attachments.length">{{ card.attachments.length }}</span>
          <span class="spacer"></span>
          <label class="btn sm" v-if="canEdit">📎 Прикрепить<input type="file" multiple hidden @change="pickFiles"></label></div>
        <div class="dropzone" :class="{over: dragOver}" v-if="canEdit || card.attachments.length"
             @dragover.prevent="canEdit && (dragOver = true)" @dragleave="dragOver = false" @drop.prevent="onDrop">
          <div class="attachments">
            <div class="att" v-for="a in card.attachments" :key="a.id">
              <div class="thumb" @click="a.is_image ? (lightbox = a) : download(a)" :title="a.is_image ? 'Открыть' : 'Скачать'">
                <img v-if="a.is_image && previews[a.id]" :src="previews[a.id]" :alt="a.filename">
                <span v-else>{{ a.is_image ? '🖼' : fileIcon(a) }}</span>
              </div>
              <div class="info">
                <div class="name" :title="a.filename">{{ a.filename }}</div>
                <div class="muted small">{{ fileSize(a.size) }} · {{ userName(a.uploader) }} · {{ ago(a.created_at) }}</div>
                <div class="row" style="gap:4px;margin-top:4px">
                  <button class="btn sm" @click="download(a)">⬇ Скачать</button>
                  <button class="btn sm danger" v-if="canRemove(a)" @click="removeAttachment(a)">✕</button>
                </div>
              </div>
            </div>
          </div>
          <div class="muted small" v-if="canEdit" style="text-align:center;padding:6px">
            {{ uploading ? 'Загрузка… (' + uploading + ')' : 'Перетащите файлы сюда · до ' + MAX_UPLOAD_MB + ' МБ' }}</div>
        </div>

        <div class="tabs" style="margin-top:22px">
          <button :class="{active: tab==='comments'}" @click="tab='comments'">Комментарии · {{ card.comments.length }}</button>
          <button :class="{active: tab==='history'}" @click="tab='history'">История</button>
        </div>
        <template v-if="tab==='comments'">
          <div class="comment" v-if="canComment">
            <avatar :user="store.user" :size="30" />
            <div class="grow">
              <textarea v-model="comment" rows="2" placeholder="Комментарий… @логин — упомянуть (Ctrl+Enter — отправить)" @keydown.ctrl.enter="addComment" style="min-height:60px"></textarea>
              <button class="btn primary sm" style="margin-top:6px" @click="addComment" :disabled="!comment.trim()">Отправить</button>
            </div>
          </div>
          <div class="comment" v-for="c in [...card.comments].reverse()" :key="c.id">
            <avatar :user="c.author" :size="30" />
            <div class="bubble">
              <div class="row small"><b>{{ userName(c.author) }}</b><span class="muted">{{ ago(c.created_at) }}<template v-if="c.edited_at"> (изм.)</template></span>
                <span class="spacer"></span>
                <button class="btn sm ghost" v-if="c.author && c.author.id === store.user.id && editingComment !== c.id" @click="startEditComment(c)">✎</button>
                <button class="btn sm ghost" v-if="(c.author && c.author.id === store.user.id) || canModerate" @click="delComment(c)">✕</button></div>
              <template v-if="editingComment === c.id">
                <textarea v-model="editText" rows="2"></textarea>
                <div class="row" style="margin-top:4px"><button class="btn sm primary" @click="saveComment(c)">Сохранить</button><button class="btn sm" @click="editingComment=null">Отмена</button></div>
              </template>
              <div class="txt" v-else v-html="highlightMentions(c.text)"></div>
            </div>
          </div>
        </template>
        <template v-else>
          <div class="feed-item small" v-for="a in activity" :key="a.id">
            <avatar :user="a.user" :size="22" /><div><b>{{ userName(a.user) }}</b> {{ a.message }} <div class="muted">{{ fmtDateTime(a.created_at) }}</div></div>
          </div>
        </template>
      </div>

      <div class="side">
        <div><h4>Колонка</h4>
          <select :value="card.column_id" :disabled="!canMove" @change="move($event.target.value)">
            <option v-for="c in board.columns" :key="c.id" :value="c.id">{{ c.name }}</option></select></div>
        <div v-if="board.sprints.length || card.sprint_id"><h4>Спринт</h4>
          <select :value="card.sprint_id || ''" :disabled="!canEdit" @change="patch({sprint_id: $event.target.value ? Number($event.target.value) : null})">
            <option value="">— Бэклог —</option>
            <option v-for="s in board.sprints" :key="s.id" :value="s.id">{{ s.name }}{{ s.status === 'active' ? ' (идёт)' : '' }}</option>
            <option v-if="card.sprint_id && !board.sprints.some(s => s.id === card.sprint_id)" :value="card.sprint_id">завершённый спринт</option>
          </select></div>
        <div><h4>Приоритет</h4>
          <select :value="card.priority" :disabled="!canEdit" @change="patch({priority: $event.target.value})">
            <option v-for="(l, k) in PRIORITY_RU" :key="k" :value="k">{{ l }}</option></select></div>
        <div class="row">
          <div class="grow"><h4>Срок</h4><input type="date" :value="card.due_date || ''" :disabled="!canEdit" @change="setDue($event.target.value)"
            :style="{borderColor: dueState(card.due_date, isDone)==='overdue' ? 'var(--danger)' : ''}"></div>
          <div style="width:80px"><h4>Оценка</h4><input type="number" min="0" max="100" :value="card.estimate ?? ''" :disabled="!canEdit" @change="setEstimate($event.target.value)" placeholder="SP"></div>
        </div>
        <div><h4>Исполнители</h4>
          <div class="toggle-list">
            <button v-for="m in board.members" :key="m.id" :class="{on: assigneeIds.includes(m.id)}" :disabled="!canAssign" @click="toggleAssignee(m.id)">
              <avatar :user="m" :size="22" /> {{ userName(m) }} <span class="check" v-if="assigneeIds.includes(m.id)">✓</span></button>
          </div></div>
        <div><h4>Метки</h4>
          <div class="toggle-list">
            <button v-for="l in board.labels" :key="l.id" :class="{on: labelIds.includes(l.id)}" :disabled="!canEdit" @click="toggleLabel(l.id)">
              <span class="chip" :style="{background: l.color}">{{ l.name }}</span> <span class="check" v-if="labelIds.includes(l.id)">✓</span></button>
          </div></div>
        <div v-if="lightbox" class="lightbox" @click="lightbox = null">
          <img :src="previews[lightbox.id]" :alt="lightbox.filename">
          <div class="cap">{{ lightbox.filename }} · {{ fileSize(lightbox.size) }}</div>
        </div>
        <div class="small muted">Создал(а) {{ userName(card.created_by) }}<br>{{ fmtDateTime(card.created_at) }}<br>Обновлено {{ ago(card.updated_at) }}</div>
        <div class="form" style="gap:6px">
          <button class="btn" v-if="canEdit" @click="archive">{{ card.is_archived ? '↩ Вернуть из архива' : '🗄 В архив' }}</button>
          <button class="btn danger" v-if="canDelete" @click="destroy">🗑 Удалить</button>
        </div>
      </div>
    </div>
  </modal>`,
};

// ===================== ДОСКА =====================
export const BoardView = {
  components: { Avatar, Modal, CardModal, RoleBadge },
  directives: { sortable: sortableDirective },
  props: ['id'],
  data: () => ({
    board: null, error: '', dragging: false, needReload: false, flash: new Set(), viewers: [],
    f: { q: '', label: '', assignee: '', priority: '', sprint: '', mine: false, overdue: false },
    adding: null, newTitle: '', newColumn: '', editingCol: null, colName: '', archived: null, colMenu: null,
    editName: false, boardName: '',
  }),
  computed: {
    store: () => store,
    settings: () => settings,
    pid() { return this.board?.project.id; },
    filtersOn() { const f = this.f; return !!(f.q || f.label || f.assignee || f.priority || f.sprint || f.mine || f.overdue); },
    activeSprint() { return this.board?.sprints.find((s) => s.status === 'active'); },
    sprintName() { return (id) => this.board.sprints.find((s) => s.id === id)?.name; },
    canMove() { return can('card:move', this.pid); },
    canManage() { return can('board:manage', this.pid); },
    cardId() { return store.route.query.card ? Number(store.route.query.card) : null; },
    cardSortable() {
      return {
        group: 'cards', disabled: !this.canMove || this.filtersOn, onStart: () => { this.dragging = true; },
        onEnd: this.onCardEnd, ghostClass: 'sortable-ghost', dragClass: 'sortable-drag',
      };
    },
    columnSortable() {
      return { disabled: !this.canManage, handle: '.column-head', draggable: '.column.real', onStart: () => { this.dragging = true; }, onEnd: this.onColumnEnd };
    },
    totalCards() { return this.board.columns.reduce((s, c) => s + c.cards.length, 0); },
  },
  async mounted() {
    this.closeMenus = () => { this.colMenu = null; };
    document.addEventListener('click', this.closeMenus);
    this.off = bus.on(this.onEvent);
    updatePresence({ board_id: this.id });
    await this.load();
  },
  unmounted() {
    document.removeEventListener('click', this.closeMenus);
    this.off?.();
    updatePresence({ board_id: null });
  },
  methods: {
    userName, fmtDate, dueState, can,
    async load() {
      try {
        const b = await http.get(`/api/boards/${this.id}`);
        this.board = b; this.viewers = b.viewers; this.error = '';
        store.crumbs = [{ text: b.project.team_name, href: `#/team/${b.project.team_id}` },
          { text: b.project.name, href: `#/project/${b.project.id}` }, { text: b.name }];
        if (!this.editName) this.boardName = b.name;
      } catch (e) { this.error = e.message; }
    },
    // другие пользователи, у которых сейчас открыта эта карточка
    cardViewers(c) {
      return this.viewers.filter((v) => v.card_id === c.id && v.id !== store.user.id)
        .map((v) => ({ ...v, user: this.board.members.find((m) => m.id === v.id) || { full_name: v.name } }));
    },
    onEvent(ev) {
      if (ev.type === 'presence' && ev.data.board_id === this.id) { this.viewers = ev.data.users; return; }
      if (ev.type === 'board.deleted' && ev.data.board_id === this.id) { toast('Доску удалили'); go(`/project/${ev.project_id}`); return; }
      if (ev.type === 'auth.refreshed') { this.load(); return; }
      if (['project.labels', 'project.members', 'sprint.changed'].includes(ev.type) && ev.project_id === this.pid) {
        clearTimeout(this.t); this.t = setTimeout(this.load, 150); return;
      }
      if (ev.type !== 'board.changed' || ev.data.board_id !== this.id) return;
      if (ev.actor_id !== store.user.id && ev.data.card_id) {
        const id = ev.data.card_id;
        if (settings.liveHighlight) this.flash.add(id);
        setTimeout(() => this.flash.delete(id), 1700);
      }
      if (this.dragging) { this.needReload = true; return; }
      clearTimeout(this.t);
      this.t = setTimeout(this.load, 120);
    },
    afterDrag() {
      this.dragging = false;
      if (this.needReload) { this.needReload = false; this.load(); }
    },

    // ----- фильтрация -----
    visible(card) {
      const f = this.f;
      if (f.q && !(`${card.key} ${card.title}`.toLowerCase().includes(f.q.toLowerCase()))) return false;
      if (f.label && !card.labels.some((l) => l.id === Number(f.label))) return false;
      if (f.assignee && !card.assignees.some((a) => a.id === Number(f.assignee))) return false;
      if (f.priority && card.priority !== f.priority) return false;
      if (f.sprint === 'none' && card.sprint_id) return false;
      if (f.sprint && f.sprint !== 'none' && card.sprint_id !== Number(f.sprint)) return false;
      if (f.mine && !card.assignees.some((a) => a.id === store.user.id)) return false;
      if (f.overdue && dueState(card.due_date) !== 'overdue') return false;
      return true;
    },
    cardsOf(col) { return this.filtersOn ? col.cards.filter(this.visible) : col.cards; },
    resetFilters() { this.f = { q: '', label: '', assignee: '', priority: '', sprint: '', mine: false, overdue: false }; },

    // ----- drag & drop -----
    onCardEnd(evt) {
      const cardId = Number(evt.item.dataset.id);
      const fromCol = Number(evt.from.dataset.col);
      const toCol = Number(evt.to.dataset.col);
      const index = evt.newIndex;
      if (fromCol === toCol && evt.oldIndex === index) { this.afterDrag(); return; }
      revertSortable(evt);
      const from = this.board.columns.find((c) => c.id === fromCol);
      const to = this.board.columns.find((c) => c.id === toCol);
      if (fromCol !== toCol && to.wip_limit && to.cards.length >= to.wip_limit) {
        toast(`WIP-лимит колонки «${to.name}»: ${to.wip_limit}`, 'error');
        this.afterDrag();
        return;
      }
      const [card] = from.cards.splice(from.cards.findIndex((c) => c.id === cardId), 1);
      card.column_id = toCol;
      to.cards.splice(index, 0, card);
      http.post(`/api/cards/${cardId}/move`, { column_id: toCol, index })
        .catch((e) => { toastError(e); this.needReload = true; })
        .finally(() => this.afterDrag());
    },
    onColumnEnd(evt) {
      const from = evt.oldDraggableIndex; const to = evt.newDraggableIndex;
      if (from === to) { this.afterDrag(); return; }
      revertSortable(evt);
      const [col] = this.board.columns.splice(from, 1);
      this.board.columns.splice(to, 0, col);
      http.post(`/api/columns/${col.id}/move`, { index: to })
        .catch((e) => { toastError(e); this.needReload = true; })
        .finally(() => this.afterDrag());
    },

    // ----- карточки -----
    openCard(c) { setQuery({ card: c.id }); },
    closeCard() { setQuery({ card: null }); },
    startAdd(col) { this.adding = col.id; this.newTitle = ''; this.$nextTick(() => this.$refs[`add${col.id}`]?.[0]?.focus()); },
    async addCard(col) {
      const title = this.newTitle.trim();
      if (!title) { this.adding = null; return; }
      try {
        const body = { column_id: col.id, title };
        if (this.f.mine && can('card:assign', this.pid)) body.assignee_ids = [store.user.id];
        const card = await http.post('/api/cards', body);
        col.cards.push(card);
        this.newTitle = '';
      } catch (e) { toastError(e); }
    },

    // ----- колонки -----
    async addColumn() {
      if (!this.newColumn.trim()) return;
      try { await http.post(`/api/boards/${this.id}/columns`, { name: this.newColumn.trim() }); this.newColumn = ''; this.load(); } catch (e) { toastError(e); }
    },
    startRename(col) { if (!this.canManage) return; this.editingCol = col.id; this.colName = col.name; this.colMenu = null; },
    async rename(col) {
      this.editingCol = null;
      if (!this.colName.trim() || this.colName === col.name) return;
      try { await http.patch(`/api/columns/${col.id}`, { name: this.colName.trim() }); col.name = this.colName.trim(); } catch (e) { toastError(e); }
    },
    async setWip(col) {
      this.colMenu = null;
      const v = prompt(`WIP-лимит для «${col.name}» (0 — без лимита):`, col.wip_limit || 0);
      if (v === null) return;
      try { await http.patch(`/api/columns/${col.id}`, { wip_limit: Math.max(0, parseInt(v, 10) || 0) }); this.load(); } catch (e) { toastError(e); }
    },
    async toggleDone(col) {
      this.colMenu = null;
      try { await http.patch(`/api/columns/${col.id}`, { is_done: !col.is_done }); this.load(); } catch (e) { toastError(e); }
    },
    async delColumn(col) {
      this.colMenu = null;
      if (!confirm(`Удалить колонку «${col.name}»?`)) return;
      try { await http.del(`/api/columns/${col.id}`); this.load(); } catch (e) { toastError(e); }
    },

    // ----- доска -----
    async saveName() {
      this.editName = false;
      if (!this.boardName.trim() || this.boardName === this.board.name) return;
      try { await http.patch(`/api/boards/${this.id}`, { name: this.boardName.trim() }); this.load(); } catch (e) { toastError(e); }
    },
    async archiveBoard() {
      try { await http.patch(`/api/boards/${this.id}`, { is_archived: !this.board.is_archived }); this.load(); } catch (e) { toastError(e); }
    },
    async deleteBoard() {
      if (!confirm(`Удалить доску «${this.board.name}» со всеми карточками?`)) return;
      try { await http.del(`/api/boards/${this.id}`); go(`/project/${this.pid}`); } catch (e) { toastError(e); }
    },
    async showArchived() { this.archived = await http.get(`/api/boards/${this.id}/archived`); },
    async restore(c) {
      try { await http.patch(`/api/cards/${c.id}`, { is_archived: false }); this.showArchived(); } catch (e) { toastError(e); }
    },
    async destroyArchived(c) {
      if (!confirm(`Удалить ${c.key} навсегда?`)) return;
      try { await http.del(`/api/cards/${c.id}`); this.showArchived(); } catch (e) { toastError(e); }
    },
    switchBoard(id) { go(`/board/${id}`); },
  },
  template: `
  <div v-if="error" class="empty">{{ error }}</div>
  <template v-else-if="board">
    <div class="board-bar">
      <span class="avatar" :style="{background: board.color, borderRadius: '7px', width: '26px', height: '26px'}">▦</span>
      <input v-if="editName" v-model="boardName" @blur="saveName" @keydown.enter="$event.target.blur()" style="width:220px;font-weight:700" autofocus>
      <span v-else class="name" @dblclick="canManage && (editName = true)" :title="canManage ? 'Двойной клик — переименовать' : ''">{{ board.name }}</span>
      <select v-if="board.boards.length > 1" :value="board.id" @change="switchBoard($event.target.value)" title="Другие доски проекта">
        <option v-for="b in board.boards" :key="b.id" :value="b.id">{{ b.name }}</option></select>
      <role-badge :role="board.project.my_role" />
      <span class="badge" v-if="board.is_archived">в архиве</span>
      <span class="spacer"></span>
      <div class="viewers" v-if="viewers.length" title="Сейчас смотрят доску">
        <span class="conn on"><span class="led"></span></span>
        <div class="avatars"><avatar v-for="u in viewers" :key="u.id" :user="board.members.find(m => m.id === u.id) || {full_name: u.name}" :size="26" /></div>
      </div>
      <button class="btn sm" @click="showArchived">🗄 Архив</button>
      <div class="dropdown" v-if="canManage">
        <button class="btn sm" @click.stop="colMenu = colMenu === 'board' ? null : 'board'">⋯</button>
        <div class="dropdown-menu" v-if="colMenu === 'board'" @click="colMenu = null">
          <button class="item" @click="editName = true">Переименовать</button>
          <button class="item" @click="archiveBoard">{{ board.is_archived ? 'Вернуть из архива' : 'Архивировать доску' }}</button>
          <button class="item" style="color:var(--danger)" @click="deleteBoard">Удалить доску</button>
        </div>
      </div>
    </div>
    <div class="board-bar" style="padding-top:8px;padding-bottom:8px;gap:8px">
      <input class="search" v-model="f.q" placeholder="🔍 Поиск по карточкам">
      <select v-model="f.label" :class="{'filter-on': f.label}"><option value="">Все метки</option><option v-for="l in board.labels" :key="l.id" :value="l.id">{{ l.name }}</option></select>
      <select v-model="f.assignee" :class="{'filter-on': f.assignee}"><option value="">Все исполнители</option><option v-for="m in board.members" :key="m.id" :value="m.id">{{ userName(m) }}</option></select>
      <select v-model="f.priority" :class="{'filter-on': f.priority}"><option value="">Любой приоритет</option><option v-for="(l, k) in PRIORITY_RU" :key="k" :value="k">{{ l }}</option></select>
      <select v-if="board.sprints.length" v-model="f.sprint" :class="{'filter-on': f.sprint}">
        <option value="">Все спринты</option>
        <option v-for="s in board.sprints" :key="s.id" :value="String(s.id)">{{ s.status === 'active' ? '🏃 ' : '' }}{{ s.name }}</option>
        <option value="none">Без спринта</option></select>
      <button class="btn sm" :class="{'filter-on': f.mine}" @click="f.mine = !f.mine">👤 Мои</button>
      <button class="btn sm" :class="{'filter-on': f.overdue}" @click="f.overdue = !f.overdue">⏰ Просроченные</button>
      <button class="btn sm ghost" v-if="filtersOn" @click="resetFilters">✕ Сбросить</button>
      <span class="muted small" v-if="filtersOn && canMove">перетаскивание отключено, пока активны фильтры</span>
      <span class="spacer"></span>
      <span class="muted small">{{ totalCards }} карточек</span>
    </div>

    <div class="board-scroll" :style="settings.boardBackground ? {background: 'color-mix(in srgb, ' + board.color + ' 9%, var(--bg))'} : {}">
      <div class="columns" :class="{compact: settings.compactCards}" v-sortable="columnSortable">
        <div class="column real" v-for="col in board.columns" :key="col.id"
             :class="{'over-limit': col.wip_limit && col.cards.length > col.wip_limit}">
          <div class="column-head">
            <input v-if="editingCol === col.id" v-model="colName" @blur="rename(col)" @keydown.enter="$event.target.blur()" autofocus>
            <span v-else class="title" @dblclick="startRename(col)">{{ col.is_done ? '✅ ' : '' }}{{ col.name }}</span>
            <span class="cnt" :class="{full: col.wip_limit && col.cards.length >= col.wip_limit}"
                  :title="col.wip_limit ? 'WIP-лимит' : ''">{{ col.cards.length }}<template v-if="col.wip_limit">/{{ col.wip_limit }}</template></span>
            <div class="dropdown" v-if="canManage">
              <button class="btn ghost sm icon" @click.stop="colMenu = colMenu === col.id ? null : col.id">⋯</button>
              <div class="dropdown-menu" v-if="colMenu === col.id">
                <button class="item" @click="startRename(col)">Переименовать</button>
                <button class="item" @click="setWip(col)">WIP-лимит…</button>
                <button class="item" @click="toggleDone(col)">{{ col.is_done ? 'Снять отметку «Готово»' : 'Отметить как «Готово»' }}</button>
                <button class="item" style="color:var(--danger)" @click="delColumn(col)">Удалить колонку</button>
              </div>
            </div>
          </div>
          <div class="cards" :data-col="col.id" v-sortable="cardSortable">
            <div class="kcard" v-for="c in cardsOf(col)" :key="c.id" :data-id="c.id" @click="openCard(c)"
                 :class="{flash: flash.has(c.id), 'done-col': col.is_done}">
              <div class="labels" v-if="c.labels.length"><span v-for="l in c.labels" :key="l.id" :style="{background: l.color}" :title="l.name"></span></div>
              <div class="card-watchers" v-if="cardViewers(c).length"
                   :title="cardViewers(c).map(v => userName(v.user) + (v.editing ? ' (редактирует)' : ' (смотрит)')).join(', ')">
                <span v-for="v in cardViewers(c)" :key="v.id" class="watcher" :class="{editing: v.editing}">
                  <avatar :user="v.user" :size="18" /><span v-if="v.editing">✏️</span></span>
              </div>
              <div class="ttl">{{ c.title }}</div>
              <div class="meta">
                <span :class="'prio prio-' + c.priority" :title="'Приоритет: ' + PRIORITY_RU[c.priority]"></span>
                <span class="key">{{ c.key }}</span>
                <span v-if="c.due_date" class="due" :class="dueState(c.due_date, col.is_done)">⏰ {{ fmtDate(c.due_date) }}</span>
                <span v-if="c.has_description" title="Есть описание">≡</span>
                <span v-if="c.checklist.total" :class="{'done-check': c.checklist.done === c.checklist.total}">☑ {{ c.checklist.done }}/{{ c.checklist.total }}</span>
                <span v-if="c.comments_count">💬 {{ c.comments_count }}</span>
                <span v-if="c.attachments_count">📎 {{ c.attachments_count }}</span>
                <span v-if="c.sprint_id && sprintName(c.sprint_id)" class="sprint-tag" :title="'Спринт: ' + sprintName(c.sprint_id)">🏃 {{ sprintName(c.sprint_id) }}</span>
                <span v-if="c.estimate != null" class="badge" style="padding:0 6px">{{ c.estimate }} SP</span>
                <span class="spacer"></span>
                <div class="avatars"><avatar v-for="a in c.assignees" :key="a.id" :user="a" :size="22" /></div>
              </div>
            </div>
          </div>
          <div class="column-foot" v-if="can('card:create', pid)">
            <form v-if="adding === col.id" @submit.prevent="addCard(col)">
              <textarea :ref="'add' + col.id" v-model="newTitle" rows="2" placeholder="Название карточки…" style="min-height:54px"
                        @keydown.enter.exact.prevent="addCard(col)" @keydown.esc="adding = null"></textarea>
              <div class="row" style="margin-top:6px"><button class="btn primary sm">Добавить</button><button type="button" class="btn ghost sm" @click="adding = null">Отмена</button></div>
            </form>
            <button v-else class="btn ghost sm" style="width:100%;justify-content:flex-start" @click="startAdd(col)">＋ Добавить карточку</button>
          </div>
        </div>
        <div class="column add-col" v-if="canManage">
          <form class="row" @submit.prevent="addColumn"><input v-model="newColumn" placeholder="Новая колонка"><button class="btn sm">＋</button></form>
        </div>
        <div v-if="!board.columns.length && !canManage" class="empty">На доске пока нет колонок</div>
      </div>
    </div>

    <card-modal v-if="cardId" :card-id="cardId" :board="board" :viewers="viewers" @close="closeCard" />

    <modal v-if="archived" title="Архив карточек" @close="archived = null">
      <div v-for="c in archived" :key="c.id" class="row" style="padding:8px 0;border-bottom:1px solid var(--border)">
        <span class="mono muted">{{ c.key }}</span><span class="grow">{{ c.title }} <span class="muted small">({{ c.column_name }})</span></span>
        <button class="btn sm" v-if="can('card:update', pid)" @click="restore(c)">Вернуть</button>
        <button class="btn sm danger" v-if="can('card:delete', pid)" @click="destroyArchived(c)">✕</button>
      </div>
      <div class="empty" v-if="!archived.length">Архив пуст</div>
    </modal>
  </template>`,
};
