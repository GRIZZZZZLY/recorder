import { RiRefreshLine } from "@remixicon/react";
import { Button } from "@/components/base/buttons/button";
import { AuthError } from "@/lib/api";

/** Скелетон: три полосы вместо прыжка пустой страницы. */
export function Loading() {
  return (
    <div className="flex flex-col gap-3" aria-busy="true">
      {[0, 1, 2].map((row) => (
        <div key={row} className="h-16 animate-pulse rounded-2xl bg-background-primary-default" />
      ))}
    </div>
  );
}

export function Empty({ text }: { text: string }) {
  return <p className="text-body-regular text-text-tertiary">{text}</p>;
}

export function Failed({ error, onRetry }: { error: Error; onRetry: () => void }) {
  const auth = error instanceof AuthError;
  return (
    <div className="rounded-2xl border border-separator-border p-6">
      <p className="text-body-medium text-text-primary">
        {auth ? "Нужен вход заново: обновите страницу." : "Не удалось загрузить данные."}
      </p>
      <p className="mt-1 text-body-2-regular text-text-tertiary">{error.message}</p>
      {!auth && (
        <Button className="mt-4" variant="secondary" size="small"
                leadingIcon={RiRefreshLine} onClick={onRetry}>
          Повторить
        </Button>
      )}
    </div>
  );
}
