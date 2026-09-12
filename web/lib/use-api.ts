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
    setLoading(true);
    apiGet<T>(path)
      .then((body) => alive && (setData(body), setError(null)))
      .catch((problem) => alive && setError(problem as Error))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [path, tick]);

  const reload = useCallback(() => setTick((value) => value + 1), []);
  return { data, error, loading, reload };
}
