/**
 * Показать реплику-основание в транскрипте.
 *
 * Пункт саммари и реплика — одно и то же место записи, но на экране они далеко
 * друг от друга. Поэтому «основание» не только перематывает плеер: страница
 * доезжает до реплики, и под ней на 1200 мс загорается подложка. Другого
 * способа связать вывод модели с тем, что было сказано, в интерфейсе нет.
 */
export function revealUtterance(part: number, idx: number) {
  const target = document.getElementById(`u-${part}-${idx}`);
  // Реплики может не быть: саммари пересобралось, а ссылка осталась старой.
  if (!target) return;

  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  target.scrollIntoView({ behavior: still ? "auto" : "smooth", block: "center" });

  // Перезапуск анимации: без снятия класса и принудительного пересчёта
  // раскладки повторное нажатие по тому же пункту ничего не покажет.
  target.classList.remove("flash-source");
  void target.offsetWidth;
  target.classList.add("flash-source");
}
