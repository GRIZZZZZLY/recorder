"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { MagnifyingGlass, X } from "@phosphor-icons/react/dist/ssr";
import { Card, Row, RowLink, Section } from "@/components/recorder/list";
import { PageHeader } from "@/components/recorder/page-header";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { Button, EvidenceLink } from "@/components/recorder/ui";
import { useApi } from "@/lib/use-api";
import type { SearchHit } from "@/lib/api";
import { count, formatClock, formatDayShort, keepNumbers } from "@/lib/format";
import { sessionHref } from "@/lib/session-link";

/** Пункт саммари, где нашлось слово: решение, факт, задача или вопрос. */
type NoteHit = {
  device_id: string;
  session: number;
  kind: string;
  text: string;
  time: string;
  source: { part: number; idx: number } | null;
  day: string | null;
  topic: string | null;
  segments: { text: string; hit: boolean }[];
};

function Body() {
  const router = useRouter();
  // Единственный источник правды — адрес: запрос попадает в ?q=, «назад»
  // восстанавливает прошлый.
  const query = useSearchParams().get("q") ?? "";
  const trimmed = query.trim();
  const [draft, setDraft] = useState(query);
  const field = useRef<HTMLInputElement>(null);
  // Адрес сменился (нажали «назад», перешли по ссылке) — поле идёт за ним.
  // Правка прямо во время отрисовки, а не в эффекте: эффект показал бы кадр со
  // старым текстом в поле и только потом заменил его.
  const [shown, setShown] = useState(query);
  if (shown !== query) {
    setShown(query);
    setDraft(query);
  }

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
  // То же слово в решениях, фактах, задачах и вопросах саммари: «млн» живёт
  // там, а не в репликах.
  const { data: notes } =
    useApi<NoteHit[]>(trimmed ? `/search/notes?q=${encodeURIComponent(trimmed)}` : null);

  function go(next: string) {
    router.push(next.trim() ? `/search/?q=${encodeURIComponent(next.trim())}` : "/search/");
  }

  const sessions = data ? new Set(data.map((hit) => `${hit.device_id}-${hit.session}`)).size : 0;

  return (
    <>
      <PageHeader title="Поиск" />

      {/* Рамка проявляется на фокусе за 150 мс; каретка красная по умолчанию. */}
      <form onSubmit={(event) => { event.preventDefault(); go(draft); }}
            onClick={() => field.current?.focus()}
            className="flex cursor-text items-center gap-3 rounded-card border border-transparent bg-surface px-4 py-1 transition-colors duration-150 focus-within:border-control">
        <MagnifyingGlass size={20} aria-hidden className="shrink-0 text-text-secondary" />
        <input
          ref={field}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Слово, имя, проект"
          aria-label="Что искать"
          className="recorder-bare min-w-0 flex-1 bg-transparent py-2.5 text-base text-text-primary outline-none placeholder:text-text-tertiary"
        />
        {draft ? (
          <button type="button" aria-label="Очистить" onClick={() => { setDraft(""); go(""); }}
                  className="press hit relative shrink-0 rounded-sm text-text-tertiary hover:text-text-primary">
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
          Ищет по формам слова: «сметы» найдёт «смету» — в разговорах и в саммари.
        </p>
      ) : error ? <Failed error={error} onRetry={reload} />
        : loading || !data ? <Loading rows={3} />
        : data.length === 0 && !notes?.length ? (
          <Empty
            title="Ничего не найдено"
            text={`По запросу «${trimmed}» ничего нет ни в разговорах, ни в саммари. Попробуйте другое слово.`}
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
          <>
            {notes?.length ? (
              <Section note={`В саммари · ${notes.length}`}>
                <Card>
                  {notes.map((note, index) => (
                    <Row key={`${note.device_id}-${note.session}-${index}`} className="items-start gap-3">
                      {note.source ? (
                        <EvidenceLink time={note.time} context={note.text}
                                      href={sessionHref(note.device_id, note.session, note.source.part, note.source.idx)} />
                      ) : (
                        // Абзац саммари: основания-времени у него нет — ведём на запись.
                        <Link href={sessionHref(note.device_id, note.session)}
                              className="press hit relative shrink-0 rounded-inner bg-hairline px-2.5 py-1.5 text-note text-text-primary shadow-plate hover:bg-hairline-strong">
                          открыть
                        </Link>
                      )}
                      <span className="min-w-0 flex-1 pt-1">
                        {note.segments.map((segment, part) =>
                          segment.hit
                            ? <mark key={part} className="mark-sweep">{keepNumbers(segment.text)}</mark>
                            : <span key={part}>{keepNumbers(segment.text)}</span>)}
                        <span className="block text-note text-text-tertiary">
                          {[note.kind, note.topic, formatDayShort(note.day)].filter(Boolean).join(" · ")}
                        </span>
                      </span>
                    </Row>
                  ))}
                </Card>
              </Section>
            ) : null}
            {data.length === 0 ? (
              <p className="px-1 text-note text-text-secondary">В репликах совпадений нет — только в саммари.</p>
            ) : null}
            {data.length > 0 && (
            <Section
              note={`${count(data.length, "совпадение", "совпадения", "совпадений")} в ${count(sessions, "разговоре", "разговорах", "разговорах")}`}
              aside="сначала новые"
            >
              <Card>
                {data.map((hit) => (
                  <RowLink
                    key={`${hit.device_id}-${hit.session}-${hit.part}-${hit.idx}`}
                    href={sessionHref(hit.device_id, hit.session, hit.part, hit.idx)}
                    className="flex-wrap items-baseline gap-4 gap-y-1"
                  >
                    {/* На телефоне подпись — строкой над найденным текстом, с
                        говорящим: колонка в 170 px оставляла тексту половину. */}
                    <span className="shrink-0 font-mono text-note text-text-tertiary max-sm:w-full sm:w-[170px]">
                      <span className="font-sans">{[hit.topic, formatDayShort(hit.day)].filter(Boolean).join(" · ")}</span> · {formatClock(hit.start_abs)}
                      {hit.label ? <span className="font-sans sm:hidden"> · {hit.label}</span> : null}
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
