"use client";

import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import { Pause, Play } from "@phosphor-icons/react/dist/ssr";
import { thin, Waveform } from "@/components/recorder/waveform";
import type { Mark } from "@/lib/api";
import { formatClock } from "@/lib/format";
import { cx } from "@/utils/cx";

type SeekFn = (part: number, seconds: number, label?: string) => void;

const SeekContext = createContext<SeekFn>(() => {});

export function useSeek() {
  return useContext(SeekContext);
}

/**
 * Плавающий плеер сессии — единственная карточка с тенью во всём интерфейсе.
 *
 * Один элемент <audio> на страницу: параллельно играющие части путают. Своя
 * кнопка и своя волна вместо `controls` — нативный виджет в тёмной теме
 * выглядит чужим, а главное, не умеет показывать, какая это часть из скольких.
 * Перемотка — пальцем по волне, а не только тычком в неё.
 */
export function PlayerProvider({ deviceId, session, parts, marks = [], envelopes = {}, children }: {
  deviceId: string;
  session: number;
  parts: number;
  /** Метки кнопкой: засечки на волне той части, которая сейчас открыта. */
  marks?: Mark[];
  /** Громкость по номеру части: волна плеера рисует ту, что играет. */
  envelopes?: Record<number, number[]>;
  children: React.ReactNode;
}) {
  const audio = useRef<HTMLAudioElement>(null);
  // Слушатель loadedmetadata предыдущей, ещё не догрузившейся части —
  // при быстром переключении отменяем его, иначе сработают все подряд.
  const pending = useRef<AbortController | null>(null);
  const [part, setPart] = useState<number | null>(null);
  const [label, setLabel] = useState<string>("");
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [blocked, setBlocked] = useState(false);

  const seek = useCallback<SeekFn>((nextPart, seconds, nextLabel) => {
    const element = audio.current;
    if (!element) return;
    pending.current?.abort();
    if (nextLabel !== undefined) setLabel(nextLabel);
    if (part !== nextPart) {
      element.src = `/audio/${encodeURIComponent(deviceId)}/${session}/${nextPart}`;
      setPart(nextPart);
    }
    const start = () => {
      element.currentTime = seconds;
      setPosition(seconds);
      // Автовоспроизведение может быть запрещено браузером — тогда молча
      // ничего не происходит, если не сообщить об этом явно.
      element.play().then(() => setBlocked(false)).catch(() => setBlocked(true));
    };
    if (element.readyState >= 1) {
      start();
    } else {
      const controller = new AbortController();
      pending.current = controller;
      element.addEventListener("loadedmetadata", start, { once: true, signal: controller.signal });
    }
  }, [deviceId, session, part]);

  const value = useMemo(() => seek, [seek]);

  function toggle() {
    const element = audio.current;
    if (!element || part === null) return;
    if (element.paused) element.play().then(() => setBlocked(false)).catch(() => setBlocked(true));
    else element.pause();
  }

  // Перемотка по волне: палец ведёт по ней, время под пальцем меняется сразу,
  // а сам звук прыгает один раз — на отпускании. Ставить currentTime на каждое
  // движение нельзя: каждая установка — новый Range-запрос к файлу.
  const scrubbing = useRef(false);

  function secondsAt(event: React.PointerEvent<HTMLDivElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    return Math.min(Math.max((event.clientX - box.left) / box.width, 0), 1) * duration;
  }

  function onScrubStart(event: React.PointerEvent<HTMLDivElement>) {
    if (idle || !duration) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    scrubbing.current = true;
    setPosition(secondsAt(event));
  }

  function onScrubMove(event: React.PointerEvent<HTMLDivElement>) {
    if (scrubbing.current) setPosition(secondsAt(event));
  }

  function onScrubEnd(event: React.PointerEvent<HTMLDivElement>) {
    if (!scrubbing.current) return;
    scrubbing.current = false;
    const element = audio.current;
    if (element) element.currentTime = secondsAt(event);
  }

  const idle = part === null;

  return (
    <SeekContext.Provider value={value}>
      {/* Отступ снизу принадлежит плееру: иначе последняя реплика прячется
          под его карточкой. */}
      <div className="pb-32">{children}</div>

      <div className={cx(
        "player-in grille fixed right-3 bottom-24 left-3 z-20 flex items-center gap-3 rounded-card bg-surface-elevated px-3 py-2.5 shadow-player",
        "lg:right-12 lg:bottom-7 lg:left-[268px] lg:gap-4 lg:px-4 lg:py-3.5",
      )}>
        <button
          type="button"
          onClick={toggle}
          disabled={idle}
          aria-label={playing ? "Пауза" : "Играть"}
          className={cx(
            "press hit relative flex size-9 shrink-0 items-center justify-center rounded-chip bg-fill text-canvas disabled:opacity-40 lg:size-10",
            // Свечение — пока идёт звук; пауза гасит.
            playing && "shadow-glow-fill",
          )}
        >
          {/* Обе иконки лежат друг на друге: смена play ↔ pause — кроссфейд за
              120 мс, а не подмена глифа между кадрами. */}
          <Pause size={14} weight="fill" aria-hidden
                 className={cx("absolute transition-opacity duration-120", playing ? "opacity-100" : "opacity-0")} />
          <Play size={14} weight="fill" aria-hidden
                className={cx("absolute transition-opacity duration-120", playing ? "opacity-0" : "opacity-100")} />
        </button>

        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          <div className="flex justify-between gap-3 text-note">
            <span className="truncate">
              {idle ? "Нажмите «основание» или реплику" : label || "Фрагмент записи"}
              {blocked ? <span className="text-text-secondary"> · браузер не разрешил автозапуск</span> : null}
            </span>
            <span className="shrink-0 text-text-secondary">
              {idle ? `частей ${parts}` : (
                <>
                  {/* На телефоне время стоит здесь: при перемотке оно нужнее
                      слова «часть», а отдельной колонке справа места нет. */}
                  <span className="font-mono sm:hidden">
                    <span className="text-text-primary">{formatClock(position)}</span>
                    {" / "}{formatClock(duration)}{" · "}
                  </span>
                  <span className="hidden sm:inline">часть </span>
                  {String(part).padStart(2, "0")} / {String(parts).padStart(2, "0")}
                </>
              )}
            </span>
          </div>
          {/* Волна видна на 24 px, а под пальцем — 44 (`hit`). touch-pan-y: вести
              по ней вбок — перемотка, тянуть вверх — по-прежнему прокрутка страницы. */}
          <div
            onPointerDown={onScrubStart}
            onPointerMove={onScrubMove}
            onPointerUp={onScrubEnd}
            onPointerCancel={onScrubEnd}
            className={cx("hit relative h-6 touch-pan-y select-none lg:h-8", !idle && "cursor-pointer")}
          >
            <Waveform
              values={thin(envelopes[part ?? 0] ?? [], 100)}
              height={idle ? 12 : 28}
              played={duration ? position / duration : 0}
              className="w-full"
            />
            {/* Засечки меток этой части: место, где владелец нажал кнопку. */}
            {duration > 0 && marks.filter((mark) => mark.part === part).map((mark) => (
              <span key={mark.offset} aria-hidden
                    className="absolute top-0 bottom-0 w-px bg-fill"
                    style={{ left: `${Math.min(100, (mark.offset / duration) * 100)}%` }} />
            ))}
          </div>
        </div>

        <div className="hidden shrink-0 font-mono text-note text-text-secondary sm:block">
          <span className="text-text-primary">{formatClock(position)}</span> / {formatClock(duration)}
        </div>

        <audio
          ref={audio}
          preload="none"
          onPlay={() => { setPlaying(true); setBlocked(false); }}
          onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)}
          onLoadedMetadata={(event) => setDuration(event.currentTarget.duration || 0)}
          onTimeUpdate={(event) => { if (!scrubbing.current) setPosition(event.currentTarget.currentTime); }}
        />
      </div>
    </SeekContext.Provider>
  );
}
