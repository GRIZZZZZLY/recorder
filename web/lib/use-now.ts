"use client";

import { useEffect, useState } from "react";

/**
 * Текущее время, обновляемое по таймеру.
 *
 * «Последняя выгрузка 3 минуты назад» и точка «пишет сейчас» обязаны стареть
 * сами: страница висит открытой часами, а перечитывать данные ради подписи
 * незачем. Время берётся один раз при монтировании и дальше тикает — вызывать
 * Date.now() прямо в рендере нельзя, результат нестабилен между отрисовками.
 */
export function useNow(intervalMs = 60_000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

/** Диктофон выгружал часть меньше пяти минут назад — значит пишет прямо сейчас. */
export function isLive(stamp: string | null | undefined, now: Date): boolean {
  if (!stamp) return false;
  // Метки базы — UTC без зоны ("2026-09-12 16:40:07").
  const at = new Date(`${stamp.replace(" ", "T")}Z`).getTime();
  return Number.isFinite(at) && now.getTime() - at < 5 * 60 * 1000;
}
