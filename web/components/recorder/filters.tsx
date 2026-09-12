"use client";

import { ButtonGroup, ButtonGroupItem } from "@/components/base/buttons/button-group";

/**
 * Ряд фильтров. Установленный ButtonGroup — не react-aria: нет
 * selectionMode/selectedKeys, у ButtonGroupItem одиночный `selected`
 * (см. web/components/base/buttons/button-group.tsx). Одиночный выбор
 * собираем вручную: текущий ключ подсвечиваем через `selected`, клик отдаёт
 * ключ наружу.
 */
export function Filters({ value, options, onChange }: {
  value: string;
  options: { key: string; title: string }[];
  onChange: (key: string) => void;
}) {
  return (
    <ButtonGroup aria-label="Фильтры">
      {options.map((option) => (
        <ButtonGroupItem
          key={option.key}
          selected={option.key === value}
          onClick={() => onChange(option.key)}
        >
          {option.title}
        </ButtonGroupItem>
      ))}
    </ButtonGroup>
  );
}
