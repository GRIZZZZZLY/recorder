"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiGet } from "@/lib/api";

/** Как часто обновлять открытую страницу. Минута: диктофон выгружает части
 *  пачками, а не по одной, и чаще смотреть не на что. */
const REFRESH_MS = 60_000;

/**
 * Загрузка одного адреса. path === null — не загружать (ждём параметр).
 *
 * Страница сама освежается: при возврате к вкладке и раз в минуту, пока она
 * видна. Обновление фоновое — прежние данные остаются на экране, скелетон не
 * появляется, ошибка сети не стирает то, что уже показано: пропала связь на
 * секунду — на экране ничего не дрогнуло.
 *
 * Потока событий с сервера здесь нет намеренно. Расшифровкой занимается
 * отдельный процесс-воркер, и приёмник о его успехах узнаёт только из базы —
 * то есть опросом. Опрос на стороне браузера даёт ровно тот же результат и не
 * требует ни висящих соединений, ни настройки буферизации в nginx.
 */
export function useApi<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const [tick, setTick] = useState(0);
  const background = useRef<AbortController | null>(null);

  useEffect(() => {
    if (path === null) return;
    let alive = true;
    const controller = new AbortController();
    setLoading(true);
    apiGet<T>(path, { signal: controller.signal })
      .then((body) => alive && (setData(body), setError(null)))
      .catch((problem) => {
        if (problem instanceof DOMException && problem.name === "AbortError") return;
        alive && setError(problem as Error);
      })
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
      controller.abort();
    };
  }, [path, tick]);

  /** Тихо перечитать: без скелетона и без стирания данных при неудаче. */
  const refresh = useCallback(() => {
    if (path === null || document.visibilityState !== "visible") return;
    background.current?.abort();
    const controller = new AbortController();
    background.current = controller;
    apiGet<T>(path, { signal: controller.signal })
      .then((body) => { setData(body); setError(null); })
      .catch(() => {
        // Молчим намеренно: на экране осталось прежнее, и это лучше, чем
        // красная плашка из-за одной неудачной попытки в фоне.
      });
  }, [path]);

  useEffect(() => {
    if (path === null) return;
    const timer = window.setInterval(refresh, REFRESH_MS);
    document.addEventListener("visibilitychange", refresh);
    window.addEventListener("focus", refresh);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
      window.removeEventListener("focus", refresh);
      background.current?.abort();
    };
  }, [path, refresh]);

  const reload = useCallback(() => setTick((value) => value + 1), []);
  return { data, error, loading, reload };
}
