import { ArrowClockwise } from "@phosphor-icons/react/dist/ssr";
import { Card } from "@/components/recorder/list";
import { Button, Dot } from "@/components/recorder/ui";
import { ApiError, AuthError } from "@/lib/api";
import { cx } from "@/utils/cx";

/**
 * Загрузка, пустота и ошибка. Скелетон повторяет форму карточек, а не крутит
 * спиннер: страница не должна прыгать, когда данные приедут.
 */

/** Полоса-заглушка с бегущим бликом. */
function Bar({ className }: { className?: string }) {
  return <span className={cx("shimmer block rounded-md bg-surface-elevated", className)} />;
}

export function Loading({ rows = 4 }: { rows?: number }) {
  return (
    <div className="flex flex-col gap-7" aria-busy="true">
      <Bar className="h-8 w-40" />
      <Card>
        {Array.from({ length: rows }, (_, index) => (
          <div key={index}
               className="flex items-center gap-3 px-4 py-4 not-first:border-t not-first:border-hairline">
            <span className="size-2 shrink-0 rounded-chip bg-surface-elevated" />
            <Bar className={cx("h-3 flex-1", index % 2 ? "max-w-[45%]" : "max-w-[65%]")} />
            <Bar className="h-3 w-20" />
          </div>
        ))}
      </Card>
    </div>
  );
}

/**
 * Пустое состояние. Плоская волна вместо иллюстрации: тишина — это и есть
 * ровная линия, другого рисунка тут не нужно.
 */
export function Empty({ title, text, action }: {
  title: string;
  text?: string;
  action?: React.ReactNode;
}) {
  // Тишина рисуется ровной линией: это единственное место, где форма
  // выдумана, и она ничего не изображает — поэтому и неподвижна.
  const flat = new Array(40).fill(3);
  return (
    <div className="flex flex-col items-center gap-2 rounded-card bg-surface shadow-plate px-6 py-9 text-center">
      <div aria-hidden className="flex h-6 items-center gap-0.5 opacity-50">
        {flat.map((value, index) => (
          <span key={index} className="w-0.5 rounded-[1px] bg-text-tertiary"
                style={{ height: value }} />
        ))}
      </div>
      <p className="mt-2 text-section text-text-primary">{title}</p>
      {text ? <p className="max-w-[420px] text-row text-text-secondary">{text}</p> : null}
      {action ? <div className="mt-2.5 flex gap-2">{action}</div> : null}
    </div>
  );
}

/** Короткая пустота внутри секции: без карточки и волны. */
export function EmptyNote({ children }: { children: React.ReactNode }) {
  return <p className="px-1 text-row text-text-secondary">{children}</p>;
}

/** Текущий адрес для возврата после входа. На сервере его нет. */
function here() {
  return typeof window === "undefined" ? "/" : window.location.pathname + window.location.search;
}

export function Failed({ error, onRetry }: { error: Error; onRetry: () => void }) {
  const auth = error instanceof AuthError;
  // «Нет такой записи» — не сбой: повторять бесполезно, нужен путь к записям.
  if (error instanceof ApiError && error.status === 404) {
    return (
      <div className="rise-in flex items-center gap-3.5 rounded-card bg-surface shadow-plate p-5 text-row">
        <Dot tone="missing" />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span>Такого здесь нет</span>
          <span className="text-note text-text-secondary">Ссылка устарела или запись удалена. Остальные записи на месте.</span>
        </div>
        <a href="/records/" className="press hit relative shrink-0 rounded-inner bg-hairline px-4 py-2 text-note hover:bg-hairline-strong">
          К записям
        </a>
      </div>
    );
  }
  return (
    <div className="rise-in flex items-center gap-3.5 rounded-card bg-surface shadow-plate p-5 text-row">
      <Dot tone="accent" />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span>{auth ? "Метка входа истекла" : "Не удалось загрузить данные"}</span>
        <span className="truncate font-mono text-micro text-text-secondary">{error.message}</span>
      </div>
      {auth ? (
        // Метка живёт месяц, но когда-то кончается — и в режиме «на домашний
        // экран» браузер пароль сам не спросит. Ведём на форму и возвращаем
        // обратно на ту же страницу.
        <a href={`/login?next=${encodeURIComponent(here())}`}
           className="press shrink-0 rounded-inner bg-hairline px-4 py-2 text-note hover:bg-hairline-strong">
          Войти
        </a>
      ) : (
        <Button icon={ArrowClockwise} onClick={onRetry} className="shrink-0">Повторить</Button>
      )}
    </div>
  );
}
