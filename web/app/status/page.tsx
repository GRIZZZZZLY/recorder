"use client";

import { Card, Row, Section, SessionNo } from "@/components/recorder/list";
import { PageHeader } from "@/components/recorder/page-header";
import { Stats } from "@/components/recorder/stats";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { Dot, Retry } from "@/components/recorder/ui";
import { useApi } from "@/lib/use-api";
import { count, formatAgo, formatDuration } from "@/lib/format";
import { isLive, useNow } from "@/lib/use-now";

type Status = {
  transcripts_pending: number;
  summaries_pending: number;
  deepgram_minutes: number;
  failed_parts: { device_id: string; session: number; part: number; attempts: number; last_error: string }[];
  stuck_summaries: { device_id: string; session: number; attempts: number; last_error: string }[];
  recovered_parts: { device_id: string; session: number; part: number }[];
  last_upload: { device_id: string; at: string; parts: number }[];
  /** Что диктофон рассказал о себе: пусто, пока прошивка это не шлёт. */
  devices: { device_id: string; battery: number | null; firmware: string | null; updated_at: string }[];
  gaps: { session: number; missing: number[] }[];
  duration_mismatch: { session: number; part: number; by_size: number; by_deepgram: number }[];
};

/** Строка «свойство — значение» в карточке сводных сведений. */
function Fact({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Row className="justify-between gap-4">
      <span className="text-text-secondary">{title}</span>
      <span className="text-right">{children}</span>
    </Row>
  );
}

