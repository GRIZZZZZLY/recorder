"use client";

import Link from "next/link";
import {
  ArrowRight, CheckCircle, Clock, Waveform as WaveIcon,
} from "@phosphor-icons/react/dist/ssr";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { StudioRecord } from "@/components/recorder/studio-record";
import { DayTimeline } from "@/components/recorder/timeline";
import { useApi } from "@/lib/use-api";
import type { DigestBody, SessionRow, Task } from "@/lib/api";
import {
  count, formatAgo, formatDayLong, formatDayShort, formatDuration, formatMinutes, todayIso,
} from "@/lib/format";
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
  // Что ждёт владельца: просроченное у всех (не только у него), спорные места,
  // вопросы без ответа, сломанное. Горит только ненулевое — «командный центр»
  // говорит о долгах, а не отчитывается, что всё в порядке.
  const counts = data?.week;
  const attention = counts ? [
    { href: "/tasks/?show=overdue", label: "Просрочено", count: counts.overdue_tasks, tone: "failed" },
    { href: "/review/", label: "Спорные", count: counts.review, tone: "pending" },
    { href: "/questions/", label: "Вопросы", count: counts.open_questions, tone: "pending" },
    { href: "/status/", label: "Ошибки", count: counts.failed_parts + counts.failed_summaries, tone: "failed" },
  ].filter((item) => item.count > 0) : [];

  // Показания прибора. В день, когда что-то записано, это счётчики дня. В день
  // без записей нулей не будет: шкала переключается на архив — сколько звука
  // лежит всего и когда был последний разговор. Считается из того же списка
  // сессий, который уже загружен ради ленты; новых запросов нет.
  const today = data.day.sessions > 0;
  const archiveSeconds = sessions?.reduce((sum, row) => sum + row.seconds, 0) ?? 0;

  return (
    <div className="studio-home">
      <header className="studio-home-heading">
        <h1>Сводка</h1>
        <span className="studio-date">{formatDayLong(todayIso())}</span>
      </header>

      <section className="studio-instrument" aria-label="Лента дня">
        <div className="studio-instrument-label">
          <span><WaveIcon size={17} aria-hidden /> Лента дня</span>
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

        {/* Нулей на шкале не бывает: пустой день переводит прибор на архив. */}
        {today ? (
          <div className="studio-meter-stats">
            <div>
              <span className="studio-meter-label"><Clock size={13} aria-hidden /> Записано сегодня</span>
              <strong>{formatMinutes(data.day.minutes)}</strong>
            </div>
            <div>
              <span className="studio-meter-label"><WaveIcon size={13} aria-hidden /> Разговоров</span>
              <strong>{data.day.sessions}</strong>
            </div>
            <div>
              <span className="studio-meter-label"><CheckCircle size={13} aria-hidden /> Новых задач</span>
              <strong>{data.day.new_tasks}</strong>
            </div>
          </div>
        ) : archiveSeconds > 0 ? (
          <div className="studio-meter-stats">
            <div>
              <span className="studio-meter-label"><Clock size={13} aria-hidden /> Всего записано</span>
              <strong>{formatDuration(archiveSeconds)}</strong>
            </div>
            <div>
              <span className="studio-meter-label"><WaveIcon size={13} aria-hidden /> Разговоров</span>
              <strong>{sessions?.length ?? 0}</strong>
            </div>
          </div>
        ) : null}

        {/* Лампы обработки горят, только пока есть работа: «Расшифровка ·
            готово» — отчёт о том, что всё в порядке, а его не показываем. */}
        {queue || waiting ? (
          <div className="studio-processing">
            {queue ? (
              <span className="pending">
                <i aria-hidden />Расшифровка · {count(queue, "часть", "части", "частей")} в очереди
              </span>
            ) : null}
            {waiting ? (
              <span className="pending">
                <i aria-hidden />Саммари · {count(waiting, "ждёт", "ждут", "ждут")}
              </span>
            ) : null}
            <Link href="/status/" aria-label="Состояние обработки" className="hit relative"><ArrowRight size={16} aria-hidden /></Link>
          </div>
        ) : null}

        {attention.length > 0 ? (
          <nav className="studio-attention" aria-label="Требует внимания">
            {attention.map((item) => (
              <Link key={item.href} href={item.href} className={cx("hit relative", item.tone)}>
                <i aria-hidden />{item.label}<strong>{item.count}</strong>
              </Link>
            ))}
          </nav>
        ) : null}
      </section>

      <div className="studio-section-heading">
        <h2>Последние записи</h2>
        <Link href="/records/" className="hit relative">Все записи <ArrowRight size={16} aria-hidden /></Link>
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

      {/* Задач на владельце нет — секции нет: «на вас задач нет» — отчёт, что
          всё в порядке, а его не показываем. Долги всех — в полосе внимания. */}
      {(!data.has_me || data.my_tasks.length > 0) && (
        <section className="studio-tasks-panel" aria-label="Мои задачи">
          <div className="studio-section-heading">
            <h2>Мои задачи{data.week.my_open_tasks > 0 && <span>{data.week.my_open_tasks}</span>}</h2>
            <Link href="/tasks/?show=mine" aria-label="Все мои задачи" className="hit relative"><ArrowRight size={18} aria-hidden /></Link>
          </div>
          {!data.has_me ? (
            <p>Ни один говорящий не отмечен как «это я» — отметьте себя на странице записи, и здесь появятся ваши задачи.</p>
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
      )}

    </div>
  );
}
