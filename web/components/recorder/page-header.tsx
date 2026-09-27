"use client";

import { useEffect, useSyncExternalStore } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { CaretLeft } from "@phosphor-icons/react/dist/ssr";
import { onVisit, previousPath, titleOf } from "@/lib/nav-history";

/**
 * Шапка страницы: крошки, заголовок 44/600 и правая подпись на той же
 * базовой линии. На телефоне заголовок 34 и крошка превращается в «назад» —
 * там она единственный путь обратно, боковой панели нет.
 */
/**
 * «Назад»: если пришли из другого раздела сайта — туда, по истории (запрос
 * поиска и прокрутка сохраняются), с его именем. Открыли страницу напрямую —
 * ссылка на раздел по умолчанию. На телефоне с домашнего экрана это
 * единственный путь назад: цель 44 px (`hit`).
 */
function BackLink({ href, title }: { href: string; title: string }) {
  const router = useRouter();
  const previous = useSyncExternalStore(onVisit, previousPath, () => null);
  return (
    <Link
      href={href}
      onClick={(event) => {
        if (!previous) return;
        event.preventDefault();
        router.back();
      }}
      className="press hit relative inline-flex items-center gap-1 hover:text-text-primary"
    >
      <CaretLeft size={14} aria-hidden className="lg:hidden" />
      {previous ? titleOf(previous) : title}
    </Link>
  );
}

/** Заголовок вкладки — по странице: «Диктофон» на всех был одинаков. */
function useDocumentTitle(title: string) {
  useEffect(() => {
    document.title = `${title} · Диктофон`;
  }, [title]);
}

export function PageHeader({ back, title, aside }: {
  back?: { href: string; title: string; note?: React.ReactNode };
  title: string;
  aside?: React.ReactNode;
}) {
  useDocumentTitle(title);
  return (
    <header className="recorder-page-header flex flex-col gap-2.5">
      {back ? (
        <div className="flex items-center gap-2 text-note text-text-secondary">
          <BackLink href={back.href} title={back.title} />
          {back.note ? (
            <>
              <span className="text-text-tertiary" aria-hidden>/</span>
              {back.note}
            </>
          ) : null}
        </div>
      ) : null}
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
        <h1 className="text-page-sm text-text-primary sm:text-page">{title}</h1>
        {aside ? <div className="text-[15px] text-text-secondary">{aside}</div> : null}
      </div>
    </header>
  );
}
