"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { MagnifyingGlass, X } from "@phosphor-icons/react/dist/ssr";
import { Card, RowLink, Section } from "@/components/recorder/list";
import { PageHeader } from "@/components/recorder/page-header";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { Button } from "@/components/recorder/ui";
import { useApi } from "@/lib/use-api";
import type { SearchHit } from "@/lib/api";
import { count, formatClock } from "@/lib/format";
import { at } from "@/lib/motion";
import { sessionHref } from "@/lib/session-link";

function Body() {
  const router = useRouter();
  // Единственный источник правды — адрес: запрос попадает в ?q=, «назад»
  // восстанавливает прошлый.
  const query = useSearchParams().get("q") ?? "";
  const trimmed = query.trim();
  const [draft, setDraft] = useState(query);
  const field = useRef<HTMLInputElement>(null);
  useEffect(() => setDraft(query), [query]);

  // ⌘K / Ctrl+K — в поле. Подсказка нарисована прямо в поле, значит должна
  // работать.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        field.current?.focus();
        field.current?.select();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const { data, error, loading, reload } =
    useApi<SearchHit[]>(trimmed ? `/search?q=${encodeURIComponent(trimmed)}` : null);

  function go(next: string) {
    router.push(next.trim() ? `/search/?q=${encodeURIComponent(next.trim())}` : "/search/");
  }

  const sessions = data ? new Set(data.map((hit) => `${hit.device_id}-${hit.session}`)).size : 0;

  return (
    <>
      <PageHeader title="Поиск" />

      {/* Рамка проявляется на фокусе за 150 мс; каретка красная по умолчанию. */}
      <form onSubmit={(event) => { event.preventDefault(); go(draft); }}
            className="enter flex items-center gap-3 rounded-card border border-transparent bg-surface px-4 py-3 transition-colors duration-150 focus-within:border-control"
            style={at(1)}>
        <MagnifyingGlass size={20} aria-hidden className="shrink-0 text-text-secondary" />
        <input
          ref={field}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Слово, имя, проект"
          aria-label="Что искать"
          className="min-w-0 flex-1 bg-transparent text-base text-text-primary outline-none placeholder:text-text-tertiary"
        />
        {draft ? (
          <button type="button" aria-label="Очистить" onClick={() => { setDraft(""); go(""); }}
                  className="press shrink-0 rounded-sm text-text-tertiary hover:text-text-primary">
            <X size={16} aria-hidden />
          </button>
        ) : (
          <kbd className="hidden shrink-0 rounded-sm border border-hairline-strong px-1.5 py-0.5 font-mono text-micro text-text-tertiary sm:block">
            ⌘K
          </kbd>
        )}
      </form>

      {!trimmed ? (
        <p className="px-1 text-note text-text-secondary">
          Поиск идёт по леммам: «сметы» найдёт «смету».
        </p>
      ) : error ? <Failed error={error} onRetry={reload} />
        : loading || !data ? <Loading rows={3} />
        : data.length === 0 ? (
          <Empty
            title="Ничего не найдено"
            text={`По запросу «${trimmed}» совпадений нет. Попробуйте одно слово или проверьте, расшифрованы ли последние части.`}
            action={
              <>
                {trimmed.includes(" ") && (
                  <Button onClick={() => go(trimmed.split(/\s+/)[0])}>
                    Искать «{trimmed.split(/\s+/)[0]}»
                  </Button>
                )}
                <Button variant="ghost" onClick={() => router.push("/status/")}>
                  Открыть состояние
                </Button>
              </>
            }
          />
        ) : (
          <Section
            note={`${count(data.length, "совпадение", "совпадения", "совпадений")} в ${count(sessions, "сессии", "сессиях", "сессиях")}`}
            aside="сначала новые"
            at={2}
          >
            <Card className="cascade">
              {data.map((hit) => (
                <RowLink
                  key={`${hit.device_id}-${hit.session}-${hit.part}-${hit.idx}`}
                  href={sessionHref(hit.device_id, hit.session, hit.part, hit.idx)}
                  className="items-baseline gap-4"
                >
                  <span className="w-[170px] shrink-0 font-mono text-note text-text-tertiary">
                    сессия {String(hit.session).padStart(4, "0")} · {formatClock(hit.start_abs)}
                  </span>
                  <span className="hidden w-20 shrink-0 truncate text-text-secondary sm:block">
                    {hit.label}
                  </span>
                  <span className="min-w-0 flex-1 leading-snug">
                    {hit.segments.map((segment, index) =>
                      segment.hit
                        ? <mark key={index} className="mark-sweep">{segment.text}</mark>
                        : <span key={index}>{segment.text}</span>)}
                  </span>
                </RowLink>
              ))}
            </Card>
          </Section>
        )}
    </>
  );
}

export default function SearchPage() {
  return (
    <Suspense fallback={<Loading />}>
      <Body />
    </Suspense>
  );
}
