"use client";

import { useState } from "react";
import { Section } from "@/components/recorder/list";
import { PageHeader } from "@/components/recorder/page-header";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { StudioRecord } from "@/components/recorder/studio-record";
import { Button } from "@/components/recorder/ui";
import { useApi } from "@/lib/use-api";
import { ApiError, apiDelete } from "@/lib/api";
import type { SessionRow } from "@/lib/api";
import { count, formatDayGroup, formatDuration } from "@/lib/format";
import { removeEach } from "@/lib/remove-each";

/** Записи одного дня подряд: список приходит отсортированным, свежие первыми. */
function byDay(rows: SessionRow[]) {
  const groups: { day: string | null; rows: SessionRow[] }[] = [];
  for (const row of rows) {
    const last = groups[groups.length - 1];
    if (last && last.day === row.day) last.rows.push(row);
    else groups.push({ day: row.day, rows: [row] });
  }
  return groups;
}

const keyOf = (row: SessionRow) => `${row.device_id}:${row.session}`;

export default function RecordsPage() {
  const { data, error, loading, reload } = useApi<SessionRow[]>("/sessions");
  // Выбор записей для удаления. Отмеченные хранятся ключами, а сами записи
  // берутся из свежего списка: страница обновляется раз в минуту, и запись,
  // исчезнувшая из списка, сама выпадает из выбора.
  const [picking, setPicking] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [asking, setAsking] = useState(false);
  // Когда показали «Удалить навсегда?». Кнопка подтверждения встаёт на место
  // «Удалить», и двойное нажатие удаляло бы без вопроса: первые полсекунды
  // нажатие на неё не считается.
  const [askedAt, setAskedAt] = useState(0);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !data) return <Loading />;

  const total = data.reduce((sum, row) => sum + row.seconds, 0);
  const selected = data.filter((row) => picked.has(keyOf(row)));
  const selectedSeconds = selected.reduce((sum, row) => sum + row.seconds, 0);

  function start() {
    setPicking(true);
    setPicked(new Set());
    setAsking(false);
    setFailure(null);
  }

  function cancel() {
    setPicking(false);
    setPicked(new Set());
    setAsking(false);
    setFailure(null);
  }

  function toggle(row: SessionRow) {
    if (busy) return;
    // Выбор поменялся — прежнее «удалить N навсегда?» уже про другое.
    setAsking(false);
    setPicked((current) => {
      const next = new Set(current);
      if (!next.delete(keyOf(row))) next.add(keyOf(row));
      return next;
    });
  }

  // Время берётся из самих нажатий (timeStamp): у обоих событий одна шкала.
  function ask(event: React.MouseEvent) {
    setAsking(true);
    setAskedAt(event.timeStamp);
  }

  async function remove(event: React.MouseEvent) {
    if (event.timeStamp - askedAt < 500) return;
    setBusy(true);
    setFailure(null);
    // Сервер удаляет по одной записи — тем же запросом, что кнопка на
    // странице записи. Уже удалённая (другой вкладкой) — не сбой: итог тот же.
    const failed = await removeEach(selected, (row) =>
      apiDelete(`/sessions/${encodeURIComponent(row.device_id)}/${row.session}`).catch((err) => {
        if (err instanceof ApiError && err.status === 404) return;
        throw err;
      }));
    setBusy(false);
    setAsking(false);
    // Не удалившиеся остаются отмеченными: повторить можно одним нажатием.
    setPicked(new Set(failed.map(({ item }) => keyOf(item))));
    if (failed.length) {
      setFailure(`не удалось удалить ${failed.length}: ${failed[0].reason}`);
    } else {
      setPicking(false);
    }
    reload();
  }

  return (
    <>
      <PageHeader
        title="Записи"
        // Одно число, и то показание прибора: сколько звука лежит в архиве.
        // Счётчик сессий владельцу ничего не говорит.
        aside={
          <span className="flex flex-wrap items-center gap-x-3.5 gap-y-1">
            {data.length > 1 ? <span>{formatDuration(total)}</span> : null}
            {data.length > 0 && !picking ? (
              <Button variant="secondary" onClick={start}>Выбрать</Button>
            ) : null}
          </span>
        }
      />

      {data.length === 0 ? (
        <Empty title="Записей ещё нет"
               text="Диктофон не присылал ни одной части. Проверьте, что устройство в сети." />
      ) : (
        byDay(data).map((group) => (
          <Section key={group.day ?? "no-day"} note={formatDayGroup(group.day)}>
            {/* Разговор узнают по часу, теме и людям — всё это на карточке. */}
            <div className="studio-record-grid">
              {group.rows.map((row) => (
                <StudioRecord key={keyOf(row)} row={row}
                              selected={picked.has(keyOf(row))}
                              onToggle={picking ? () => toggle(row) : undefined} />
              ))}
            </div>
          </Section>
        ))
      )}

      {picking && (
        <div className="records-pick-bar" role="region" aria-label="Выбранные записи">
          {asking ? (
            <>
              <span className="records-pick-text" aria-live="polite">
                Удалить {count(selected.length, "запись", "записи", "записей")} навсегда?
                <span className="block text-note text-text-secondary">
                  Звук, расшифровка, саммари и задачи сотрутся без возврата.
                </span>
              </span>
              <span className="records-pick-actions">
                <Button key="confirm" variant="primary" disabled={busy} onClick={remove}>
                  {busy ? "Удаляю…" : "Удалить навсегда"}
                </Button>
                <Button variant="ghost" disabled={busy} onClick={() => setAsking(false)}>Отмена</Button>
              </span>
            </>
          ) : (
            <>
              <span className="records-pick-text" aria-live="polite">
                {selected.length
                  ? <>Выбрано {selected.length} · <span className="font-mono">{formatDuration(selectedSeconds)}</span></>
                  : "Отметьте записи"}
              </span>
              <span className="records-pick-actions">
                <Button key="ask" variant="secondary" disabled={!selected.length} onClick={ask}>
                  Удалить
                </Button>
                <Button variant="ghost" onClick={cancel}>Отмена</Button>
              </span>
            </>
          )}
          {failure ? <p className="rise-in w-full text-note text-accent-text">{failure}</p> : null}
        </div>
      )}
    </>
  );
}
