"use client";

import { Suspense, useEffect, useMemo, useRef } from "react";
import { useSearchParams } from "next/navigation";
import { BookmarkSimple } from "@phosphor-icons/react/dist/ssr";
import { Card, Row, Section, SessionNo, Time } from "@/components/recorder/list";
import { PageHeader } from "@/components/recorder/page-header";
import { PlayerProvider, useSeek } from "@/components/recorder/player";
import { SpeakersForm } from "@/components/recorder/speakers-form";
import { SummaryList } from "@/components/recorder/summary-list";
import { TaskRow } from "@/components/recorder/task-row";
import { Empty, EmptyNote, Failed, Loading } from "@/components/recorder/states";
import { Dot, Evidence, SourceDot, Tag } from "@/components/recorder/ui";
import { useApi } from "@/lib/use-api";
import { at } from "@/lib/motion";
import { revealUtterance } from "@/lib/reveal";
import type { Mark, Question, Summary, Task, Utterance } from "@/lib/api";
import { count, formatClock, formatDay, formatTime } from "@/lib/format";
import { cx } from "@/utils/cx";

type Part = {
  part: number;
  recovered: number;
  transcript_status: string | null;
  last_error: string | null;
  /** Громкость части: считается сервером из файла при приёме. */
  envelope: number[];
};

type Detail = {
  device_id: string;
  session: number;
  day: string;
  first_at: string | null;
  parts: Part[];
  summary: Summary | null;
  summary_status: string | null;
  summary_error: string | null;
  timeline: Utterance[];
  marks: Mark[];
  speakers: { part: number; speaker: number; name: string; is_me: boolean }[];
  tasks: Task[];
  questions: Question[];
};

/** Полоска на каждую часть: расшифрована, в очереди или упала. Вырастают
 * слева направо по порядку номеров — части и приходили по очереди. */
function Parts({ parts }: { parts: Part[] }) {
  return (
    <Section note={`Части · ${parts.length}`} at={2}>
      <div className="flex gap-1.5 rounded-card bg-surface shadow-plate px-4 py-3.5">
        {parts.map((part, index) => (
          <span
            key={part.part}
            style={at(2, index)}
            title={`часть ${String(part.part).padStart(2, "0")}: ${part.transcript_status ?? "в очереди"}`}
            className={cx(
              "part-grow h-1 flex-1 rounded-sm",
              part.transcript_status === "done" ? "bg-fill"
                : part.transcript_status === "failed" ? "bg-accent"
                : "bg-waveform",
            )}
          />
        ))}
      </div>
    </Section>
  );
}

function Transcript({ timeline, marked }: { timeline: Utterance[]; marked: Set<string> }) {
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
    <Section note="Транскрипт" aside="нажмите реплику, чтобы послушать" at={7}>
      <div className="flex flex-col gap-4">
        {byPart.map((group) => (
          <div key={group.part} className="flex flex-col gap-1.5">
            <span className="px-1 font-mono text-micro text-text-tertiary">
              часть {String(group.part).padStart(2, "0")}
            </span>
            <Card className="py-1.5">
              {group.items.map((utterance) => {
                const id = `u-${utterance.part}-${utterance.idx}`;
                return (
                  <button
                    key={id}
                    id={id}
                    type="button"
                    onClick={() => seek(utterance.part, utterance.start,
                                        `${utterance.label} · ${formatClock(utterance.start_abs)}`)}
                    className={cx(
                      // scroll-my-24: якорь иначе прижимает реплику к самому
                      // верху окна, под шапку страницы.
                      "press-row grid w-full scroll-my-24 grid-cols-[76px_1fr] gap-x-3 px-4 py-2 text-left",
                      "hover:bg-surface-elevated sm:grid-cols-[76px_90px_1fr]",
                    )}
                  >
                    <Time className={marked.has(id) ? "flex items-center gap-1.5 text-text-primary" : undefined}>
                      {marked.has(id) && (
                        <BookmarkSimple size={12} weight="fill" aria-label="отмечено кнопкой" className="text-mark" />
                      )}
                      {formatClock(utterance.start_abs)}
                    </Time>
                    <span className="truncate text-note text-text-secondary">
                      {utterance.label}
                    </span>
                    <span className="col-span-2 text-row leading-relaxed sm:col-span-1">
                      {utterance.text}
                    </span>
                  </button>
                );
              })}
            </Card>
          </div>
        ))}
      </div>
    </Section>
  );
}

