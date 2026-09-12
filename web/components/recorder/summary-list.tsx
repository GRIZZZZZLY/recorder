"use client";

import { useSeek } from "@/components/recorder/player";
import { SourceDot } from "@/components/recorder/source-link";
import type { SummaryItem } from "@/lib/api";

/** Пункт саммари: время, текст, состояние источника и перемотка на реплику. */
export function SummaryList({ title, items }: { title: string; items: SummaryItem[] }) {
  const seek = useSeek();
  if (items.length === 0) return null;

  return (
    <section className="mt-8">
      <h2 className="text-title-3-semibold text-text-primary">{title}</h2>
      <ul className="mt-3 flex flex-col gap-3">
        {items.map((item, index) => (
          <li key={`${item.time}-${index}`}
              className="rounded-2xl border border-separator-border bg-background-primary-default p-6">
            <p className="text-body-regular text-text-primary">
              <span className="nums mr-2 text-text-tertiary">{item.time}</span>
              {item.text}
            </p>
            {(item.owner !== undefined || item.due !== undefined) && (
              <p className="mt-1 text-body-2-regular text-text-tertiary">
                {item.owner || "исполнитель не назван"} · {item.due || "срок не назван"}
              </p>
            )}
            <div className="mt-3 flex items-center gap-2">
              <SourceDot source={item.source ?? null} />
              {item.source ? (
                <button type="button"
                        className="text-body-2-medium text-text-secondary underline-offset-2 hover:underline"
                        onClick={() => seek(item.source!.part, item.source!.start)}>
                  Послушать основание
                </button>
              ) : (
                <span className="text-body-2-regular text-text-tertiary">источник не найден</span>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