export default function StatusPage() {
  const { data, error, loading, reload } = useApi<Status>("/status");
  const now = useNow();

  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !data) return <Loading />;

  const upload = data.last_upload[0];
  // Состояние того диктофона, который выгружался последним: он же и показан выше.
  const device = data.devices.find((row) => row.device_id === upload?.device_id) ?? data.devices[0];
  const live = isLive(upload?.at, now);
  const broken = data.failed_parts.length + data.stuck_summaries.length;

  return (
    <>
      <PageHeader
        title="Состояние"
        aside={
          <span className="flex items-center gap-2">
            <Dot tone={live ? "accent" : upload ? "ok" : "missing"} live={live} />
            {upload ? `последняя выгрузка ${formatAgo(upload.at, now)}` : "выгрузок не было"}
          </span>
        }
      />

      <Stats items={[
        { value: data.transcripts_pending, label: "в очереди расшифровки",
          dot: data.transcripts_pending ? "warn" : "ok" },
        { value: data.summaries_pending, label: "ждут саммари",
          dot: data.summaries_pending ? "warn" : "ok" },
        { value: broken, label: "с ошибкой", tone: broken ? "accent" : undefined,
          dot: broken ? "accent" : "ok" },
      ]} />

      <div className="grid gap-5 lg:grid-cols-2">
        <Section note="Устройство">
          <Card>
            {upload ? (
              <>
                <Fact title="Последняя выгрузка">
                  {formatAgo(upload.at, now)} · {count(upload.parts, "часть", "части", "частей")}
                </Fact>
                <Fact title="Устройство"><span className="font-mono text-note">{upload.device_id}</span></Fact>
              </>
            ) : (
              <Fact title="Последняя выгрузка">
                <span className="text-text-secondary">выгрузок не было</span>
              </Fact>
            )}
            {data.last_upload.slice(1).map((row) => (
              <Fact key={row.device_id} title={row.device_id}>
                {formatAgo(row.at, now)} · {count(row.parts, "часть", "части", "частей")}
              </Fact>
            ))}
            {/* Заряд и прошивка приходят заголовками выгрузки. Прошивка на плате
                их ещё не шлёт, поэтому здесь честное «нет данных», а не
                выдуманный процент: плата в пути. */}
            <Fact title="Заряд">
              {device?.battery === null || device?.battery === undefined ? (
                <span className="text-text-tertiary">диктофон ещё не сообщал</span>
              ) : (
                <span className="flex items-center justify-end gap-2">
                  <Dot tone={device.battery > 20 ? "ok" : device.battery > 10 ? "warn" : "accent"} />
                  <span className="font-mono">{device.battery}%</span>
                </span>
              )}
            </Fact>
            <Fact title="Прошивка">
              {device?.firmware
                ? <span className="font-mono text-note">{device.firmware}</span>
                : <span className="text-text-tertiary">диктофон ещё не сообщал</span>}
            </Fact>
          </Card>
        </Section>

        <Section note="Конвейер">
          <Card>
            <Fact title="Расшифровка">
              <span className="flex items-center justify-end gap-2">
                <Dot tone={data.transcripts_pending ? "warn" : "ok"} />
                {data.transcripts_pending
                  ? `${count(data.transcripts_pending, "часть", "части", "частей")} в очереди`
                  : "очередь пуста"}
              </span>
            </Fact>
            <Fact title="Саммари">
              <span className="flex items-center justify-end gap-2">
                <Dot tone={data.summaries_pending ? "warn" : "ok"} />
                {data.summaries_pending ? `${data.summaries_pending} ждёт` : "все собраны"}
              </span>
            </Fact>
            <Fact title="Расшифровано Deepgram">
              {formatDuration(data.deepgram_minutes * 60)}
              <span className="block text-note text-text-tertiary">оценка расхода кредита</span>
            </Fact>
          </Card>
        </Section>
      </div>

      {data.failed_parts.length > 0 && (
        <Section note={`Части с ошибкой · ${data.failed_parts.length}`}>
          <Card>
            {data.failed_parts.map((row) => (
              <Row key={`${row.session}-${row.part}`} className="flex-wrap gap-y-2">
                <Dot tone="accent" />
                <SessionNo session={row.session} />
                <span className="shrink-0 font-mono text-note text-text-secondary">
                  часть {String(row.part).padStart(2, "0")}
                </span>
                <span className="min-w-0 flex-1 truncate font-mono text-note text-text-secondary">
                  {row.last_error}
                </span>
                <span className="shrink-0 text-note text-text-tertiary">
                  попыток {row.attempts}
                </span>
                <Retry path={`/parts/${encodeURIComponent(row.device_id)}/${row.session}/${row.part}/retry`}
                       onDone={reload} />
              </Row>
            ))}
          </Card>
        </Section>
      )}

      {data.stuck_summaries.length > 0 && (
        <Section note={`Саммари с ошибкой · ${data.stuck_summaries.length}`}>
          <Card>
            {data.stuck_summaries.map((row) => (
              <Row key={row.session} className="flex-wrap gap-y-2">
                <Dot tone="accent" />
                <SessionNo session={row.session} />
                <span className="min-w-0 flex-1 truncate font-mono text-note text-text-secondary">
                  {row.last_error}
                </span>
                <span className="shrink-0 text-note text-text-tertiary">попыток {row.attempts}</span>
                <Retry path={`/sessions/${encodeURIComponent(row.device_id)}/${row.session}/retry-summary`}
                       onDone={reload} />
              </Row>
            ))}
          </Card>
        </Section>
      )}

      {data.gaps.length > 0 && (
        <Section note={`Пропуски частей · ${data.gaps.length}`}
                 aside="номера отсутствуют: запись потеряна или ещё не выгружена">
          <Card>
            {data.gaps.map((row) => (
              <Row key={row.session}>
                <Dot tone="warn" />
                <SessionNo session={row.session} />
                <span className="min-w-0 flex-1 font-mono text-note text-text-secondary">
                  нет частей {row.missing.join(", ")}
                </span>
              </Row>
            ))}
          </Card>
        </Section>
      )}

      {data.duration_mismatch.length > 0 && (
        <Section note={`Расхождение длительности · ${data.duration_mismatch.length}`}
                 aside="размер файла и ответ Deepgram расходятся больше чем на 2%">
          <Card>
            {data.duration_mismatch.map((row) => (
              <Row key={`${row.session}-${row.part}`}>
                <Dot tone="warn" />
                <SessionNo session={row.session} />
                <span className="shrink-0 font-mono text-note text-text-secondary">
                  часть {String(row.part).padStart(2, "0")}
                </span>
                <span className="min-w-0 flex-1 text-right font-mono text-note text-text-secondary">
                  по файлу {row.by_size.toFixed(1)} с · по Deepgram {row.by_deepgram.toFixed(1)} с
                </span>
              </Row>
            ))}
          </Card>
        </Section>
      )}

      {data.recovered_parts.length > 0 && (
        <Section note={`Восстановленные части · ${data.recovered_parts.length}`}
                 aside="питание пропало во время записи, заголовок починен при старте">
          <Card>
            {data.recovered_parts.map((row) => (
              <Row key={`${row.session}-${row.part}`}>
                <Dot tone="ok" />
                <SessionNo session={row.session} />
                <span className="min-w-0 flex-1 font-mono text-note text-text-secondary">
                  часть {String(row.part).padStart(2, "0")}
                </span>
              </Row>
            ))}
          </Card>
        </Section>
      )}

      {broken === 0 && data.gaps.length === 0 && data.duration_mismatch.length === 0 && (
        <Empty title="Обработка идёт ровно"
               text="Ошибок, пропусков и расхождений нет — смотреть нечего." />
      )}
    </>
  );
}
