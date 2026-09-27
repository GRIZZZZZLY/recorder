/**
 * Показать реплику-основание в транскрипте.
 *
 * Пункт саммари и реплика — одно и то же место записи, но на экране они далеко
 * друг от друга. Поэтому «основание» не только перематывает плеер: страница
 * доезжает до реплики, и под ней на 1200 мс загорается подложка. Другого
 * способа связать вывод модели с тем, что было сказано, в интерфейсе нет.
 *
 * `from` — откуда пришли (кнопка «основание»): плеер покажет чип «↑ к пункту»,
 * иначе после прыжка на тысячу пикселей вниз дорогу обратно ищут прокруткой.
 */
type Listener = (from: HTMLElement | null) => void;
const listeners = new Set<Listener>();

export function onReveal(listener: Listener) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function revealUtterance(part: number, idx: number, from: HTMLElement | null = null) {
  const target = document.getElementById(`u-${part}-${idx}`);
  // Реплики может не быть: саммари пересобралось, а ссылка осталась старой.
  if (!target) return;

  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  target.scrollIntoView({ behavior: still ? "auto" : "smooth", block: "center" });
  // Фокус — туда же: иначе он оставался на кнопке далеко за экраном, и
  // клавиатура с читалкой начинали с чужого места.
  target.focus({ preventScroll: true });

  // Вспышка — атрибутом, а не классом: className строки ведёт React и при
  // первой же перерисовке (строка стала «звучащей») стирал добавленный руками
  // класс — вспышку никто не видел. Перезапуск: снять, пересчитать раскладку,
  // поставить, иначе повторное нажатие по тому же пункту ничего не покажет.
  delete target.dataset.flash;
  void target.offsetWidth;
  target.dataset.flash = "";
  target.addEventListener("animationend", () => { delete target.dataset.flash; }, { once: true });

  listeners.forEach((listener) => listener(from));
}
