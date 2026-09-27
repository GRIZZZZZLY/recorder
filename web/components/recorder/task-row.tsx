"use client";

import { useState } from "react";
import { Row } from "@/components/recorder/list";
import { EvidenceLink, EvidenceTime, ROW_ACTION, ROW_ACTION_QUIET, SourceDot, SourceNote, SourceOf, TapeLine } from "@/components/recorder/ui";
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

  const time = task.source ? formatClock(task.source.start_abs) : "";
  // Раскладка одна на телефон и десктоп, сверху вниз: что сделать, где это на
  // записи, чьё и к какому сроку. Строка — место на плёнке, а не строчка списка дел.
  return (
    <Row className="items-start gap-3">
      {/* Точка источника: жёлтая — цитата не подтвердилась. */}
      <span className="pt-1.5"><SourceDot source={task.source} /></span>

      <div className="flex min-w-0 flex-1 flex-col gap-2.5">
        {/* Гашение текста — 150 мс: закрытая задача не мигает. */}
        <span className={cx("transition-colors duration-150", closed && "text-text-secondary")}>
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

        {/* Основание — время реплики; на другой странице оно ведёт на запись и
            стоит на плёнке этой записи, на странице самой записи — играет. */}
        <TapeLine tape={showSource ? task.tape : null} at={task.source?.start_abs}>
          {showSource ? (
            <EvidenceLink time={time} context={task.text}
                          href={task.source ? sessionHref(task.device_id, task.session, task.source.part, task.source.idx) : null} />
          ) : (
            <EvidenceTime time={time} label={task.text} context={task.text} source={task.source} />
          )}
        </TapeLine>

        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-note">
          {task.owner ? <span className="text-text-secondary">{task.owner}</span> : null}
          <span className={task.overdue && !closed ? "text-accent-text" : "text-text-secondary"}>{due(task)}</span>
          {/* Открытая — «Готово» и «Убрать»; закрытая — только «Вернуть», на месте
              «Готово»: второе нажатие туда же отменяет, а не убирает. */}
          <span className="ml-auto flex shrink-0 gap-2">
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
        </div>
      </div>
    </Row>
  );
}
