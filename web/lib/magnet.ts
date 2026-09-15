/**
 * Магнит ленты дня: куда подтянуть головку, когда свайп закончился.
 *
 * `x` — положение головки, `spans` — записи в тех же единицах, `radius` —
 * дальность притяжения. Внутри записи магнит молчит: головка уже на месте.
 * Рядом с записью — тянет к ближайшему её краю: подъехали слева — к началу,
 * проскочили вправо — к концу, а не назад через всю запись.
 */
export function magnetTarget(
  x: number,
  spans: { start: number; end: number }[],
  radius: number,
): number | null {
  let best: number | null = null;
  for (const { start, end } of spans) {
    if (x >= start && x <= end) return null;
    const edge = x < start ? start : end;
    const gap = Math.abs(edge - x);
    if (gap <= radius && (best === null || gap < Math.abs(best - x))) best = edge;
  }
  return best;
}
