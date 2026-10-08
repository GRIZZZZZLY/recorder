"use client";

import { useState } from "react";
import { Card, Row, Section } from "@/components/recorder/list";
import { Button } from "@/components/recorder/ui";
import { apiPost } from "@/lib/api";
import { changedNames, isGuess, pickMe } from "@/lib/speakers";
import { cx } from "@/utils/cx";

type Speaker = { part: number; speaker: number; name: string; is_me: boolean; auto: boolean };

/** Ключ разметки. Номер говорящего принадлежит своей части: части уходят
 *  в распознавание поодиночке, и «первый» во второй части — уже другой голос. */
const keyOf = (s: { part: number; speaker: number }) => `${s.part}:${s.speaker}`;

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

export function SpeakersForm({ deviceId, session, speakers, meName, onSaved }:
  { deviceId: string; session: number; speakers: Speaker[]; meName: string; onSaved: () => void }) {
  // То, что страница показала при загрузке: сравниваем с ним, а не шлём всё.
  // Форма пересоздаётся при каждом новом списке говорящих (key в page.tsx).
  const [shown] = useState(() =>
    Object.fromEntries(speakers.map((s) => [keyOf(s), s.name])));
  const [shownMe] = useState(() => {
    const found = speakers.find((s) => s.is_me);
    return found ? keyOf(found) : "";
  });
  const [names, setNames] = useState(shown);
  const [me, setMe] = useState(shownMe);
  const manyParts = new Set(speakers.map((s) => s.part)).size > 1;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (speakers.length === 0) return null;

  async function save() {
    setBusy(true);
    try {
      await apiPost(`/sessions/${encodeURIComponent(deviceId)}/${session}/speakers`,
                    { names: changedNames(shown, names, shownMe, me), me: me === "" ? null : me });
      setError(null);
      onSaved();
    } catch (err) {
      setError(`не удалось сохранить: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section note={`Говорящие · ${speakers.length}`}>
      <Card>
        {speakers.map((speaker) => {
          const key = keyOf(speaker);
          const name = names[key] ?? "";
          const who = `Говорящий ${speaker.speaker + 1}`;
          const guess = isGuess(speaker, name, me);
          return (
            <Row key={key}>
              <span aria-hidden
                    className="flex size-7 shrink-0 items-center justify-center rounded-chip bg-surface-elevated text-micro text-text-secondary">
                {name.trim() ? name.trim()[0].toUpperCase() : speaker.speaker + 1}
              </span>
              {manyParts && (
                <span className="shrink-0 font-mono text-micro text-text-tertiary"
                      title="номер части: нумерация голосов в каждой части своя">
                  ч.{String(speaker.part).padStart(2, "0")}
                </span>
              )}
              {/* Пометка лежит поверх поля, а не рядом: иначе поля в строках
                  с догадкой и без неё разной ширины, и столбец имён пляшет. */}
              <div className="relative min-w-0 flex-1">
                <input
                  value={name}
                  placeholder={who}
                  aria-label={`Имя: ${who}${manyParts ? `, часть ${speaker.part}` : ""}${guess ? ", угадано по голосу" : ""}`}
                  title={guess ? "Имя поставлено по сходству голоса с теми, кого вы уже называли. Неверно — исправьте или сотрите." : undefined}
                  onChange={(event) =>
                    setNames((old) => ({ ...old, [key]: event.target.value }))}
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
              <MeSwitch value={key} current={me} onChange={(value) => {
                setNames((old) => pickMe(speakers, shown, old, me, value, meName));
                setMe(value);
              }} />
            </Row>
          );
        })}
      </Card>
      <div className="flex items-center gap-3 px-1">
        <Button variant="secondary" disabled={busy} onClick={save}>Сохранить</Button>
        <span className="text-note text-text-secondary">
          {manyParts
            ? "Номера — метки диаризации внутри своей части, между частями не совпадают."
            : "Номера — метки диаризации, транскрипт не меняется."}
          {speakers.some((s) => s.auto) && " «По голосу» — догадка: исправьте имя или отметьте «это я» и сохраните — тогда это ваше слово."}
        </span>
      </div>
      {error ? <p className="rise-in px-1 text-note text-accent-text">{error}</p> : null}
    </Section>
  );
}
