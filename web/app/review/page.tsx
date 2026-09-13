"use client";

import { Card, Row, Section } from "@/components/recorder/list";
import { PageHeader } from "@/components/recorder/page-header";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { EvidenceLink, SourceDot } from "@/components/recorder/ui";
import { useApi } from "@/lib/use-api";
import type { ReviewItem } from "@/lib/api";
import { formatDayShort } from "@/lib/format";
import { at } from "@/lib/motion";
import { sessionHref } from "@/lib/session-link";

export default function ReviewPage() {
  const { data, error, loading, reload } = useApi<ReviewItem[]>("/review");

  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !data) return <Loading />;

  // Группируем по виду спорного места: у каждого вида своя причина, и читать
  // их вперемешку бессмысленно.
  const groups = new Map<string, ReviewItem[]>();
  for (const item of data) {
    const bucket = groups.get(item.kind) ?? [];
    bucket.push(item);
    groups.set(item.kind, bucket);
  }

  return (
    <>
      <PageHeader title="Спорные места" />
      <p className="enter max-w-[65ch] text-[15px] text-text-secondary" style={at(1)}>
        Обработка ничего из этого не ждёт. Это места, где саммари не уверено —
        стоит послушать и решить самому.
      </p>

      {data.length === 0 ? (
        <Empty title="Нечего проверять"
               text="Саммари уверено во всём, что собрало из последних разговоров." />
      ) : (
        [...groups.entries()].map(([kind, items], groupIndex) => (
          <Section key={kind} note={`${kind} · ${items.length}`} at={groupIndex + 2}>
            <Card className="cascade">
              {items.map((item, index) => (
                <Row key={`${item.session}-${index}`} className="flex-wrap items-start gap-y-2">
                  {/* Точка вспыхивает при входе: страница и просит смотреть сюда. */}
                  <span className="pt-1"><SourceDot source={item.source} flash /></span>
                  <span className="flex min-w-0 flex-1 flex-col gap-1">
                    <span>{item.text}</span>
                    <span className="text-note text-text-secondary">{item.detail}</span>
                  </span>
                  <span className="shrink-0 font-mono text-note text-text-tertiary">
                    {String(item.session).padStart(4, "0")} · {formatDayShort(item.day)}
                  </span>
                  <EvidenceLink href={item.source
                    ? sessionHref(item.device_id, item.session, item.source.part, item.source.idx)
                    : null} />
                </Row>
              ))}
            </Card>
          </Section>
        ))
      )}
    </>
  );
}
