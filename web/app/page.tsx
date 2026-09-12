"use client";

import Link from "next/link";
import { PageTitle } from "@/components/recorder/eyebrow";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { TaskRow } from "@/components/recorder/task-row";
import { Tile } from "@/components/recorder/tile";
import { useApi } from "@/lib/use-api";
import type { DigestBody } from "@/lib/api";
import { formatDay, formatMinutes, plural } from "@/lib/format";

/** Заголовок собирается из чисел дня; курсивом идёт последний член перечисления. */
function headline(body: DigestBody): { plain: string; italic: string } {
  const pieces: string[] = [];
  if (body.day.sessions) {
    pieces.push(`${body.day.sessions} ${plural(body.day.sessions, "разговор", "разговора", "разговоров")}`);
  }
  if (body.day.new_tasks) {
    pieces.push(`${body.day.new_tasks} ${plural(body.day.new_tasks, "задача", "задачи", "задач")}`);
  }
  const italic = body.week.review
    ? `${body.week.review} ${plural(body.week.review, "спорное место", "спорных места", "спорных мест")}`
    : "ничего спорного";
  return { plain: pieces.length ? `Сегодня: ${pieces.join(", ")}, и` : "Сегодня тихо, а в работе", italic };
}

export default function DigestPage() {
  const { data, error, loading, reload } = useApi<DigestBody>("/digest");

  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !data) return <Loading />;

  const today = new Date().toISOString().slice(0, 10);
  const title = headline(data);

  return (
    <>
      <PageTitle eyebrow={`ДИКТОФОН · ${formatDay(today).toUpperCase()}`}
                 plain={title.plain} italic={title.italic} />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Tile label="Записано минут" value={formatMinutes(data.day.minutes)}
              hint={`за неделю ${formatMinutes(data.week.minutes)}`} />
        <Tile label="Разговоров" value={data.day.sessions} hint={`за неделю ${data.week.sessions}`} />
        <Tile label="Новых задач" value={data.day.new_tasks} hint={`за неделю ${data.week.new_tasks}`} />
        <Tile label="Вопросов без ответа" value={data.week.open_questions}
              hint={`просрочено задач: ${data.week.overdue_tasks}`} />
      </div>

      <section className="mt-12">
        <h2 className="text-title-3-semibold text-text-primary">Мои открытые задачи</h2>
        {!data.has_me ? (
          <p className="mt-3 rounded-2xl border border-separator-border p-6 text-body-regular text-text-secondary">
            Ни один говорящий не отмечен как «это я». Отметьте себя на странице сессии —
            тогда здесь появятся ваши задачи.
          </p>
        ) : data.my_tasks.length === 0 ? (
          <div className="mt-3"><Empty text="Открытых задач нет." /></div>
        ) : (
          <ul className="mt-3 flex flex-col gap-3">
            {data.my_tasks.map((task) => (
              <TaskRow key={task.task_id} task={task} onChanged={reload} />
            ))}
          </ul>
        )}
      </section>

      <section className="mt-12">
        <h2 className="text-title-3-semibold text-text-primary">Последние записи</h2>
        <ul className="mt-3 flex flex-col gap-3">
          {data.recent.map((row) => (
            <li key={`${row.device_id}-${row.session}`}>
              <Link href={`/session/?d=${encodeURIComponent(row.device_id)}&n=${row.session}`}
                    className="block rounded-2xl border border-separator-border bg-background-primary-default p-6 hover:bg-background-primary-hover">
                <p className="text-headline-medium text-text-primary">
                  Сессия {String(row.session).padStart(4, "0")}
                </p>
                <p className="mt-1 text-body-2-regular text-text-tertiary nums">
                  {formatDay(row.day)} · {formatMinutes(row.seconds / 60)} мин · частей {row.parts},
                  расшифровано {row.done_parts} ·{" "}
                  {row.summary_status === "done" ? "саммари готово" : "обрабатывается"}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
