"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowUpRight, BookmarkSimple, Check, Play } from "@phosphor-icons/react/dist/ssr";
import type { SessionRow } from "@/lib/api";
import { startPlayback } from "@/lib/audio";
import { count, formatDuration, formatTime, recordTitle } from "@/lib/format";
import { sessionHref } from "@/lib/session-link";
import { statusTone, statusWord } from "@/lib/session-status";
import { minutesSince, useNow } from "@/lib/use-now";
import { Waveform } from "./waveform";

/**
 * Карточка записи «Студийного прибора»: шильдик с номером и часом, статус
 * обработки, тема, участники, волна с длительностью, метки.
 *
 * Номер записи здесь — гравировка на корпусе (REC / 0009), а не отчёт системы;
 * число частей по-прежнему всплывает только пока расшифровка отстаёт от записи.
 *
 * Карточка — не одна большая ссылка: клавиша ▶ на ней настоящая и играет
 * запись сразу, а ссылка — заголовок, растянутый на всю карточку (::after).
 * Кнопка внутри ссылки была бы недопустимой разметкой.
 *
 * В режиме выбора (передан `onToggle`) нажатие на карточку отмечает её, а не
 * открывает запись: заголовок становится кнопкой-переключателем, а на месте ▶
 * встаёт отметка — звук при выборе не нужен и включался бы случайно.
 */
export function StudioRecord({ row, compact = false, selected = false, onToggle }: {
  row: SessionRow;
  /** На сводке карточки ниже: волна 30 px вместо 40. */
  compact?: boolean;
  selected?: boolean;
  onToggle?: () => void;
}) {
  const tone = statusTone(row);
  // Отстаёт — только пока части ждут: упавшая не ждёт, о ней говорит статус.
  const lagging = row.done_parts + row.failed_parts < row.parts;
  // Имя — одно на всех экранах (recordTitle). «Тема появится после саммари» —
  // не имя, а обещание под ним, и только пока саммари действительно
  // считается: у готового саммари тем может не быть вовсе (бытовая запись).
  // Больше часа без движения — уже не «обрабатывается», а «ждёт с такого-то
  // дня»: обещание уместно, только пока работа идёт.
  const now = useNow();
  const stale = tone === "warn" && minutesSince(row.last_at, now) > 60;
  const pending = tone === "warn" && !row.topic && !stale;
  const title = recordTitle(row);
  const href = sessionHref(row.device_id, row.session);
  const router = useRouter();

  // Звук стартует в самом нажатии — iPhone разрешает только так, — и плеер
  // на странице записи подхватывает его уже играющим.
  function listen() {
    startPlayback(row.device_id, row.session, row.first_part, title);
    router.push(href);
  }

  return (
    <div className={selected ? "studio-record is-selected" : "studio-record"}>
      <div className="studio-record-meta">
        <span className="studio-rec-no">REC / {String(row.session).padStart(4, "0")}</span>
        {/* Без темы имя записи уже содержит час — второй раз не нужен. */}
        {row.topic ? <span>{formatTime(row.first_at)}</span> : null}
        {/* «Готово» — отчёт, что всё в порядке: его не показываем. */}
        {tone !== "ok" && (
          <span className={`studio-record-status ${tone}`}>
            <i aria-hidden />{statusWord(row, now)}
          </span>
        )}
      </div>
      <h3>
        {onToggle ? (
          <button type="button" className="studio-record-link" aria-pressed={selected} onClick={onToggle}>
            {title}
          </button>
        ) : (
          <>
            <Link href={href} className="studio-record-link">{title}</Link>
            <ArrowUpRight size={17} aria-hidden />
          </>
        )}
      </h3>
      {pending && <div className="studio-record-person">тема появится после саммари</div>}
      {row.speakers.length > 0 && (
        <div className="studio-record-person">{row.speakers.join(" · ")}</div>
      )}
      <div className="studio-record-audio">
        {onToggle ? (
          <span className="studio-record-check" aria-hidden>
            {selected ? <Check size={18} weight="bold" /> : null}
          </span>
        ) : (
          <button type="button" className="studio-small-play hit" onClick={listen}
                  aria-label={`Слушать: ${title}`}>
            <Play size={17} weight="fill" aria-hidden />
          </button>
        )}
        <div className="studio-wave-preview">
          <Waveform values={row.envelope} height={compact ? 30 : 40} className="w-full" />
        </div>
        <span className="studio-duration">{formatDuration(row.seconds)}</span>
      </div>
      {(lagging || row.marks.length > 0) && (
        <div className="studio-record-bottom">
          {lagging && <span>расшифровано {row.done_parts} из {row.parts}</span>}
          {row.marks.length > 0 && (
            <span className="studio-bookmark" title="отмечено кнопкой на диктофоне">
              <BookmarkSimple size={14} weight="fill" aria-hidden />
              {count(row.marks.length, "метка", "метки", "меток")}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
