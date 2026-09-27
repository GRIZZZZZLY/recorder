"use client";

import { Card, Row, Section } from "@/components/recorder/list";
import { PageHeader } from "@/components/recorder/page-header";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { EvidenceLink, SourceDot } from "@/components/recorder/ui";
import { useApi } from "@/lib/use-api";
import type { ReviewItem } from "@/lib/api";
import { formatClock, formatDayShort } from "@/lib/format";
import { sessionHref } from "@/lib/session-link";

export default function ReviewPage() {
  const { data, error, loading, reload } = useApi<ReviewItem[]>("/review");

  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !data) return <Loading />;

  // Один пункт — одна строка, причины под ним. Группы по виду причины
  // показывали одну и ту же задачу дважды, а счётчик считал причины.
  const items = new Map<string, { item: ReviewItem; reasons: { kind: string; detail: string }[] }>();
  for (const item of data) {
    const key = `${item.device_id}-${item.session}-${item.text}`;
    const entry = items.get(key) ?? { item, reasons: [] };
    entry.reasons.push({ kind: item.kind, detail: item.detail });
    items.set(key, entry);
  }

  return (
    <>
      <PageHeader title="Спорные места" />
      <p className="max-w-[65ch] text-[15px] text-text-secondary">
        Обработка ничего из этого не ждёт. Это места, где саммари не уверено —
        стоит послушать и решить самому.
      </p>

      {data.length === 0 ? (
        <Empty title="Нечего проверять"
               text="Саммари уверено во всём, что собрало из последних разговоров." />
      ) : (
        <Section note={`Проверить · ${items.size}`}>
          <Card>
            {[...items.values()].map(({ item, reasons }, index) => (
              <Row key={`${item.session}-${index}`} className="flex-wrap items-start gap-y-2">
                {/* Точка вспыхивает при входе: страница и просит смотреть сюда. */}
                <span className="pt-1"><SourceDot source={item.source} /></span>
                {/* На телефоне текст на всю строку, день и «основание» —
                    второй строкой: рядом с ними тексту оставалось 71 px. */}
                <span className="flex min-w-0 flex-1 flex-col gap-1 max-md:basis-[calc(100%-7rem)]">
                  <span>{item.text}</span>
                  {reasons.map((reason) => (
                    <span key={reason.kind} className="text-note">
                      <span className="text-warn">{reason.kind}</span>
                      <span className="text-text-secondary"> · {reason.detail}</span>
                    </span>
                  ))}
                </span>
                {/* День, без номера сессии: номер владельцу ничего не говорит. */}
                <span className="shrink-0 text-note text-text-tertiary max-md:order-2 max-md:ml-5">
                  {item.day ? formatDayShort(item.day) : null}
                </span>
                <span className="shrink-0 max-md:order-1">
                  <EvidenceLink time={item.source ? formatClock(item.source.start_abs) : ""}
                                context={item.text} href={item.source
                    ? sessionHref(item.device_id, item.session, item.source.part, item.source.idx)
                    : null} />
                </span>
              </Row>
            ))}
          </Card>
        </Section>
      )}
    </>
  );
}
