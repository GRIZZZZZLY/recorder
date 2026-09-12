"use client";

import { useState } from "react";
import { Button } from "@/components/base/buttons/button";
import { ListenLink, SourceDot } from "@/components/recorder/source-link";
import { apiPost, type Task } from "@/lib/api";
import { formatDay } from "@/lib/format";
import { cx } from "@/utils/cx";

const ACTIONS: { status: Task["status"]; title: string }[] = [
  { status: "done", title: "Закрыть" },
  { status: "dismissed", title: "Снять" },
  { status: "open", title: "Вернуть" },
];

function due(task: Task) {
  if (!task.due_said) return "срок не назван";
  if (!task.due_date) return `${task.due_said} · дату не разобрали`;
  return `${task.due_said} · ${formatDay(task.due_date)}`;
}

export function TaskRow({ task, onChanged }: { task: Task; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function change(status: Task["status"], title: string) {
    setBusy(true);
    try {
      await apiPost(`/tasks/${encodeURIComponent(task.device_id)}/${task.session}/${task.task_id}`,
                    { status });
      setError(null);
      onChanged();
    } catch (err) {
      setError(`не удалось ${title.toLowerCase()}: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  const chips = [
    task.mine ? "моя" : null,
    task.overdue ? "просрочено" : null,
    task.vanished ? "исчезла из саммари" : null,
    task.status === "done" ? "закрыта" : task.status === "dismissed" ? "снята" : null,
  ].filter(Boolean) as string[];

  return (
    <li className="rounded-2xl border border-separator-border bg-background-primary-default p-6">
      <p className="text-headline-medium text-text-primary">{task.text}</p>
      <p className="mt-2 text-body-2-regular text-text-tertiary">
        {task.owner || "исполнитель не назван"} ·{" "}
        <span className={cx("nums", task.overdue && "text-[var(--color-overdue)]")}>{due(task)}</span>{" "}
        · сессия <span className="nums">{String(task.session).padStart(4, "0")}</span>
      </p>
      {chips.length > 0 && (
        <p className="mt-1 text-caption-1-medium uppercase tracking-[0.12em] text-text-tertiary">
          {chips.join(" · ")}
        </p>
      )}
      <div className="mt-3 flex items-center gap-2">
        <SourceDot source={task.source} />
        <ListenLink source={task.source} deviceId={task.device_id} session={task.session} />
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        {ACTIONS.filter((action) => action.status !== task.status).map((action) => (
          <Button key={action.status} variant="ghost" size="small" disabled={busy}
                  onClick={() => change(action.status, action.title)}>
            {action.title}
          </Button>
        ))}
      </div>
      {error && (
        <p className="mt-2 text-body-2-regular text-[var(--color-overdue)]">{error}</p>
      )}
    </li>
  );
}
