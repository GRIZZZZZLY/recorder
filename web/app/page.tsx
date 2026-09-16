"use client";

import Link from "next/link";
import { Card, RowLink, Section } from "@/components/recorder/list";
import { PageHeader } from "@/components/recorder/page-header";
import { Stats, StatusPill } from "@/components/recorder/stats";
import { Empty, EmptyNote, Failed, Loading } from "@/components/recorder/states";
import { DayTimeline } from "@/components/recorder/timeline";
import { Dot, SourceDot } from "@/components/recorder/ui";
import { useApi } from "@/lib/use-api";
import type { DigestBody, SessionRow, Task } from "@/lib/api";
import {
  count, formatAgo, formatDayLong, formatDayShort, formatDuration, formatMinutes,
  formatTime, todayIso,
} from "@/lib/format";
import { at } from "@/lib/motion";
import { sessionHref } from "@/lib/session-link";
import { statusTone } from "@/lib/session-status";
import { isLive, useNow } from "@/lib/use-now";
import { cx } from "@/utils/cx";

type Status = { transcripts_pending: number; summaries_pending: number };

function due(task: Task) {
  if (!task.due_date) return task.due_said || "без срока";
  return `${task.due_said || "срок"} · ${formatDayShort(task.due_date)}`;
}

export default function DigestPage() {
  const { data, error, loading, reload } = useApi<DigestBody>("/digest");
  const { data: status } = useApi<Status>("/status");
  // Ленте дня нужны все сессии, а не пять последних из сводки: по ней ходят
  // стрелками в прошлые дни.
  const { data: sessions } = useApi<SessionRow[]>("/sessions");
  const now = useNow();

  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !data) return <Loading />;

  const today = todayIso();
  const lastAt = data.recent[0]?.last_at ?? null;
  const live = isLive(lastAt, now);
  const queue = status?.transcripts_pending ?? 0;
  const waiting = status?.summaries_pending ?? 0;

  // Хореография: шапка на нулевом такте, плашки 1–3, лента 5, числа 7, блок
  // списков 8, строки в них — с девятого. Такт 40 мс: страница собирается
  // сверху вниз за ~700 мс, первый текст виден сразу.
  return (
    <>
      <PageHeader title="Сводка" aside={formatDayLong(today)} />

      <div className="flex flex-wrap gap-3">
        <StatusPill
          at={1}
          tone={live ? "accent" : lastAt ? "ok" : "missing"}
          live={live}
          title="Диктофон"
          value={live ? "пишет · выгрузка идёт" : `последняя выгрузка ${formatAgo(lastAt, now)}`}
        />
        <StatusPill at={2} tone={queue ? "warn" : "ok"} title="Расшифровка"
                    value={queue ? `${count(queue, "часть", "части", "частей")} в очереди` : "очередь пуста"} />
        <StatusPill at={3} tone={waiting ? "warn" : "ok"} title="Саммари"
                    value={waiting ? `${waiting} ждёт` : "все собраны"} />
      </div>

      {/* Лента живёт своей жизнью: по ней ходят в прошлые дни, даже когда
          сегодня записей не было. Пусто она выглядит только пока их нет вовсе. */}
      {sessions && sessions.length > 0 ? (
        <DayTimeline rows={sessions} now={now} at={5} />
      ) : (
        <div className="enter" style={at(5)}>
          <Empty
            title="Записей ещё нет"
            text={lastAt
              ? `Последняя выгрузка была ${formatAgo(lastAt, now)}. Проверьте заряд диктофона или посмотрите, что с обработкой.`
              : "Диктофон ещё не присылал записи."}
            action={<Link href="/status/"
                          className="press rounded-inner bg-hairline px-3.5 py-2 text-note hover:bg-hairline-strong">
              Открыть состояние
            </Link>}
          />
        </div>
      )}

      {/* Четыре нуля — не сведения, а шум: пока за день ничего не записано,
          лента уже сказала «записей нет», и числам добавить нечего. */}
      {data.day.sessions > 0 && (
        <div className="enter" style={at(7)}>
          <Stats items={[
            { value: formatMinutes(data.day.minutes), label: "записано сегодня" },
            { value: data.day.sessions, label: "разговоров" },
            { value: data.day.new_tasks, label: "новые задачи" },
            { value: data.day.review, label: "спорных мест", tone: data.day.review ? "accent" : undefined },
          ]} />
        </div>
      )}

      <div className="grid gap-5 lg:grid-cols-[1.2fr_1fr]">
        <Section
          at={8}
          head="Мои открытые задачи"
          aside={data.week.my_open_tasks > 0 ? (
            <Link href="/tasks/?show=mine" className="hover:text-text-primary">
              все {data.week.my_open_tasks}
            </Link>
          ) : undefined}
        >
          {!data.has_me ? (
            <EmptyNote>
              Ни один говорящий не отмечен как «это я» — отметьте себя на странице сессии.
            </EmptyNote>
          ) : data.my_tasks.length === 0 ? (
            <EmptyNote>Открытых задач нет.</EmptyNote>
          ) : (
            <Card className="cascade" style={at(9)}>
              {data.my_tasks.slice(0, 5).map((task) => (
                <RowLink key={`${task.session}-${task.task_id}`}
                         href={sessionHref(task.device_id, task.session,
                                           task.source?.part, task.source?.idx)}>
                  <SourceDot source={task.source} />
                  <span className="min-w-0 flex-1 truncate">{task.text}</span>
                  {task.owner ? <span className="hidden text-text-tertiary sm:block">{task.owner}</span> : null}
                  <span className={cx("shrink-0 text-right text-note",
                                      task.overdue ? "text-accent-text" : "text-text-secondary")}>
                    {due(task)}
                  </span>
                </RowLink>
              ))}
            </Card>
          )}
        </Section>

        <Section
          at={8}
          head="Последние записи"
          aside={<Link href="/records/" className="hover:text-text-primary">все записи</Link>}
        >
          {data.recent.length === 0 ? (
            <EmptyNote>Записей ещё не было.</EmptyNote>
          ) : (
            <Card className="cascade" style={at(9)}>
              {data.recent.map((row) => (
                <RowLink key={`${row.device_id}-${row.session}`}
                         href={sessionHref(row.device_id, row.session)}>
                  {/* Разговор узнают по часу и теме. Номер сессии — внутренность
                      системы, ему место только на странице «Состояние». */}
                  <span className="min-w-0 flex-1 truncate">
                    <span className="font-mono text-text-secondary">{formatTime(row.first_at)}</span>
                    {row.topic ? <> · {row.topic}</> : null}
                  </span>
                  <span className="shrink-0 text-note text-text-secondary">
                    {formatDuration(row.seconds)}
                  </span>
                  <Dot tone={statusTone(row)} />
                </RowLink>
              ))}
            </Card>
          )}
        </Section>
      </div>
    </>
  );
}
