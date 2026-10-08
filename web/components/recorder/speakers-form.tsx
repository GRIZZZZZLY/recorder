"use client";

import { useState } from "react";
import { Card, Row, Section } from "@/components/recorder/list";
import { Button } from "@/components/recorder/ui";
import { apiPost } from "@/lib/api";
import { changedNames, expandNames, groupRows, isGuess, pickMe, type GroupRow } from "@/lib/speakers";
import { count } from "@/lib/format";
import { cx } from "@/utils/cx";

type Speaker = { part: number; speaker: number; name: string; is_me: boolean; auto: boolean };

/**
 * Переключатель «это я». Внутри — нативная радиокнопка: «я» в разговоре один,
 * а значит это выбор из группы, а не набор флажков. Рисуем поверх неё
 * переключатель, чтобы состояние читалось с одного взгляда.
 */
function MeSwitch({ value, current, onChange }: {
  value: string;
  current: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="hit relative flex shrink-0 cursor-pointer items-center gap-2.5">
      <span className="text-micro text-text-secondary">это я</span>
      <input
        type="radio"
        name="me"
        value={value}
        checked={current === value}
        onChange={() => onChange(value)}
        className="peer sr-only"
      />
      {/* Ручка — потомок трека, а не сосед инпута, поэтому её состояние
          задаётся через вложенный селектор: обычный peer-checked достал бы
          только сам трек. */}
      <span aria-hidden className="relative block h-[22px] w-9 rounded-chip bg-hairline-strong transition-colors peer-checked:bg-ok peer-checked:[&>span]:translate-x-3.5 peer-checked:[&>span]:bg-fill peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-focus">
        <span className="absolute top-0.5 left-0.5 block size-[18px] rounded-chip bg-text-secondary transition-transform duration-320 ease-spring" />
      </span>
    </label>
  );
}

/** «13 номеров · 25 мин»: сколько номеров Gemini склеено в голос и сколько он говорил. */
function voiceMeta(row: GroupRow): string {
  const minutes = row.seconds >= 60 ? `${Math.round(row.seconds / 60)} мин` : `${Math.round(row.seconds)} с`;
  return row.keys.length > 1
    ? `${count(row.keys.length, "номер", "номера", "номеров")} · ${minutes}`
    : `ч.${row.key.split(":")[0].padStart(2, "0")} · ${minutes}`;
}

export function SpeakersForm({ deviceId, session, speakers, groups, shortKeys, meName, onSaved }:
  { deviceId: string; session: number; speakers: Speaker[];
    groups: { keys: string[]; seconds: number }[]; shortKeys: string[];
    meName: string; onSaved: () => void }) {
  // Строки — голоса, а не номера: Gemini дробит людей по 5-минутным порциям.
  const [rows] = useState(() => groupRows(speakers, groups, shortKeys));
  // То, что страница показала при загрузке: сравниваем с ним, а не шлём всё.
  // Форма пересоздаётся при каждом новом списке (key в page.tsx).
  const [shown] = useState(() => Object.fromEntries(rows.map((r) => [r.key, r.name])));
  const [shownMe] = useState(() => rows.find((r) => r.is_me)?.key ?? "");
  const [names, setNames] = useState(shown);
  const [me, setMe] = useState(shownMe);
  // Строка, куда «это я» само подставило имя вместо догадки (см. pickMe).
  const [filled, setFilled] = useState<{ key: string; value: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (rows.length === 0) return null;
  const voices = rows.filter((r) => !r.short);
  const short = rows.filter((r) => r.short);

  async function save() {
    setBusy(true);
    try {
      // Имя голоса уходит на все его номера; сервер сам не тронет нетронутые.
      await apiPost(`/sessions/${encodeURIComponent(deviceId)}/${session}/speakers`,
                    { names: expandNames(rows, changedNames(shown, names, shownMe, me)),
                      me: me === "" ? null : me });
      setError(null);
      onSaved();
    } catch (err) {
      setError(`не удалось сохранить: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  function line(row: GroupRow, who: string, meta: string) {
    const name = names[row.key] ?? "";
    const guess = isGuess(row, name, me);
    return (
      <Row key={row.key}>
        <span aria-hidden
              className="flex size-7 shrink-0 items-center justify-center rounded-chip bg-surface-elevated text-micro text-text-secondary">
          {name.trim() ? name.trim()[0].toUpperCase() : who.split(" ").pop()}
        </span>
        <div className="flex min-w-0 flex-1 flex-col">
          {/* Пометка лежит поверх поля, а не рядом: иначе поля в строках
              с догадкой и без неё разной ширины, и столбец имён пляшет. */}
          <div className="relative">
            <input
              value={name}
              placeholder={who}
              aria-label={`Имя: ${who}, ${meta}${guess ? ", угадано по голосу" : ""}`}
              title={guess ? "Имя поставлено по сходству голоса с теми, кого вы уже называли. Неверно — исправьте или сотрите." : undefined}
              onChange={(event) => setNames((old) => ({ ...old, [row.key]: event.target.value }))}
              className={cx("w-full rounded-inner bg-transparent px-2 py-1 text-row text-text-primary transition-colors placeholder:text-text-tertiary hover:bg-hairline max-md:min-h-11",
                            guess && "pr-20")}
            />
            {guess && (
              <span aria-hidden
                    className="pointer-events-none absolute top-1/2 right-2 -translate-y-1/2 text-micro text-text-tertiary">
                по голосу
              </span>
            )}
          </div>
          <span className="px-2 font-mono text-micro text-text-tertiary">{meta}</span>
        </div>
        <MeSwitch value={row.key} current={me} onChange={(value) => {
          const picked = pickMe(rows, shown, names, me, value, meName, filled);
          setNames(picked.names);
          setFilled(picked.filled);
          setMe(value);
        }} />
      </Row>
    );
  }

  return (
    <Section note={`Голоса · ${voices.length}`}>
      {voices.length > 0 && (
        <Card>{voices.map((row, index) => line(row, `Голос ${index + 1}`, voiceMeta(row)))}</Card>
      )}
      {short.length > 0 && (
        <details className="group/short flex flex-col gap-3">
          <summary className="cursor-pointer list-none px-1 text-note text-text-secondary [&::-webkit-details-marker]:hidden">
            {`Короткие реплики · ${count(short.length, "номер", "номера", "номеров")}`}
            <span className="block text-micro text-text-tertiary">
              меньше 8 секунд речи — голос по ним не узнать; назовите, если важно
            </span>
          </summary>
          <Card>
            {short.map((row) => {
              const [part, speaker] = row.key.split(":");
              return line(row, `Говорящий ${Number(speaker) + 1}`, `ч.${part.padStart(2, "0")}`);
            })}
          </Card>
        </details>
      )}
      <div className="flex items-center gap-3 px-1">
        <Button variant="secondary" disabled={busy} onClick={save}>Сохранить</Button>
        <span className="text-note text-text-secondary">
          Голос собран из номеров, которыми его пометило распознавание; имя голоса
          получают все его номера.
          {rows.some((r) => r.auto) && " «По голосу» — догадка: исправьте имя или отметьте «это я» и сохраните — тогда это ваше слово."}
        </span>
      </div>
      {error ? <p className="rise-in px-1 text-note text-accent-text">{error}</p> : null}
    </Section>
  );
}
