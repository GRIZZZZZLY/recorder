import Link from "next/link";
import type { SessionRow } from "@/lib/api";
import { formatDay, formatDuration } from "@/lib/format";
import { sessionHref } from "@/lib/session-link";

export function SessionCard({ row }: { row: SessionRow }) {
  const ready = row.summary_status === "done";
  return (
    <Link href={sessionHref(row.device_id, row.session)}
          className="block rounded-2xl border border-separator-border bg-background-primary-default p-6 hover:bg-background-primary-hover">
      <div className="flex items-center justify-between gap-4">
        <p className="text-headline-medium text-text-primary">
          Сессия <span className="nums">{String(row.session).padStart(4, "0")}</span>
        </p>
        <span className="rounded-full border border-separator-border px-3 py-1 text-caption-1-medium text-text-secondary">
          {ready ? "саммари готово" : "обрабатывается"}
        </span>
      </div>
      <p className="nums mt-2 text-body-2-regular text-text-tertiary">
        {formatDay(row.day)} · {formatDuration(row.seconds)} · частей {row.parts},
        расшифровано {row.done_parts}
      </p>
    </Link>
  );
}
