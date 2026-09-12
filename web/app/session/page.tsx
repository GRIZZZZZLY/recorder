"use client";

import { Suspense, useMemo } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { PageTitle } from "@/components/recorder/eyebrow";
import { PlayerProvider, useSeek } from "@/components/recorder/player";
import { SpeakersForm } from "@/components/recorder/speakers-form";
import { SummaryList } from "@/components/recorder/summary-list";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { useApi } from "@/lib/use-api";
import type { Summary, Utterance } from "@/lib/api";
import { formatClock, formatDay } from "@/lib/format";

type Detail = {
  device_id: string;
  session: number;
  day: string;
  parts: { part: number; recovered: number; transcript_status: string | null; last_error: string | null }[];
  summary: Summary | null;
  summary_error: string | null;
  timeline: Utterance[];
  speakers: { speaker: number; name: string; is_me: boolean }[];
};

function Transcript({ timeline }: { timeline: Utterance[] }) {
  const seek = useSeek();
  // Группировка заранее: заголовок части ставится по данным, а не по счётчику,
  // который менялся бы прямо во время отрисовки.
  const byPart = useMemo(() => {
    const groups: { part: number; items: Utterance[] }[] = [];
    for (const item of timeline) {
      const last = groups[groups.length - 1];
      if (last && last.part === item.part) last.items.push(item);
      else groups.push({ part: item.part, items: [item] });
    }
    return groups;
  }, [timeline]);

  return (
    <section className="mt-8 pb-40">
      <h2 className="text-title-3-semibold text-text-primary">Транскрипт</h2>
      <p className="mt-1 text-body-2-regular text-text-tertiary">
        Нажмите на реплику, чтобы послушать.
      </p>
      {byPart.map((group) => (
        <div key={group.part} className="mt-4 flex flex-col gap-1">
          <p className="text-caption-1-medium uppercase tracking-[0.12em] text-text-tertiary">
            часть {String(group.part).padStart(2, "0")}
          </p>
          {group.items.map((u) => (
            <button key={`${u.part}-${u.idx}`} type="button" id={`u-${u.part}-${u.idx}`}
                    onClick={() => seek(u.part, u.start)}
                    className="w-full rounded-xl px-3 py-2 text-left hover:bg-background-secondary-default target:bg-background-secondary-default">
              <span className="nums mr-2 text-text-tertiary">{formatClock(u.start_abs)}</span>
              <span className="text-body-medium text-text-primary">{u.label}:</span>{" "}
              <span className="text-body-regular text-text-secondary">{u.text}</span>
            </button>
          ))}
        </div>
      ))}
    </section>
  );
}

function Body() {
  const params = useSearchParams();
  const deviceId = params.get("d") ?? "";
  const session = Number(params.get("n") ?? 0);
  const ready = Boolean(deviceId) && Number.isFinite(session) && session > 0;
  const { data, error, loading, reload } =
    useApi<Detail>(ready ? `/sessions/${encodeURIComponent(deviceId)}/${session}` : null);

  if (!ready) return <Empty text="Не указана сессия." />;
  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !data) return <Loading />;

  const minutes = data.parts.length;

  return (
    <PlayerProvider deviceId={data.device_id} session={data.session}>
      <PageTitle
        eyebrow={`СЕССИЯ ${String(data.session).padStart(4, "0")} · ${formatDay(data.day).toUpperCase()} · ЧАСТЕЙ ${minutes}`}
        plain="Разговор и его" italic="основания" />

      {data.summary ? (
        <>
          <p className="text-body-regular leading-6 text-text-secondary">{data.summary.summary}</p>
          {data.summary.topics.length > 0 && (
            <p className="mt-3 flex flex-wrap gap-2">
              {data.summary.topics.map((topic) => (
                <Link key={topic} href={`/project/?name=${encodeURIComponent(topic)}`}
                      className="rounded-full border border-separator-border px-3 py-1 text-caption-1-medium text-text-secondary hover:border-border-button-hover">
                  {topic}
                </Link>
              ))}
            </p>
          )}
          <SummaryList title="Решения" items={data.summary.decisions} />
          <SummaryList title="Факты" items={data.summary.facts} />
          <SummaryList title="Задачи" items={data.summary.tasks} />
          <SummaryList title="Вопросы без ответа" items={data.summary.open_questions} />
        </>
      ) : (
        <Empty text="Саммари ещё не готово." />
      )}

      {data.summary_error && (
        <p className="mt-4 rounded-2xl border border-separator-border p-4 text-body-2-regular text-text-tertiary">
          Последняя ошибка саммари: {data.summary_error}
        </p>
      )}

      {data.parts.filter((part) => part.transcript_status === "failed").map((part) => (
        <p key={part.part} className="mt-4 rounded-2xl border border-separator-border p-4 text-body-2-regular text-text-tertiary">
          Расшифровка части {String(part.part).padStart(2, "0")} не удалась: {part.last_error}
        </p>
      ))}

      <SpeakersForm deviceId={data.device_id} session={data.session}
                    speakers={data.speakers} onSaved={reload} />

      {data.timeline.length === 0
        ? <div className="mt-8"><Empty text="Расшифровки пока нет." /></div>
        : <Transcript timeline={data.timeline} />}
    </PlayerProvider>
  );
}

export default function SessionPage() {
  // Статический экспорт требует Suspense вокруг useSearchParams.
  return (
    <Suspense fallback={<Loading />}>
      <Body />
    </Suspense>
  );
}
