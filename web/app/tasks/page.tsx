"use client";

import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Card, Section } from "@/components/recorder/list";
import { PageHeader } from "@/components/recorder/page-header";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { TaskRow } from "@/components/recorder/task-row";
import { Segmented } from "@/components/recorder/ui";
import { useApi } from "@/lib/use-api";
import type { DigestBody, Task } from "@/lib/api";
import { todayIso } from "@/lib/format";
import { cx } from "@/utils/cx";

type Show = "open" | "mine" | "overdue" | "all";

/**
 * Задачи по срочности, а не одной кучей: просроченное сверху, «когда-нибудь»
 * внизу. Внутри группы порядок тот, что пришёл с сервера.
 */
function group(tasks: Task[], today: string) {
  const week = new Date(today);
  week.setDate(week.getDate() + 7);
  const weekIso = week.toISOString().slice(0, 10);

  const buckets: { title: string; rows: Task[] }[] = [
    { title: "Просрочено", rows: [] },
    { title: "На этой неделе", rows: [] },
    { title: "Дальше", rows: [] },
    { title: "Без срока", rows: [] },
    { title: "Закрытые", rows: [] },
  ];

  for (const task of tasks) {
    if (task.status !== "open") buckets[4].rows.push(task);
    else if (task.overdue) buckets[0].rows.push(task);
    else if (!task.due_date) buckets[3].rows.push(task);
    else if (task.due_date <= weekIso) buckets[1].rows.push(task);
    else buckets[2].rows.push(task);
  }
  return buckets.filter((bucket) => bucket.rows.length > 0);
}

function Body() {
  const router = useRouter();
  const params = useSearchParams();
  // Единственный источник правды — адрес: state не держим, иначе фильтр не
  // попадёт в ссылку и историю. push, не replace: «назад» должно возвращать
  // предыдущий фильтр, а не уводить со страницы.
  const rawSession = params.get("session");
  const session = rawSession !== null && Number.isFinite(Number(rawSession)) ? rawSession : null;
  const show = (params.get("show") ?? "open") as Show;

  const query = `/tasks?show=${show}${session ? `&session=${session}` : ""}`;
  const { data, error, loading, reload } = useApi<{ tasks: Task[]; has_me: boolean }>(query);
  const { data: digest } = useApi<DigestBody>("/digest");

  function setShow(key: Show) {
    router.push(`/tasks/?show=${key}${session ? `&session=${session}` : ""}`);
  }

  const filters = [
    { key: "open" as const, title: "Открытые", count: digest?.week.open_tasks },
    { key: "mine" as const, title: "Мои", count: digest?.week.my_open_tasks },
    { key: "overdue" as const, title: "Просроченные", count: digest?.week.overdue_tasks, alarm: true },
    { key: "all" as const, title: "Все" },
  ];

  return (
    <>
      <PageHeader
        title="Задачи"
        aside={<Segmented label="Фильтр задач" value={show} options={filters} onChange={setShow} />}
      />

      {session !== null && (
        <p className="px-1 text-note text-text-secondary">
          Только сессия <span className="font-mono">{String(Number(session)).padStart(4, "0")}</span>.
        </p>
      )}

      {/* Пока перезапрашивается — прежний список гаснет, а не пропадает в
          скелетон: новые строки въедут каскадом на его место. */}
      {error ? <Failed error={error} onRetry={reload} />
        : !data ? <Loading />
        : show === "mine" && !data.has_me ? (
          <Empty
            title="Не отмечено, кто из говорящих — вы"
            text="Откройте любую запись и включите «это я» напротив своего имени: тогда «Мои» начнут работать."
          />
        ) : data.tasks.length === 0 ? (
          <Empty title="Задач нет"
                 text={show === "overdue" ? "Ничего не просрочено." : "В этом фильтре пусто."} />
        ) : (
          group(data.tasks, todayIso()).map((bucket, index) => (
            <Section key={bucket.title} note={`${bucket.title} · ${bucket.rows.length}`}
                     at={index + 1} className={cx(loading && "stale")}>
              <Card className="cascade">
                {bucket.rows.map((task) => (
                  <TaskRow key={`${task.session}-${task.task_id}`} task={task} onChanged={reload} />
                ))}
              </Card>
            </Section>
          ))
        )}
    </>
  );
}

export default function TasksPage() {
  return (
    <Suspense fallback={<Loading />}>
      <Body />
    </Suspense>
  );
}
