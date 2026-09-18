"use client";

import { Section } from "@/components/recorder/list";
import { PageHeader } from "@/components/recorder/page-header";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { StudioRecord } from "@/components/recorder/studio-record";
import { useApi } from "@/lib/use-api";
import type { SessionRow } from "@/lib/api";
import { formatDayGroup, formatDuration } from "@/lib/format";

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
        byDay(data).map((group) => (
          <Section key={group.day ?? "no-day"} note={formatDayGroup(group.day)}>
            {/* Разговор узнают по часу, теме и людям — всё это на карточке. */}
            <div className="studio-record-grid">
              {group.rows.map((row) => (
                <StudioRecord key={`${row.device_id}-${row.session}`} row={row} />
              ))}
            </div>
          </Section>
        ))
      )}
    </>
  );
}
