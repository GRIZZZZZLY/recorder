"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/base/buttons/button";
import { Input } from "@/components/base/input/input";
import { PageTitle } from "@/components/recorder/eyebrow";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { useApi } from "@/lib/use-api";
import type { SearchHit } from "@/lib/api";
import { formatClock } from "@/lib/format";
import { sessionHref } from "@/lib/session-link";

function Body() {
  const router = useRouter();
  // Единственный источник правды — адрес: запрос попадает в ?q=, «назад»
  // восстанавливает прошлый (та же причина, что и на /tasks и /questions).
  const query = useSearchParams().get("q") ?? "";
  const trimmed = query.trim();
  const [draft, setDraft] = useState(query);
  useEffect(() => setDraft(query), [query]);
  const { data, error, loading, reload } =
    useApi<SearchHit[]>(trimmed ? `/search?q=${encodeURIComponent(trimmed)}` : null);

  return (
    <>
      <PageTitle eyebrow="ДИКТОФОН" plain="Найти" italic="сказанное" />
      <form className="flex flex-wrap items-center gap-3"
            onSubmit={(event) => { event.preventDefault(); router.push(`/search/?q=${encodeURIComponent(draft.trim())}`); }}>
        <Input className="min-w-48 flex-1" placeholder="Слово или фраза"
               value={draft} onChange={setDraft} />
        <Button type="submit" variant="primary" size="small">Найти</Button>
      </form>

      <div className="mt-6">
        {!trimmed ? <Empty text="Введите слово: поиск идёт по леммам, «сметы» найдёт «смету»." />
          : error ? <Failed error={error} onRetry={reload} />
          : loading || !data ? <Loading />
          : data.length === 0 ? <Empty text="Ничего не найдено." />
          : (
            <ul className="flex flex-col gap-3">
              {data.map((hit) => (
                <li key={`${hit.device_id}-${hit.session}-${hit.part}-${hit.idx}`}>
                  <Link href={sessionHref(hit.device_id, hit.session, hit.part, hit.idx)}
                        className="block rounded-2xl border border-separator-border bg-background-primary-default p-6 hover:bg-background-primary-hover">
                    <p className="nums text-caption-1-medium uppercase tracking-[0.12em] text-text-tertiary">
                      сессия {String(hit.session).padStart(4, "0")} · {formatClock(hit.start_abs)}
                    </p>
                    <p className="mt-2 text-body-regular text-text-secondary">
                      <span className="text-body-medium text-text-primary">{hit.label}: </span>
                      {hit.segments.map((part, index) =>
                        part.hit ? <mark key={index}>{part.text}</mark> : <span key={index}>{part.text}</span>)}
                    </p>
                  </Link>
                </li>
              ))}
            </ul>
          )}
      </div>
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
