"use client";

import { BookmarkSimple } from "@phosphor-icons/react/dist/ssr";
import { Card, RowLink, Section } from "@/components/recorder/list";
import { PageHeader } from "@/components/recorder/page-header";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { Waveform } from "@/components/recorder/waveform";
import { Dot } from "@/components/recorder/ui";
import { useApi } from "@/lib/use-api";
import type { SessionRow } from "@/lib/api";
import { formatDayGroup, formatDuration, formatTime } from "@/lib/format";
import { sessionHref } from "@/lib/session-link";
import { STATUS_TEXT, statusTone } from "@/lib/session-status";
import { cx } from "@/utils/cx";

/** Записи одного дня подряд: список приходит отсортированным, свежие первыми. */
function byDay(rows: SessionRow[]) {
  const groups: { day: string | null; rows: SessionRow[] }[] = [];
  for (const row of rows) {
    const last = groups[groups.length - 1];
    if (last && last.day === row.day) last.rows.push(row);
    else groups.push({ day: row.day, rows: [row] });
  }
  return groups;
}

export default function RecordsPage() {
  const { data, error, loading, reload } = useApi<SessionRow[]>("/sessions");

  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !data) return <Loading />;

  const total = data.reduce((sum, row) => sum + row.seconds, 0);

  return (
    <>
      <PageHeader
        title="Записи"
        // Одно число, и то показание прибора: сколько звука лежит в архиве.
        // Счётчик сессий владельцу ничего не говорит.
        aside={data.length > 1 ? formatDuration(total) : undefined}
      />

      {data.length === 0 ? (
        <Empty title="Записей ещё нет"
               text="Диктофон не присылал ни одной части. Проверьте, что устройство в сети." />
      ) : (
        byDay(data).map((group, index) => (
          <Section key={group.day ?? "no-day"} note={formatDayGroup(group.day)} at={index + 1}>
            <Card className="cascade">
              {group.rows.map((row) => {
                const tone = statusTone(row);
                return (
                  <RowLink key={`${row.device_id}-${row.session}`}
                           href={sessionHref(row.device_id, row.session)}>
                    {/* Разговор узнают по часу, теме и людям. Номер сессии и число
                        частей — внутренности системы; число частей всплывает только
                        пока расшифровка не догнала запись. */}
                    <span className="shrink-0 font-mono text-note text-text-secondary">
                      {formatTime(row.first_at)}
                    </span>
                    <span className="min-w-0 flex-1 truncate">
                      {row.topic || <span className="text-text-secondary">тема появится после саммари</span>}
                      {row.speakers.length > 0 && (
                        <span className="block truncate text-note text-text-tertiary sm:inline sm:before:content-['_·_']">
                          {row.speakers.join(", ")}
                        </span>
                      )}
                      {row.done_parts < row.parts && (
                        <span className="block text-note text-text-tertiary sm:inline sm:before:content-['_·_']">
                          расшифровано {row.done_parts} из {row.parts}
                        </span>
                      )}
                    </span>
                    {row.marks.length > 0 && (
                      <span className="flex shrink-0 items-center gap-1 text-note text-text-secondary"
                            title={`отмечено кнопкой: ${row.marks.length}`}>
                        <BookmarkSimple size={12} weight="fill" aria-hidden className="text-mark" />
                        {row.marks.length}
                      </span>
                    )}
                    <Waveform values={row.envelope} height={24}
                              className="wave-grow hidden w-[220px] shrink-0 lg:flex" />
                    <span className="shrink-0 text-right text-note text-text-secondary">
                      {formatDuration(row.seconds)}
                    </span>
                    <span className={cx("flex w-[130px] shrink-0 items-center justify-end gap-2 text-note",
                                        tone === "accent" ? "text-accent-text" : "text-text-secondary")}>
                      <span className="hidden md:inline">{STATUS_TEXT[tone]}</span>
                      <Dot tone={tone} title={STATUS_TEXT[tone]} />
                    </span>
                  </RowLink>
                );
              })}
            </Card>
          </Section>
        ))
      )}
    </>
  );
}
