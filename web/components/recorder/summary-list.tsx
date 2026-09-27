"use client";

import { Card, Row, Section } from "@/components/recorder/list";
import { EvidenceTime, SourceNote } from "@/components/recorder/ui";
import type { SummaryItem } from "@/lib/api";
import { keepNumbers } from "@/lib/format";

/**
 * Пункты саммари: время-кнопка «основание» (реплика, из которой пункт взят)
 * и текст. Без источника — просто время: слушать нечего.
 */
export function SummaryList({ id, title, items }: { id?: string; title: string; items: SummaryItem[] }) {
  if (items.length === 0) return null;

  return (
    <Section id={id} note={`${title} · ${items.length}`}>
      <Card>
        {items.map((item, index) => (
          <Row key={`${item.time}-${index}`} className="items-start gap-3.5">
            <EvidenceTime time={item.time} label={item.text} context={item.text} source={item.source} />
            <span className="min-w-0 flex-1 pt-1">
              {keepNumbers(item.text)}
              {(item.owner || item.due) && (
                <span className="block text-note text-text-secondary">
                  {[item.owner, item.due].filter(Boolean).join(" · ")}
                </span>
              )}
              <SourceNote source={item.source} />
            </span>
          </Row>
        ))}
      </Card>
    </Section>
  );
}
