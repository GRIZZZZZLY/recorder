"use client";

import { useState } from "react";
import Link from "next/link";
import { CaretRight } from "@phosphor-icons/react/dist/ssr";
import { Row } from "@/components/recorder/list";
import { SourceDot, SourceNote } from "@/components/recorder/ui";
import { apiPost, type Task } from "@/lib/api";
import { formatDayShort } from "@/lib/format";
import { sessionHref } from "@/lib/session-link";
import { cx } from "@/utils/cx";

const ACTIONS: { status: Task["status"]; title: string }[] = [
  { status: "done", title: "Закрыть" },
  { status: "dismissed", title: "Снять" },
  { status: "open", title: "Вернуть" },
];

const MARKS: Record<Task["status"], string> = {
  open: "", done: "закрыта", dismissed: "снята",
};

function due(task: Task) {
  if (!task.due_date) return task.due_said || "без срока";
  return `${task.due_said || "срок"} · ${formatDayShort(task.due_date)}`;
}

/**
 * Строка задачи с действиями.
 *
 * Закрытая задача остаётся на месте: текст гаснет, появляется отметка. Строка
 * не схлопывается и не уезжает — владелец должен видеть, что произошло, а не
 * догадываться по исчезновению.
 */
export function TaskRow({ task, onChanged }: { task: Task; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const closed = task.status !== "open";
  // Отметка появляется с анимацией только после действия владельца. На
  // загрузке страницы уже закрытые задачи не должны мигать чипами: там
  // работает каскад строки.
  const [acted, setActed] = useState(false);

  async function change(status: Task["status"], title: string) {
    setActed(true);
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

  return (
    <Row className="flex-wrap gap-y-2">
      {/* Просроченная задача: точка источника загорается при входе строки. */}
      <SourceDot source={task.source} />

      {/* Гашение текста — 150 мс, как в макете: закрытая задача не мигает.
          На телефоне текст занимает всю строку (ширина минус точка и зазор),
          срок и действия уходят второй строкой: иначе на текст оставалось
          79 px и слово на строку. */}
      <span className={cx("min-w-0 flex-1 transition-colors duration-150 max-md:basis-[calc(100%-1.25rem)]",
                          closed && "text-text-secondary")}>
        {task.text}
        {MARKS[task.status] ? (
          <span className={cx("ml-2.5 inline-block rounded-chip border border-hairline-strong px-2 py-0.5 text-micro text-text-secondary",
                              acted && "chip-in")}>
            {MARKS[task.status]}
          </span>
        ) : null}
        {task.vanished ? (
          <span className="ml-2.5 inline-block rounded-chip border border-hairline-strong px-2 py-0.5 text-micro text-text-secondary">
            исчезла из саммари
          </span>
        ) : null}
        <SourceNote source={task.source} />
        {error ? <span className="rise-in block text-note text-accent-text">{error}</span> : null}
      </span>

      {task.owner ? (
        <span className="hidden shrink-0 text-text-secondary md:block">{task.owner}</span>
      ) : null}

      {/* Вторая строка на телефоне начинается под текстом, а не под точкой. */}
      <span className={cx("shrink-0 text-note max-md:ml-5",
                          task.overdue ? "text-accent-text" : "text-text-secondary")}>
        {due(task)}
      </span>

      {/* Переход к записи — стрелкой, не номером сессии: номер владельцу ничего
          не говорит, а «куда ведёт» стрелка говорит сама. На телефоне стрелка
          встаёт в конец второй строки: другого пути от задачи к реплике там нет. */}
      <Link
        href={sessionHref(task.device_id, task.session, task.source?.part, task.source?.idx)}
        className="press hit relative block shrink-0 text-text-tertiary hover:text-text-primary max-md:order-last"
        title="Открыть запись на этой реплике"
        aria-label="Открыть запись на этой реплике"
      >
        <CaretRight size={14} aria-hidden />
      </Link>

      <span className="flex shrink-0 gap-3 text-note text-text-secondary max-md:ml-auto">
        {ACTIONS.filter((action) => action.status !== task.status).map((action) => (
          <button
            key={action.status}
            type="button"
            disabled={busy}
            onClick={() => change(action.status, action.title)}
            className="press hit relative rounded-sm hover:text-text-primary disabled:opacity-40"
          >
            {action.title}
          </button>
        ))}
      </span>
    </Row>
  );
}
