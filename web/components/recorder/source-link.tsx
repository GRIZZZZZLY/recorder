import Link from "next/link";
import { RiPlayCircleLine } from "@remixicon/react";
import { cx } from "@/utils/cx";
import type { Source } from "@/lib/api";
import { sessionHref } from "@/lib/session-link";

/** Цвет точки — состояние источника: подтверждён, не подтверждён, не найден. */
export function SourceDot({ source }: { source: Source }) {
  const title = !source
    ? "источник не найден"
    : source.confirmed
      ? "цитата найдена в записи"
      : "источник не подтверждён";
  const color = !source
    ? "bg-[var(--color-source-missing)]"
    : source.confirmed
      ? "bg-[var(--color-source-confirmed)]"
      : "bg-[var(--color-source-unconfirmed)]";
  return (
    <span role="img" title={title} aria-label={title}
          className={cx("inline-block size-2 rounded-full", color)} />
  );
}

/** Ссылка на реплику-основание внутри страницы сессии. */
export function ListenLink({ source, deviceId, session }:
  { source: Source; deviceId: string; session: number }) {
  if (!source) return null;
  return (
    <Link
      href={sessionHref(deviceId, session, source.part, source.idx)}
      className="inline-flex items-center gap-1 text-body-2-medium text-text-secondary underline-offset-2 hover:underline"
    >
      <RiPlayCircleLine className="size-4" aria-hidden />
      Послушать основание
    </Link>
  );
}
