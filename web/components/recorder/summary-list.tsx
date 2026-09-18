"use client";

import { useSeek } from "@/components/recorder/player";
import { Card, Row, Section, Time } from "@/components/recorder/list";
import { Evidence } from "@/components/recorder/ui";
import { revealUtterance } from "@/lib/reveal";
import type { SummaryItem } from "@/lib/api";

/**
 * Пункты саммари: время, текст и кнопка «основание» — реплика, из которой
 * пункт взят. Без источника кнопка выключена: слушать нечего.
 */
export function SummaryList({ title, items }: { title: string; items: SummaryItem[] }) {
  const seek = useSeek();
  if (items.length === 0) return null;

  return (
    <Section note={`${title} · ${items.length}`}>
      <Card>
        {items.map((item, index) => (
          <Row key={`${item.time}-${index}`} className="gap-3.5">
            <Time className="shrink-0">{item.time}</Time>
            <span className="min-w-0 flex-1">
              {item.text}
              {(item.owner || item.due) && (
                <span className="block text-note text-text-secondary">
                  {[item.owner, item.due].filter(Boolean).join(" · ")}
                </span>
              )}
            </span>
            <Evidence
              disabled={!item.source}
              title={item.source ? undefined : "источник не найден"}
              onClick={() => {
                if (!item.source) return;
                seek(item.source.part, item.source.start, item.text);
                revealUtterance(item.source.part, item.source.idx);
              }}
            />
          </Row>
        ))}
      </Card>
    </Section>
  );
}
