"use client";

import { useCallback, useEffect, useState } from "react";
import { apiGet } from "@/lib/api";

/** Загрузка одного адреса. path === null — не загружать (ждём параметр). */
export function useApi<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const [tick, setTick] = useState(0);

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

  const reload = useCallback(() => setTick((value) => value + 1), []);
  return { data, error, loading, reload };
}
