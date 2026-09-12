"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Button } from "@/components/base/buttons/button";
import { Input } from "@/components/base/input/input";
import { PageTitle } from "@/components/recorder/eyebrow";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { useApi } from "@/lib/use-api";
import type { SearchHit } from "@/lib/api";
import { formatClock } from "@/lib/format";

function Body() {
  const initial = useSearchParams().get("q") ?? "";
  const [draft, setDraft] = useState(initial);
  const [query, setQuery] = useState(initial);
  const { data, error, loading, reload } =
    useApi<SearchHit[]>(query.trim() ? `/search?q=${encodeURIComponent(query)}` : null);

  return (
    <>
      <PageTitle eyebrow="ДИКТОФОН" plain="Найти" italic="сказанное" />
      <form className="flex flex-wrap items-center gap-3"
            onSubmit={(event) => { event.preventDefault(); setQuery(draft); }}>
        <Input className="min-w-48 flex-1" placeholder="Слово или фраза"
               value={draft} onChange={setDraft} />
        <Button type="submit" variant="primary" size="small">Найти</Button>
      </form>

      <div className="mt-6">
        {!query.trim() ? <Empty text="Введите слово: поиск идёт по леммам, «сметы» найдёт «смету»." />
          : error ? <Failed error={error} onRetry={reload} />
          : loading || !data ? <Loading />
          : data.length === 0 ? <Empty text="Ничего не найдено." />
          : (
            <ul className="flex flex-col gap-3">
              {data.map((hit) => (
                <li key={`${hit.session}-${hit.part}-${hit.idx}`}>
                  <Link href={`/session/?d=${encodeURIComponent(hit.device_id)}&n=${hit.session}#u-${hit.part}-${hit.idx}`}
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
