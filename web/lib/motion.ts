import type { CSSProperties } from "react";

/**
 * Такты хореографии страницы. `enter`, `cascade`, линейка ленты и вспышка
 * точки читают `--i` из инлайн-стиля (шаг `--stagger`), линейка — ещё и `--n`,
 * номер часа. Переменные наследуются вглубь, поэтому такт ставится один раз
 * на секцию.
 */
export function at(i: number, n?: number): CSSProperties {
  return { "--i": i, ...(n !== undefined && { "--n": n }) } as CSSProperties;
}
