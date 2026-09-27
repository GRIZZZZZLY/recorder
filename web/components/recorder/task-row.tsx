"use client";

import { useState } from "react";
import { Row } from "@/components/recorder/list";
import { EvidenceLink, EvidenceTime, ROW_ACTION, ROW_ACTION_QUIET, SourceDot, SourceNote, SourceOf } from "@/components/recorder/ui";
import { formatClock } from "@/lib/format";
import { apiPost, type Task } from "@/lib/api";
import { formatDayShort } from "@/lib/format";
import { sessionHref } from "@/lib/session-link";
import { cx } from "@/utils/cx";

const ACTIONS: { status: Task["status"]; title: string }[] = [
  // «Снять» читалось двояко (снять с себя? отменить?): «Убрать» — не задача.
  { status: "done", title: "Готово" },
  { status: "dismissed", title: "Убрать" },
  { status: "open", title: "Вернуть" },
];

const MARKS: Record<Task["status"], string> = {
  open: "", done: "готово", dismissed: "убрана",
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
export function TaskRow({ task, onChanged, showSource = true }: {
  task: Task;
  /** Новое состояние: список держит строку на месте с отметкой и «Вернуть». */
  onChanged: (status: Task["status"]) => void;
  /** Откуда задача. На странице самой записи это и так видно. */
  showSource?: boolean;
}) {
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
      onChanged(status);
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
      {/* На телефоне время-основание — в первой строке справа от текста
          (ширина минус точка, кнопка и зазоры), остальное — ниже. */}
      <span className={cx("min-w-0 flex-1 transition-colors duration-150 max-md:basis-[calc(100%-7rem)]",
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
        {showSource ? <SourceOf topic={task.topic} day={task.day} /> : null}
        <SourceNote source={task.source} />
        {error ? <span className="rise-in block text-note text-accent-text">{error}</span> : null}
      </span>

      {/* Вторая строка на телефоне начинается под текстом, а не под точкой;
          исполнитель на телефоне тоже виден — чья задача, главный вопрос к ней. */}
      {task.owner ? (
        <span className="shrink-0 text-text-secondary max-md:order-2 max-md:ml-5 max-md:text-note">{task.owner}</span>
      ) : null}

      <span className={cx("shrink-0 text-note max-md:order-2", !task.owner && "max-md:ml-5",
                          task.overdue && !closed ? "text-accent-text" : "text-text-secondary")}>
        {due(task)}
      </span>

      {/* Основание задачи — время реплики, как у всех пунктов саммари. На
          странице записи оно играет, на других — ведёт на реплику. На телефоне
          — в первой строке справа от текста: другого пути к записи там нет. */}
      <span className="shrink-0 max-md:order-1">
        {showSource ? (
          <EvidenceLink time={task.source ? formatClock(task.source.start_abs) : ""} context={task.text}
                        href={task.source ? sessionHref(task.device_id, task.session, task.source.part, task.source.idx) : null} />
        ) : (
          <EvidenceTime time={task.source ? formatClock(task.source.start_abs) : ""} label={task.text}
                        context={task.text} source={task.source} />
        )}
      </span>

      <span className="flex shrink-0 gap-2 max-md:order-2 max-md:ml-auto">
        {/* Открытая — «Готово» и «Убрать»; закрытая — только «Вернуть», на месте
            «Готово»: второе нажатие туда же отменяет, а не убирает. */}
        {ACTIONS.filter((action) => task.status === "open" ? action.status !== "open" : action.status === "open").map((action) => (
          <button
            key={action.status}
            type="button"
            disabled={busy}
            onClick={() => change(action.status, action.title)}
            className={action.status === "dismissed" ? ROW_ACTION_QUIET : ROW_ACTION}
          >
            {action.title}
          </button>
        ))}
      </span>
    </Row>
  );
}
