"use client";

import Link from "next/link";
import { ArrowUpRight, BookmarkSimple, Play } from "@phosphor-icons/react/dist/ssr";
import type { SessionRow } from "@/lib/api";
import { count, formatDay, formatDuration, formatTime } from "@/lib/format";
import { sessionHref } from "@/lib/session-link";
import { statusTone, STATUS_TEXT } from "@/lib/session-status";
import { Waveform } from "./waveform";

/**
 * Карточка записи «Студийного прибора»: шильдик с номером и часом, статус
 * обработки, тема, участники, волна с длительностью, метки.
 *
 * Номер записи здесь — гравировка на корпусе (REC / 0009), а не отчёт системы;
 * число частей по-прежнему всплывает только пока расшифровка отстаёт от записи.
 */
export function StudioRecord({ row, compact = false }: {
  row: SessionRow;
  /** На сводке карточки ниже: волна 30 px вместо 40. */
  compact?: boolean;
}) {
  const tone = statusTone(row);
  const lagging = row.done_parts < row.parts;
  // Без темы разговор зовётся своим днём и часом. «Тема появится после
  // саммари» — обещание, и оно верно, только пока саммари действительно
  // считается: у готового саммари тем может не быть вовсе (бытовая запись),
  // и обещать там нечего.
  const pending = tone === "warn";
  const title = row.topic
    || (pending ? null : row.day ? `${formatDay(row.day)}, ${formatTime(row.first_at)}` : null);
  return (
    <Link className="studio-record" href={sessionHref(row.device_id, row.session)}>
      <div className="studio-record-meta">
        <span className="studio-rec-no">REC / {String(row.session).padStart(4, "0")}</span>
        <span>{formatTime(row.first_at)}</span>
        <span className={`studio-record-status ${tone}`}><i aria-hidden />{STATUS_TEXT[tone]}</span>
      </div>
      <h3>
        {title
          ? <span>{title}</span>
          : <span className="muted">
              {pending ? "тема появится после саммари" : "разговор без темы"}
            </span>}
        <ArrowUpRight size={17} aria-hidden />
      </h3>
      {row.speakers.length > 0 && (
        <div className="studio-record-person">{row.speakers.join(" · ")}</div>
      )}
      <div className="studio-record-audio">
        <span className="studio-small-play" aria-hidden><Play size={17} weight="fill" /></span>
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
    </Link>
  );
}
