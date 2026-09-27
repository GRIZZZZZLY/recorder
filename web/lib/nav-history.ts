/**
 * Откуда пришли внутри сайта. «Назад» в шапке записи вёл всегда в «Записи»,
 * даже если пришли из поиска — и запрос терялся. Оболочка отмечает каждый
 * раздел; шапка страницы, зная предыдущий, возвращает по истории.
 */
let current: string | null = null;
let previous: string | null = null;
const listeners = new Set<() => void>();

export function visited(path: string) {
  if (path === current) return;
  previous = current;
  current = path;
  listeners.forEach((listener) => listener());
}

export function previousPath() {
  return previous;
}

export function onVisit(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

const TITLES: Record<string, string> = {
  "/": "Сводка", "/records/": "Записи", "/tasks/": "Задачи", "/questions/": "Вопросы",
  "/review/": "Спорные места", "/search/": "Поиск", "/projects/": "Проекты",
  "/project/": "Проект", "/status/": "Состояние", "/session/": "Запись",
};

/** Имя раздела для подписи «назад»; неизвестный путь — просто «Назад». */
export function titleOf(path: string) {
  return TITLES[path] ?? "Назад";
}
