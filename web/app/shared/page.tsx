"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { PageHeader } from "@/components/recorder/page-header";
import { PlayerProvider } from "@/components/recorder/player";
import { SummaryList } from "@/components/recorder/summary-list";
import { Transcript } from "@/components/recorder/transcript";
import { Empty, EmptyNote, Loading } from "@/components/recorder/states";
import { ApiError, sharedGet } from "@/lib/api";
import type { SharedDetail } from "@/lib/api";
import { groupRows, withVoiceLabels } from "@/lib/speakers";
import { formatClock, formatDay, formatTime, keepNumbers, recordTitle } from "@/lib/format";

/** У гостя нет меток кнопкой: сервер их не отдаёт. */
const NO_MARKS = new Set<string>();

function Body() {
  const raw = useSearchParams().get("t") ?? "";
  // Ключ с «/» или «..» увёл бы запрос на чужой путь (вплоть до /login), а гость
  // увидел бы «Проверьте связь»: такой ключ заведомо мёртвый, сети не трогаем.
  const token = /^[A-Za-z0-9_-]+$/.test(raw) ? raw : "";
  const [result, setResult] = useState<{ token: string; data: SharedDetail | null; error: Error | null }>(
    { token: "", data: null, error: null });

  useEffect(() => {
    if (!token) return;
    let alive = true;
    sharedGet(token).then(
      (data) => { if (alive) setResult({ token, data, error: null }); },
      (error: Error) => { if (alive) setResult({ token, data: null, error }); },
    );
    return () => { alive = false; };
  }, [token]);

  // Реплики подписаны голосом, как у владельца. «Это я» гостю не отдаётся и
  // на подписи не влияет.
  const data = useMemo(() => result.data && {
    ...result.data,
    timeline: withVoiceLabels(result.data.timeline, groupRows(
      result.data.speakers.map((row) => ({ ...row, is_me: false })),
      result.data.voice_groups, result.data.short_keys)),
  }, [result.data]);

  const error = result.token === token ? result.error : null;
  if (!token || error) {
    const gone = !token || (error instanceof ApiError && error.status === 404);
    return gone
      ? <Empty title="Ссылка больше не работает" text="Попросите владельца прислать новую." />
      : <Empty title="Не удалось открыть запись" text="Проверьте связь и обновите страницу." />;
  }
  if (!data || result.token !== token) return <Loading />;

  const topic = data.summary?.topics?.[0] ?? null;
  const title = recordTitle({ topic, day: data.day, first_at: data.first_at });
  const duration = data.parts.reduce((end, part) => Math.max(end, part.offset + part.seconds), 0);

  return (
    <PlayerProvider deviceId={`share-${token}`} session={0} title={title} parts={data.parts}
                    audioBase={`/share/${encodeURIComponent(token)}/audio`}>
      <PageHeader
        title={title}
        aside={
          <span className="flex flex-wrap items-center gap-x-3.5 gap-y-1">
            {/* Без темы дата уже в заголовке (recordTitle), второй раз не повторяем. */}
            {topic ? <span>{formatDay(data.day)}, {formatTime(data.first_at)}</span> : null}
            {duration > 0 ? <span className="font-mono">{formatClock(duration)}</span> : null}
          </span>
        }
      />
      {data.summary ? (
        <>
          {/* Та же табличка, что у владельца: оформление в styles/studio.css. */}
          <section className="studio-summary" aria-labelledby="summary-label">
            <h2 id="summary-label" className="studio-summary-label">Главное из разговора</h2>
            <p>{keepNumbers(data.summary.summary)}</p>
          </section>
          <SummaryList title="Решения" items={data.summary.decisions} />
          <SummaryList title="Задачи" items={data.summary.tasks} />
          <SummaryList title="Вопросы без ответа" items={data.summary.open_questions} />
          <SummaryList title="Факты" items={data.summary.facts} />
        </>
      ) : null}
      {data.timeline.length === 0
        ? <EmptyNote>Расшифровки пока нет.</EmptyNote>
        : <Transcript timeline={data.timeline} marked={NO_MARKS}
                      received={new Set(data.parts.map((part) => part.part))} />}
    </PlayerProvider>
  );
}

export default function SharedPage() {
  // Статический экспорт требует Suspense вокруг useSearchParams.
  return (
    <Suspense fallback={<Loading />}>
      <Body />
    </Suspense>
  );
}
