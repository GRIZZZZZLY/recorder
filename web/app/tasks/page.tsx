"use client";

import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
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
  const router = useRouter();
  const params = useSearchParams();
  // Единственный источник правды — адрес: state не держим, иначе фильтр не
  // попадёт в ссылку/историю (был баг). Невалидный session (не число) не
  // показываем и не передаём в API — не сужаем список мусором из адреса.
  const rawSession = params.get("session");
  const session = rawSession !== null && Number.isFinite(Number(rawSession)) ? rawSession : null;
  const show = params.get("show") ?? "open";
  const query = `/tasks?show=${show}${session ? `&session=${session}` : ""}`;
  const { data, error, loading, reload } = useApi<{ tasks: Task[]; has_me: boolean }>(query);

  function setShow(key: string) {
    // push, не replace: с replace каждый клик переписывает единственную запись
    // истории, и «назад» после пары переключений всегда уводит на страницу,
    // с которой пришли на /tasks — прежний выбранный фильтр восстановить
    // нельзя. Так же вело себя старое useState-состояние (адрес вообще не
    // менялся) — итог был бы тот же самый неисправленный баг. push кладёт
    // отдельную запись на каждое переключение, «назад» возвращает предыдущий
    // фильтр — как было со старыми обычными ссылками `?show=`.
    router.push(`/tasks/?show=${key}${session ? `&session=${session}` : ""}`);
  }

  return (
    <>
      <PageTitle eyebrow="ДИКТОФОН" plain="Что надо" italic="сделать" />
      <Filters value={show} options={FILTERS} onChange={setShow} />

      {session !== null && (
        <p className="mt-3 text-body-2-regular text-text-tertiary">
          Только сессия <span className="nums">{String(Number(session)).padStart(4, "0")}</span>.
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