/** Содержимое страницы. Живёт внутри плеера: перемотка нужна всем секциям. */
function Content({ data, reload }: { data: Detail; reload: () => void }) {
  const seek = useSeek();

  // Ссылки на реплику-основание ведут на #u-{часть}-{индекс}: элемент с этим
  // id появляется в DOM только после загрузки данных, а браузер уже
  // попытался прокрутить к фрагменту при открытии страницы. Дублируем
  // прокрутку вручную один раз, когда данные готовы.
  //
  // ?t= — секунда сессии: так открывает лента дня, когда головка стоит внутри
  // записи. Точной перемотки по секунде нет, и выдумывать её незачем: ведём на
  // реплику, которая в этот момент звучала.
  const scrolled = useRef(false);
  const jump = useSearchParams().get("t");
  useEffect(() => {
    if (scrolled.current) return;
    scrolled.current = true;
    const parsed = /^u-(\d+)-(\d+)$/.exec(window.location.hash.slice(1));
    if (parsed) {
      revealUtterance(Number(parsed[1]), Number(parsed[2]));
      return;
    }
    const seconds = Number(jump);
    if (!jump || !Number.isFinite(seconds)) return;
    const before = data.timeline.filter((u) => u.start_abs <= seconds + 0.5);
    const target = before[before.length - 1] ?? data.timeline[0];
    if (!target) return;
    seek(target.part, target.start, `${target.label} · ${formatClock(target.start_abs)}`);
    revealUtterance(target.part, target.idx);
  }, [data.timeline, jump, seek]);

  // Реплики, на которых стоит метка кнопкой: флажок в транскрипте.
  const marked = new Set(
    data.marks.filter((mark) => mark.source)
      .map((mark) => `u-${mark.source!.part}-${mark.source!.idx}`),
  );

  // Длительность — из шкалы времени (последняя реплика: начало плюс её
  // длина), а не число загруженных частей. Пустая шкала — запасной вариант.
  const duration = data.timeline.length > 0
    ? Math.max(...data.timeline.map((u) => u.start_abs + (u.end - u.start)))
    : null;
  const tone = data.summary_status === "done" ? "ok"
    : data.summary_status === "failed" ? "accent" : "warn";
  const toneText = tone === "ok" ? "саммари готово"
    : tone === "accent" ? "саммари не собралось" : "обрабатывается";
  const topic = data.summary?.topics?.[0];
  const failedParts = data.parts.filter((part) => part.transcript_status === "failed");
  const openQuestions = data.questions.filter((question) => question.state === "open");
  const doneParts = data.parts.filter((part) => part.transcript_status === "done").length;

  return (
    <>
      <PageHeader
        back={{ href: "/records/", title: "Записи", note: <SessionNo session={data.session} /> }}
        title={topic || `Сессия ${String(data.session).padStart(4, "0")}`}
        aside={
          <span className="flex flex-wrap items-center gap-x-3.5 gap-y-1">
            <span>{formatDay(data.day)}{data.first_at ? `, ${formatTime(data.first_at)}` : ""}</span>
            {duration !== null ? <span className="font-mono">{formatClock(duration)}</span> : null}
            <span className="flex items-center gap-2"><Dot tone={tone} />{toneText}</span>
          </span>
        }
      />

      <div className="grid items-start gap-6 lg:grid-cols-[1fr_340px]">
        <div className="flex min-w-0 flex-col gap-6">
          {data.summary ? (
            <>
              <p className="enter text-[17px] leading-normal text-text-primary" style={at(1)}>{data.summary.summary}</p>
              {data.summary.topics.length > 0 && (
                <div className="enter flex flex-wrap gap-2" style={at(2)}>
                  {data.summary.topics.map((item) => (
                    <Tag key={item} href={`/project/?name=${encodeURIComponent(item)}`}>{item}</Tag>
                  ))}
                </div>
              )}
              <SummaryList title="Решения" items={data.summary.decisions} at={3} />
              <SummaryList title="Факты" items={data.summary.facts} at={4} />
            </>
          ) : (
            <Empty title="Саммари ещё не готово"
                   text="Как только части расшифруются, здесь появятся решения, факты и задачи." />
          )}

          {data.marks.length > 0 && (
            <Section note={`Метки · ${data.marks.length}`}
                     aside="кнопка на диктофоне во время записи" at={4}>
              <Card className="cascade">
                {data.marks.map((mark) => (
                  <Row key={`${mark.part}-${mark.offset}`} className="gap-3.5">
                    <BookmarkSimple size={14} weight="fill" aria-hidden className="shrink-0 text-mark" />
                    <Time className="shrink-0">{mark.time}</Time>
                    <span className="min-w-0 flex-1">
                      {mark.text || <span className="text-text-secondary">часть ещё не расшифрована</span>}
                      {mark.label ? (
                        <span className="block text-note text-text-secondary">{mark.label}</span>
                      ) : null}
                    </span>
                    <Evidence
                      disabled={!mark.source}
                      title={mark.source ? undefined : "реплики в этом месте нет"}
                      onClick={() => {
                        if (!mark.source) return;
                        seek(mark.source.part, mark.source.start, `метка · ${mark.time}`);
                        revealUtterance(mark.source.part, mark.source.idx);
                      }}
                    />
                  </Row>
                ))}
              </Card>
            </Section>
          )}

          <Section note={`Задачи · ${data.tasks.length}`} at={5}>
            {data.tasks.length === 0 ? <EmptyNote>Задач нет.</EmptyNote> : (
              <Card className="cascade">
                {data.tasks.map((task) => (
                  <TaskRow key={task.task_id} task={task} onChanged={reload} />
                ))}
              </Card>
            )}
          </Section>

          {openQuestions.length > 0 && (
            <Section note={`Вопросы без ответа · ${openQuestions.length}`} at={6}>
              <Card className="cascade">
                {openQuestions.map((question) => (
                  <Row key={question.question_id} className="gap-3.5">
                    <Time className="shrink-0">{question.time}</Time>
                    <span className="min-w-0 flex-1">{question.text}</span>
                    <SourceDot source={question.source} />
                    <Evidence
                      disabled={!question.source}
                      onClick={() => {
                        if (!question.source) return;
                        seek(question.source.part, question.source.start, question.text);
                        revealUtterance(question.source.part, question.source.idx);
                      }}
                    />
                  </Row>
                ))}
              </Card>
            </Section>
          )}

          {data.timeline.length === 0
            ? <EmptyNote>Расшифровки пока нет.</EmptyNote>
            : <Transcript timeline={data.timeline} marked={marked} />}
        </div>

        <div className="flex flex-col gap-6">
          <SpeakersForm key={JSON.stringify(data.speakers)} deviceId={data.device_id}
                        session={data.session} speakers={data.speakers} onSaved={reload} />
          <Parts parts={data.parts} />

          {(data.summary_error || failedParts.length > 0) && (
            <Section note="Ошибки обработки" at={3}>
              <Card>
                {data.summary_error ? (
                  <Row className="items-start">
                    <Dot tone="accent" />
                    <span className="min-w-0 flex-1">
                      Саммари
                      <span className="block font-mono text-micro text-text-secondary">
                        {data.summary_error}
                      </span>
                    </span>
                  </Row>
                ) : null}
                {failedParts.map((part) => (
                  <Row key={part.part} className="items-start">
                    <Dot tone="accent" />
                    <span className="min-w-0 flex-1">
                      Часть {String(part.part).padStart(2, "0")}
                      <span className="block font-mono text-micro text-text-secondary">
                        {part.last_error || "причина не записана"}
                      </span>
                    </span>
                  </Row>
                ))}
              </Card>
            </Section>
          )}

          <p className="px-1 text-note text-text-tertiary">
            {count(data.parts.length, "часть", "части", "частей")} · расшифровано {doneParts}
          </p>
        </div>
      </div>
    </>
  );
}

function Body() {
  const params = useSearchParams();
  const deviceId = params.get("d") ?? "";
  const session = Number(params.get("n") ?? 0);
  const ready = Boolean(deviceId) && Number.isFinite(session) && session > 0;
  const { data, error, loading, reload } =
    useApi<Detail>(ready ? `/sessions/${encodeURIComponent(deviceId)}/${session}` : null);

  if (!ready) return <Empty title="Сессия не указана" text="Откройте запись из списка." />;
  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !data) return <Loading />;

  return (
    <PlayerProvider deviceId={data.device_id} session={data.session} parts={data.parts.length}
                    marks={data.marks}
                    envelopes={Object.fromEntries(data.parts.map((p) => [p.part, p.envelope]))}>
      <Content data={data} reload={reload} />
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
