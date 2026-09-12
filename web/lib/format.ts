/** Общие форматтеры. Дата и время показываются одинаково на всех страницах. */

const MONTHS = [
  "января", "февраля", "марта", "апреля", "мая", "июня",
  "июля", "августа", "сентября", "октября", "ноября", "декабря",
];

/** "2026-09-12" → "12 сентября 2026". Пусто → "дата неизвестна". */
export function formatDay(iso: string | null | undefined): string {
  if (!iso) return "дата неизвестна";
  const [year, month, day] = iso.slice(0, 10).split("-").map(Number);
  if (!year || !month || !day) return "дата неизвестна";
  return `${day} ${MONTHS[month - 1]} ${year}`;
}

/** Секунды от начала сессии → "Ч:ММ:СС". Часы без ведущего нуля. */
export function formatClock(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const mm = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
  const ss = String(total % 60).padStart(2, "0");
  return `${Math.floor(total / 3600)}:${mm}:${ss}`;
}

/** Минуты с одним знаком: "4.0". */
export function formatMinutes(value: number): string {
  return value.toFixed(1);
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
