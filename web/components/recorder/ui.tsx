import type { RemixiconComponentType } from "@remixicon/react";
import { cx } from "@/utils/cx";

/**
 * Кнопка и поле ввода вместо одноимённых компонентов BoardUI.
 *
 * От BoardUI осталось только то, что вызывается: три вида кнопки и один
 * размер. Остальное (иконочные кнопки, группы, состояния загрузки, ссылки в
 * виде кнопок) не использовалось ни разу. Радиокнопки, бейджи, таблицу и
 * группу фильтров разметка рисует нативными элементами по месту — заводить
 * под каждый по компоненту не из чего.
 */

const VARIANTS = {
  primary: "bg-accent-500 text-text-white hover:bg-accent-400 active:bg-accent-600",
  secondary:
    "border border-border-button-default bg-background-secondary-default text-text-primary hover:border-border-button-hover hover:bg-background-secondary-hover",
  ghost: "text-text-secondary hover:bg-background-secondary-hover hover:text-text-primary",
} as const;

export function Button({
  variant = "secondary",
  leadingIcon: Icon,
  className,
  children,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: keyof typeof VARIANTS;
  leadingIcon?: RemixiconComponentType;
}) {
  return (
    <button
      {...props}
      className={cx(
        "recorder-press inline-flex items-center gap-1.5 rounded-2lg px-3 py-1.5 text-body-medium",
        "disabled:cursor-not-allowed disabled:opacity-50",
        VARIANTS[variant],
        className,
      )}
    >
      {Icon ? <Icon className="size-4 shrink-0" aria-hidden /> : null}
      {children}
    </button>
  );
}

/**
 * Поле ввода. onChange отдаёт строку, а не событие: во всех местах вызова
 * нужна только она, а так `onChange={setDraft}` пишется без обёртки.
 */
export function Input({
  className,
  onChange,
  ...props
}: Omit<React.InputHTMLAttributes<HTMLInputElement>, "onChange"> & {
  onChange?: (value: string) => void;
}) {
  return (
    <input
      {...props}
      onChange={(event) => onChange?.(event.target.value)}
      className={cx(
        "rounded-2lg border border-border-button-default bg-background-primary-default px-3 py-1.5",
        "text-body-regular text-text-primary transition-colors placeholder:text-text-placeholder",
        "hover:border-border-button-hover focus:border-border-focus-ring",
        className,
      )}
    />
  );
}
