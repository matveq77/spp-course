// Пользовательские настройки интерфейса (хранятся в localStorage этого браузера)
const { reactive, watch } = Vue;

const LS_KEY = 'kb_settings';

export const DEFAULTS = {
  theme: 'system',        // light | dark | system
  accent: '#4f46e5',      // основной цвет интерфейса
  compactCards: false,    // компактные карточки на доске
  boardBackground: true,  // цветная подложка доски в цвет доски
  liveHighlight: true,    // подсвечивать карточки, изменённые другими
  notifyToasts: true,     // всплывающие уведомления
  notifySound: false,     // звук при новом уведомлении
  sidebarCollapsed: false,
};

export const ACCENTS = ['#4f46e5', '#0ea5e9', '#10b981', '#f59e0b', '#ef4444', '#ec4899', '#8b5cf6', '#0f766e'];

function load() {
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(LS_KEY) || '{}') }; } catch { return { ...DEFAULTS }; }
}

export const settings = reactive(load());

const media = window.matchMedia('(prefers-color-scheme: dark)');
export const effectiveTheme = () => (settings.theme === 'system' ? (media.matches ? 'dark' : 'light') : settings.theme);

export function applySettings() {
  const root = document.documentElement;
  root.dataset.theme = effectiveTheme();
  root.style.setProperty('--accent', settings.accent);
  root.classList.toggle('sidebar-collapsed', settings.sidebarCollapsed);
}

export function resetSettings() { Object.assign(settings, DEFAULTS); }

export function toggleTheme() { settings.theme = effectiveTheme() === 'dark' ? 'light' : 'dark'; }

watch(settings, () => {
  try { localStorage.setItem(LS_KEY, JSON.stringify(settings)); } catch { /* приватный режим */ }
  applySettings();
}, { deep: true });
media.addEventListener('change', applySettings);
applySettings();

// короткий «дзынь» без файлов — через Web Audio
export function playBeep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator(); const gain = ctx.createGain();
    osc.frequency.value = 880; gain.gain.setValueAtTime(0.08, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.25);
    osc.connect(gain).connect(ctx.destination); osc.start(); osc.stop(ctx.currentTime + 0.25);
  } catch { /* без звука */ }
}
