/** Общие форматтеры. Дата, время и длительность выглядят одинаково везде. */

const MONTHS = [
  "января", "февраля", "марта", "апреля", "мая", "июня",
  "июля", "августа", "сентября", "октября", "ноября", "декабря",
];

const MONTHS_SHORT = [
  "янв", "фев", "мар", "апр", "мая", "июн",
  "июл", "авг", "сен", "окт", "ноя", "дек",
];

const WEEKDAYS = [
  "воскресенье", "понедельник", "вторник", "среда",
  "четверг", "пятница", "суббота",
];

function parts(iso: string | null | undefined) {
  if (!iso) return null;
  const [year, month, day] = iso.slice(0, 10).split("-").map(Number);
  if (!year || !month || !day) return null;
  return { year, month, day };
}

/** "2026-09-12" → "12 сентября". */
export function formatDay(iso: string | null | undefined): string {
  const value = parts(iso);
  if (!value) return "дата неизвестна";
  return `${value.day} ${MONTHS[value.month - 1]}`;
}

/** "2026-09-12" → "12 сен". Для узких колонок. */
export function formatDayShort(iso: string | null | undefined): string {
  const value = parts(iso);
  if (!value) return "—";
  return `${value.day} ${MONTHS_SHORT[value.month - 1]}`;
}

/** "2026-09-12" → "пятница, 12 сентября". Шапка сводки. */
export function formatDayLong(iso: string | null | undefined): string {
  const value = parts(iso);
  if (!value) return "дата неизвестна";
  const weekday = WEEKDAYS[new Date(value.year, value.month - 1, value.day).getDay()];
  return `${weekday}, ${value.day} ${MONTHS[value.month - 1]}`;
}

/** Заголовок группы в списке записей: "Сегодня · 12 сентября". */
export function formatDayGroup(iso: string | null | undefined, today = todayIso()): string {
  if (!iso) return "дата неизвестна";
  const date = iso.slice(0, 10);
  if (date === today) return `Сегодня · ${formatDay(date)}`;
  if (date === shiftIso(today, -1)) return `Вчера · ${formatDay(date)}`;
  return formatDay(date);
}

