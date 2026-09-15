/**
 * Пересчёт огибающей под число столбиков, которое даёт ширина дорожки.
 *
 * Вниз — пик в каждом окне, та же логика, что у сервера (`envelope.resample`):
 * громкое место не пропадает между столбиками. Вверх — линейная интерполяция
 * между соседями: у записи в полминуты всего несколько десятков чисел, и без
 * растяжения они превращались в забор из широких прямоугольников. Интерполяция
 * ничего не выдумывает громче соседей: значение между двумя точками лежит
 * между ними.
 */
export function resample(values: number[], bars: number): number[] {
  if (!values.length || bars <= 0) return [];
  if (values.length === bars) return values;
  if (values.length > bars) {
    const step = values.length / bars;
    return Array.from({ length: bars }, (_, i) => {
      const from = Math.floor(i * step);
      const to = Math.max(Math.floor((i + 1) * step), from + 1);
      return Math.max(...values.slice(from, to));
    });
  }
  if (values.length === 1) return new Array(bars).fill(values[0]);
  const span = (values.length - 1) / (bars - 1);
  return Array.from({ length: bars }, (_, i) => {
    const x = i * span;
    const left = Math.min(Math.floor(x), values.length - 2);
    const t = x - left;
    return Math.round(values[left] * (1 - t) + values[left + 1] * t);
  });
}
