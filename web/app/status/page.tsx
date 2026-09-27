"use client";

import { Card, Row, Section, SessionNo } from "@/components/recorder/list";
import { PageHeader } from "@/components/recorder/page-header";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { Dot, Retry } from "@/components/recorder/ui";
import { useApi } from "@/lib/use-api";
import { count, formatAgo, formatDuration, formatStamp, humanError } from "@/lib/format";
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
/** «40 с», «25,5 с»: запятая, как принято по-русски, и без лишнего «,0». */
function seconds(value: number) {
  return `${value.toLocaleString("ru", { maximumFractionDigits: 1 })} с`;
}

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
  // Неделя без выгрузок — повод посмотреть на диктофон: разряжен, забыт,
  // не видит сеть. Тишина в выходные — нет.
  const silentDays = upload ? (now.getTime() - new Date(`${upload.at.replace(" ", "T")}Z`).getTime()) / 86_400_000 : 0;
  const broken = data.failed_parts.length + data.stuck_summaries.length;

  return (
    <>
      <PageHeader
        title="Состояние"
        aside={
          <span className="flex items-center gap-2">
            {/* Зелёная точка при одиннадцати днях тишины читалась как «всё
                хорошо»: вне записи точка нейтральная. */}
            <Dot tone={live ? "accent" : !upload ? "missing" : silentDays >= 7 ? "warn" : "muted"} live={live} />
            {upload ? `последняя выгрузка ${formatAgo(upload.at, now)}` : "выгрузок не было"}
          </span>
        }
      />

      {/* Очередь и ошибки — строками «Обработки» и списками ниже: крупные
          числа наверху повторяли их же. */}

      <div className="grid gap-5 lg:grid-cols-2">
        <Section note="Устройство">
          <Card>
            {upload ? (
              <>
                {/* Точное время: «11 дней назад» уже стоит в шапке страницы. */}
                <Fact title="Последняя выгрузка">
                  {formatStamp(upload.at)} · {count(upload.parts, "часть", "части", "частей")}
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
            {(device?.battery === null || device?.battery === undefined) && !device?.firmware ? (
              // Ни заряда, ни прошивки: одна строка вместо двух одинаковых.
              <Fact title="Заряд и прошивка">
                <span className="text-text-tertiary">диктофон ещё не сообщал</span>
              </Fact>
            ) : (<>
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
            </>)}
          </Card>
        </Section>

        <Section note="Обработка">
          <Card>
            <Fact title="Расшифровка">
              <span className="flex items-center justify-end gap-2">
                {data.transcripts_pending ? <Dot tone="warn" /> : null}
                {data.transcripts_pending
                  ? `${count(data.transcripts_pending, "часть", "части", "частей")} в очереди`
                  : "очередь пуста"}
              </span>
            </Fact>
            <Fact title="Саммари">
              <span className="flex items-center justify-end gap-2">
                {data.summaries_pending ? <Dot tone="warn" /> : null}
                {data.summaries_pending ? `ждут: ${data.summaries_pending}` : "все собраны"}
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
                <span className="shrink-0 text-note text-text-primary">не расшифровалась</span>
                {/* Причина — то, ради чего строка есть. На телефоне она уходит
                    отдельной строкой целиком: рядом с номером, попытками и
                    «Повторить» ей оставалось 6 px и одна буква. */}
                <span className="min-w-0 flex-1 text-note max-md:order-last max-md:basis-full">
                  <span className="text-text-primary">{humanError(row.last_error)}</span>
                  <span className="block truncate font-mono text-micro text-text-tertiary max-md:whitespace-normal max-md:break-words"
                        title={row.last_error}>{row.last_error}</span>
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
                <span className="shrink-0 text-note text-text-primary">не собралось</span>
                {/* Причина — то, ради чего строка есть. На телефоне она уходит
                    отдельной строкой целиком: рядом с номером, попытками и
                    «Повторить» ей оставалось 6 px и одна буква. */}
                <span className="min-w-0 flex-1 text-note max-md:order-last max-md:basis-full">
                  <span className="text-text-primary">{humanError(row.last_error)}</span>
                  <span className="block truncate font-mono text-micro text-text-tertiary max-md:whitespace-normal max-md:break-words"
                        title={row.last_error}>{row.last_error}</span>
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
                  {row.missing.length === 1 ? "не пришла часть" : "не пришли части"}{" "}
                  {row.missing.map((n) => String(n).padStart(2, "0")).join(", ")}
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
                  по файлу {seconds(row.by_size)} · по Deepgram {seconds(row.by_deepgram)}
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
