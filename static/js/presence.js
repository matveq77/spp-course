// Присутствие: какую доску/карточку смотрю и что редактирую. Сервер рассылает это остальным по SSE.
import { http } from './api.js';
import { store } from './store.js';

const state = { board_id: null, card_id: null, editing: null };
let seq = 0;

function send() {
  if (!store.connectionId) return;
  seq += 1;
  http.post('/api/presence', { connection_id: store.connectionId, ...state, seq }).catch(() => {});
}

export function updatePresence(patch) {
  const changed = Object.keys(patch).some((k) => state[k] !== patch[k]);
  Object.assign(state, patch);
  if (!state.board_id) { state.card_id = null; state.editing = null; }
  if (!state.card_id) state.editing = null;
  if (changed) send();
}

// после переподключения SSE (новый connection_id) повторно сообщаем своё состояние
export const resendPresence = () => { if (state.board_id) send(); };
