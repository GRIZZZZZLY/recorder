"use client";

import Link from "next/link";
import {
  ArrowRight, CheckCircle, Clock, Waveform as WaveIcon, WarningCircle,
} from "@phosphor-icons/react/dist/ssr";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { StudioRecord } from "@/components/recorder/studio-record";
import { DayTimeline } from "@/components/recorder/timeline";
import { useApi } from "@/lib/use-api";
import type { DigestBody, SessionRow, Task } from "@/lib/api";
import { count, formatAgo, formatDayLong, formatDayShort, formatMinutes, todayIso } from "@/lib/format";
import { sessionHref } from "@/lib/session-link";
import { useNow } from "@/lib/use-now";
import { cx } from "@/utils/cx";

type Status = { transcripts_pending: number; summaries_pending: number };

function due(task: Task) {
  if (!task.due_date) return task.due_said || "без срока";
  return `${task.due_said || "срок"} · ${formatDayShort(task.due_date)}`;
}

/**
 * Сводка — лицевая панель прибора: лента дня в дисплее, под ней счётчики дня
 * и лампочки обработки; ниже — последние записи карточками, мои задачи и
 * спорные места. Состояние диктофона словами живёт в шильдике над страницей
 * (Shell) и на странице «Состояние».
 */
export default function DigestPage() {
  const { data, error, loading, reload } = useApi<DigestBody>("/digest");
  const { data: status } = useApi<Status>("/status");
  // Ленте дня нужны все сессии, а не пять последних из сводки: по ней ходят
  // стрелками в прошлые дни.
  const { data: sessions } = useApi<SessionRow[]>("/sessions");
  const now = useNow();

  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !data) return <Loading />;

  const lastAt = data.recent[0]?.last_at ?? null;
  const queue = status?.transcripts_pending ?? 0;
  const waiting = status?.summaries_pending ?? 0;

  // Хореография: шапка на нулевом такте, прибор на первом, лента внутри него
  // на втором, дальше секции по порядку. Такт 40 мс.
  return (
    <div className="studio-home">
      <header className="studio-home-heading">
        <div>
          <span className="studio-eyebrow">ВАШ ЗВУКОВОЙ ДНЕВНИК</span>
          <h1>Сводка<span className="studio-title-dot">.</span></h1>
        </div>
        <span className="studio-date">{formatDayLong(todayIso())}</span>
      </header>

      <section className="studio-instrument" aria-label="Лента дня">
        <div className="studio-instrument-label">
          <span><WaveIcon size={17} aria-hidden /> Лента дня</span>
          <span className="studio-engraved" aria-hidden>24 H / AUDIO LOG</span>
        </div>

        {/* Лента живёт своей жизнью: по ней ходят в прошлые дни, даже когда
            сегодня записей не было. Пусто она выглядит только пока их нет вовсе. */}
        {sessions && sessions.length > 0 ? (
          <DayTimeline rows={sessions} now={now} />
        ) : (
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
        )}

        {/* Три нуля — не сведения, а шум: пока за день ничего не записано,
            лента уже сказала «записей нет», и счётчикам добавить нечего. */}
        {data.day.sessions > 0 && (
          <div className="studio-meter-stats">
            <div>
              <span className="studio-meter-label"><Clock size={13} aria-hidden /> Записано сегодня</span>
              <strong>{formatMinutes(data.day.minutes)}</strong>
            </div>
            <div>
              <span className="studio-meter-label"><WaveIcon size={13} aria-hidden /> Разговоров</span>
              <strong>{String(data.day.sessions).padStart(2, "0")}</strong>
            </div>
            <div>
              <span className="studio-meter-label"><CheckCircle size={13} aria-hidden /> Новых задач</span>
              <strong>{String(data.day.new_tasks).padStart(2, "0")}</strong>
            </div>
          </div>
        )}

        <div className="studio-processing">
          <span className={cx(queue && "pending")}>
            <i aria-hidden />Расшифровка · {queue ? `${count(queue, "часть", "части", "частей")} в очереди` : "готово"}
          </span>
          <span className={cx(waiting && "pending")}>
            <i aria-hidden />Саммари · {waiting ? count(waiting, "ждёт", "ждут", "ждут") : "готово"}
          </span>
          <Link href="/status/" aria-label="Состояние обработки"><ArrowRight size={16} aria-hidden /></Link>
        </div>
      </section>

      <div className="studio-section-heading">
        <h2>Последние записи</h2>
        <Link href="/records/">Все записи <ArrowRight size={16} aria-hidden /></Link>
      </div>
      {data.recent.length > 0 ? (
        <div className="studio-record-grid">
          {data.recent.slice(0, 4).map((row) => (
            <StudioRecord key={`${row.device_id}-${row.session}`} row={row} compact />
          ))}
        </div>
      ) : (
        <p className="studio-empty">Здесь появится ваш первый разговор.</p>
      )}

      <section className="studio-tasks-panel" aria-label="Мои задачи">
        <div className="studio-section-heading">
          <h2>Мои задачи{data.week.my_open_tasks > 0 && <span>{data.week.my_open_tasks}</span>}</h2>
          <Link href="/tasks/?show=mine" aria-label="Все мои задачи"><ArrowRight size={18} aria-hidden /></Link>
        </div>
        {!data.has_me ? (
          <p>Ни один говорящий не отмечен как «это я» — отметьте себя на странице записи, и здесь появятся ваши задачи.</p>
        ) : data.my_tasks.length === 0 ? (
          <p>Открытых задач нет.</p>
        ) : (
          data.my_tasks.slice(0, 5).map((task) => (
            <Link key={`${task.session}-${task.task_id}`} className="studio-task-link"
                  href={sessionHref(task.device_id, task.session, task.source?.part, task.source?.idx)}>
              <span className="studio-check-square" aria-hidden />
              <span className="studio-task-text">{task.text}</span>
              <span className={cx("studio-task-due", task.overdue && "overdue")}>{due(task)}</span>
              <ArrowRight size={15} aria-hidden />
            </Link>
          ))
        )}
      </section>

      {data.day.review > 0 && (
        <Link href="/review/" className="studio-review-note">
          <WarningCircle size={17} aria-hidden /> Проверить спорные места
          <span>{data.day.review}</span>
          <ArrowRight size={15} aria-hidden />
        </Link>
      )}
    </div>
  );
}
