"use client";

import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import { formatClock } from "@/lib/format";

type SeekFn = (part: number, seconds: number) => void;

const SeekContext = createContext<SeekFn>(() => {});

export function useSeek() {
  return useContext(SeekContext);
}

/**
 * Плеер закреплён снизу по центру — место, где в референсе живёт чат-виджет.
 * Один элемент audio на страницу: параллельно играющие части путают.
 */
export function PlayerProvider({ deviceId, session, children }:
  { deviceId: string; session: number; children: React.ReactNode }) {
  const audio = useRef<HTMLAudioElement>(null);
  // Слушатель loadedmetadata предыдущей, ещё не догрузившейся части —
  // при быстром переключении отменяем его, иначе сработают все подряд.
  const pending = useRef<AbortController | null>(null);
  const [part, setPart] = useState<number | null>(null);
  const [position, setPosition] = useState(0);
  const [blocked, setBlocked] = useState(false);

  const seek = useCallback<SeekFn>((nextPart, seconds) => {
    const element = audio.current;
    if (!element) return;
    pending.current?.abort();
    const source = `/audio/${encodeURIComponent(deviceId)}/${session}/${nextPart}`;
    if (part !== nextPart) {
      element.src = source;
      setPart(nextPart);
    }
    const start = () => {
      element.currentTime = seconds;
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

  return (
    <SeekContext.Provider value={value}>
      {children}
      <div className="fixed inset-x-0 bottom-0 z-20 mx-auto w-full max-w-[360px] px-4 pb-4">
        <div className="rounded-2xl border border-separator-border bg-background-primary-default p-4 shadow-player">
          <p className="text-caption-1-medium uppercase tracking-[0.12em] text-text-tertiary">
            {part === null ? "часть не выбрана" : `часть ${String(part).padStart(2, "0")}`}
            <span className="nums"> · {formatClock(position)}</span>
          </p>
          {blocked && (
            <p className="mt-1 text-body-2-regular text-text-tertiary">
              нажмите ▶, браузер не разрешил автозапуск
            </p>
          )}
          <audio ref={audio} controls preload="none" className="mt-2 w-full"
                 onPlay={() => setBlocked(false)}
                 onTimeUpdate={(event) => setPosition(event.currentTarget.currentTime)} />
        </div>
      </div>
    </SeekContext.Provider>
  );
}
