"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Filters } from "@/components/recorder/filters";
import { PageTitle } from "@/components/recorder/eyebrow";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { TaskRow } from "@/components/recorder/task-row";
import { useApi } from "@/lib/use-api";
import type { Task } from "@/lib/api";

const FILTERS = [
  { key: "open", title: "Открытые" },
  { key: "mine", title: "Мои" },
  { key: "overdue", title: "Просроченные" },
  { key: "all", title: "Все" },
];

function Body() {
  const params = useSearchParams();
  const session = params.get("session");
  const [show, setShow] = useState(params.get("show") ?? "open");
  const query = `/tasks?show=${show}${session ? `&session=${session}` : ""}`;
  const { data, error, loading, reload } = useApi<{ tasks: Task[]; has_me: boolean }>(query);

  return (
    <>
      <PageTitle eyebrow="ДИКТОФОН" plain="Что надо" italic="сделать" />
      <Filters value={show} options={FILTERS} onChange={setShow} />

      {session && (
        <p className="mt-3 text-body-2-regular text-text-tertiary">
          Только сессия {String(Number(session)).padStart(4, "0")}.
        </p>
      )}

      <div className="mt-6">
        {error ? <Failed error={error} onRetry={reload} />
          : loading || !data ? <Loading />
          : show === "mine" && !data.has_me ? (
            <p className="rounded-2xl border border-separator-border p-6 text-body-regular text-text-secondary">
              Ни один говорящий не отмечен как «это я»: отметьте себя на странице сессии,
              иначе «Мои» ничего не найдут.
            </p>
          ) : data.tasks.length === 0 ? <Empty text="Ничего нет." />
          : (
            <ul className="flex flex-col gap-3">
              {data.tasks.map((task) => (
                <TaskRow key={`${task.session}-${task.task_id}`} task={task} onChanged={reload} />
              ))}
            </ul>
          )}
      </div>
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