export function todayIso(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

function shiftIso(iso: string, days: number): string {
  const [year, month, day] = iso.split("-").map(Number);
  const date = new Date(year, month - 1, day + days);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

/** Метка базы — UTC без зоны ("2026-09-12 16:40:07"); в местное время. */
function parseStamp(stamp: string | null | undefined): Date | null {
  if (!stamp || stamp.length < 16) return null;
  const at = new Date(`${stamp.replace(" ", "T")}Z`);
  return Number.isFinite(at.getTime()) ? at : null;
}

/** "2026-09-12 16:40:07" (UTC) → "19:40" по местным часам. Время выгрузки первой части. */
export function formatTime(stamp: string | null | undefined): string {
  const at = parseStamp(stamp);
  if (!at) return "—";
  return `${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}`;
}

/** Часы от начала местных суток дробью: 16:40 → 16.67. Нужно ленте дня, где
 * линия «сейчас» тоже местная — иначе блоки и линия живут в разных поясах. */
export function hoursOfDay(stamp: string | null | undefined): number | null {
  const at = parseStamp(stamp);
  return at ? at.getHours() + at.getMinutes() / 60 : null;
}

/** Секунды внутри записи → "0:19", "12:34", "1:02:03" — как в любом плеере.
 * «00:00:19» у разговора на минуту читалось как отчёт машины, а не время. */
export function formatClock(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const ss = String(total % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/** Время из саммари («0:00:27», его пишет сервер) — в тот же вид. Неразборное
 * возвращается как есть. */
export function normalizeClock(text: string): string {
  const match = /^(\d+):(\d\d):(\d\d)$/.exec(text);
  return match ? formatClock(Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3])) : text;
}

/** «2 200 000» не должно рваться переносом строки посреди числа: пробелы
 * между разрядами — неразрывные. Модель пишет числа с обычными пробелами. */
export function keepNumbers(text: string): string {
  return text.replace(/(\d) (?=\d{3}(?!\d))/g, "$1\u00a0");
}

/** Метка базы → «15 сен, 12:31» по местному времени. */
export function formatStamp(stamp: string | null | undefined): string {
  const at = parseStamp(stamp);
  if (!at) return "";
  const iso = `${at.getFullYear()}-${String(at.getMonth() + 1).padStart(2, "0")}-${String(at.getDate()).padStart(2, "0")}`;
  return `${formatDayShort(iso)}, ${formatTime(stamp)}`;
}

/** Сырая ошибка расшифровщика или модели → что случилось, по-человечески.
 * Неизвестная — общая фраза; сырой текст всё равно показывается под ней. */
export function humanError(raw: string | null | undefined): string {
  const text = raw ?? "";
  if (/corrupt|unsupported|invalid audio|decode/i.test(text)) return "звук повреждён или в незнакомом формате";
  if (/timeout|timed out/i.test(text)) return "расшифровщик не ответил вовремя";
  if (/\b(401|403)\b|api key|unauthori/i.test(text)) return "ключ расшифровщика не принят";
  if (/\b429\b|rate limit/i.test(text)) return "расшифровщик перегружен — повторите позже";
  if (/\b5\d\d\b/.test(text)) return "сбой на стороне расшифровщика";
  if (/claude|model/i.test(text)) return "модель не ответила";
  return text ? "причина — ниже" : "причина не записана";
}

/** Имя записи — одно на всех экранах: тема саммари, без неё — день и час.
 * Раньше у одной записи было три имени: «тема появится после саммари»,
 * «разговор без темы» и «15 сентября, 15:30». */
export function recordTitle(row: { topic: string | null; day: string | null; first_at: string | null }): string {
  if (row.topic) return row.topic;
  return row.day ? `${formatDay(row.day)}, ${formatTime(row.first_at)}` : "Разговор";
}

/** Длительность словами: "45 с", "30 мин", "1 ч 20 мин". */
export function formatDuration(seconds: number): string {
  const total = Math.round(seconds);
  if (total < 60) return `${total} с`;
  const minutes = Math.round(total / 60);
  if (minutes < 60) return `${minutes} мин`;
  const rest = minutes % 60;
  return rest ? `${Math.floor(minutes / 60)} ч ${rest} мин` : `${Math.floor(minutes / 60)} ч`;
}

/** Длительность из минут: то же, но на вход минуты (их отдаёт дайджест). */
export function formatMinutes(minutes: number): string {
  return formatDuration(minutes * 60);
}

/** "3 мин назад", "вчера · 09:14" — как давно приходила часть. */
export function formatAgo(stamp: string | null | undefined, now = new Date()): string {
  const at = parseStamp(stamp);
  if (!at) return "выгрузок не было";
  const minutes = Math.floor((now.getTime() - at.getTime()) / 60000);
  if (minutes < 1) return "только что";
  if (minutes < 60) return `${minutes} ${plural(minutes, "минуту", "минуты", "минут")} назад`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} ${plural(hours, "час", "часа", "часов")} назад`;
  const days = Math.floor(hours / 24);
  return `${days} ${plural(days, "день", "дня", "дней")} назад`;
}

/** Русское склонение по числу: 1 разговор, 2 разговора, 5 разговоров. */
export function plural(count: number, one: string, few: string, many: string): string {
  const mod100 = Math.abs(count) % 100;
  const mod10 = mod100 % 10;
  if (mod100 >= 11 && mod100 <= 14) return many;
  if (mod10 === 1) return one;
  if (mod10 >= 2 && mod10 <= 4) return few;
  return many;
}

/** "6 разговоров" — число со склонённым словом. */
export function count(value: number, one: string, few: string, many: string): string {
  return `${value} ${plural(value, one, few, many)}`;
}
