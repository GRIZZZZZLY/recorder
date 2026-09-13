"use client";

import { PageTitle } from "@/components/recorder/eyebrow";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { Tile } from "@/components/recorder/tile";
import { useApi } from "@/lib/use-api";
import { formatMinutes } from "@/lib/format";

type Status = {
  transcripts_pending: number;
  summaries_pending: number;
  deepgram_minutes: number;
  failed_parts: { session: number; part: number; attempts: number; last_error: string }[];
  stuck_summaries: { session: number; attempts: number; last_error: string }[];
  recovered_parts: { session: number; part: number }[];
  last_upload: { device_id: string; at: string; parts: number }[];
  gaps: { session: number; missing: number[] }[];
  duration_mismatch: { session: number; part: number; by_size: number; by_deepgram: number }[];
};

function Rows({ title, hint, head, rows }: {
  title: string; hint?: string; head: string[]; rows: (string | number)[][];
}) {
  return (
    <section className="mt-10">
      <h2 className="text-title-3-semibold text-text-primary">{title}</h2>
      {hint ? <p className="mt-1 text-body-2-regular text-text-tertiary">{hint}</p> : null}
      {rows.length === 0 ? <div className="mt-3"><Empty text="Нет." /></div> : (
        // overflow-x-auto: тексты ошибок Deepgram длинные, на телефоне таблица
        // шире экрана — прокручивается она, а не страница.
        <div className="mt-3 overflow-x-auto rounded-2xl border border-separator-border">
          <table className="w-full text-left">
            <caption className="sr-only">{title}</caption>
            <thead>
              <tr className="border-b border-separator-border">
                {head.map((cell) => (
                  <th key={cell} scope="col"
                      className="px-4 py-2 text-caption-1-medium uppercase tracking-[0.12em] text-text-tertiary">
                    {cell}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={index} className="border-b border-separator-border last:border-0">
                  {row.map((cell, cellIndex) =>
                    cellIndex === 0 ? (
                      <th key={cellIndex} scope="row"
                          className="nums px-4 py-2 text-body-2-medium font-medium text-text-primary">
                        {cell}
                      </th>
                    ) : (
                      <td key={cellIndex} className="nums px-4 py-2 text-body-2-regular text-text-secondary">
                        {cell}
                      </td>
                    ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default function StatusPage() {
  const { data, error, loading, reload } = useApi<Status>("/status");

  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !data) return <Loading />;

  return (
    <>
      <PageTitle eyebrow="ДИКТОФОН" plain="Что происходит с" italic="обработкой" />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-3">
        <Tile label="В очереди на расшифровку" value={data.transcripts_pending} />
        <Tile label="Ждут саммари" value={data.summaries_pending} />
        <Tile label="Расшифровано Deepgram" value={`${formatMinutes(data.deepgram_minutes)} мин`}
              hint="оценка расхода кредита" />
      </div>

      <Rows title="Последняя выгрузка с диктофона" head={["Устройство", "Когда", "Частей всего"]}
            rows={data.last_upload.map((row) => [row.device_id, row.at, row.parts])} />
      <Rows title="Части с ошибкой расшифровки" head={["Сессия", "Часть", "Попыток", "Ошибка"]}
            rows={data.failed_parts.map((row) => [row.session, row.part, row.attempts, row.last_error])} />
      <Rows title="Саммари с ошибкой" head={["Сессия", "Попыток", "Ошибка"]}
            rows={data.stuck_summaries.map((row) => [row.session, row.attempts, row.last_error])} />
      <Rows title="Пропуски частей"
            hint="Номера отсутствуют: запись потеряна или ещё не выгружена."
            head={["Сессия", "Нет частей"]}
            rows={data.gaps.map((row) => [row.session, row.missing.join(", ")])} />
      <Rows title="Расхождение длительности"
            hint="Размер файла и ответ Deepgram расходятся больше чем на 2%: часть аудио могла не обработаться."
            head={["Сессия", "Часть", "По файлу", "По Deepgram"]}
            rows={data.duration_mismatch.map((row) =>
              [row.session, row.part, `${row.by_size.toFixed(1)} с`, `${row.by_deepgram.toFixed(1)} с`])} />
      <Rows title="Восстановленные части"
            hint="Питание пропало во время записи, заголовок починен при старте диктофона."
            head={["Сессия", "Часть"]}
            rows={data.recovered_parts.map((row) => [row.session, row.part])} />
    </>
  );
}
