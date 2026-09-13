import { extendTailwindMerge } from "tailwind-merge";

/**
 * Текстовые стили из styles/globals.css.
 *
 * Важно: каждая утилита text-*, объявленная через @theme (`text-body-medium`,
 * `text-title-3-semibold`), должна быть в этом списке. Иначе tailwind-merge —
 * который про нашу тему ничего не знает — принимает её за утилиту цвета и
 * молча выбрасывает, когда рядом в className стоит настоящий цвет
 * (`text-text-primary` и подобные).
 *
 * Добавили или переименовали стиль в globals.css — поправьте здесь.
 */
const TEXT_STYLES = [
  "caption-1-medium",
  "caption-1-semibold",
  "body-2-regular",
  "body-2-medium",
  "body-regular",
  "body-medium",
  "headline-medium",
  "title-3-semibold",
  "title-1-medium",
  "display-recorder",
  "display-recorder-lg",
];

const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [{ text: TEXT_STYLES }],
    },
  },
});

/**
 * Слияние классов Tailwind: при конфликте побеждает последний.
 */
export const cx = twMerge;
