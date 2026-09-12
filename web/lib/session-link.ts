/** Ссылка на сессию в плеере. part/idx — если нужно указать конкретную реплику. */
export function sessionHref(deviceId: string, session: number, part?: number, idx?: number): string {
  const base = `/session/?d=${encodeURIComponent(deviceId)}&n=${session}`;
  return part !== undefined && idx !== undefined ? `${base}#u-${part}-${idx}` : base;
}
