import Link from "next/link";
import { CaretLeft } from "@phosphor-icons/react/dist/ssr";
import { at } from "@/lib/motion";

/**
 * Шапка страницы: крошки, заголовок 44/600 и правая подпись на той же
 * базовой линии. На телефоне заголовок 34 и крошка превращается в «назад» —
 * там она единственный путь обратно, боковой панели нет.
 */
export function PageHeader({ back, title, aside }: {
  back?: { href: string; title: string; note?: React.ReactNode };
  title: string;
  aside?: React.ReactNode;
}) {
  // Нулевой такт хореографии: шапка первой, дальше секции по своим тактам.
  return (
    <header className="enter flex flex-col gap-2.5" style={at(0)}>
      {back ? (
        <div className="flex items-center gap-2 text-note text-text-secondary">
          <Link href={back.href} className="press inline-flex items-center gap-1 hover:text-text-primary">
            <CaretLeft size={14} aria-hidden className="lg:hidden" />
            {back.title}
          </Link>
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
