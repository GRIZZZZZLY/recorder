"use client";

import { PageTitle } from "@/components/recorder/eyebrow";
import { SessionCard } from "@/components/recorder/session-card";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { useApi } from "@/lib/use-api";
import type { SessionRow } from "@/lib/api";

export default function RecordsPage() {
  const { data, error, loading, reload } = useApi<SessionRow[]>("/sessions");

  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !data) return <Loading />;

  return (
    <>
      <PageTitle eyebrow="ДИКТОФОН" plain="Все" italic="записи" />
      {data.length === 0 ? (
        <Empty text="Пока пусто: записей с диктофона ещё не приходило." />
      ) : (
        <ul className="flex flex-col gap-3">
          {data.map((row) => (
            <li key={`${row.device_id}-${row.session}`}>
              <SessionCard row={row} />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
