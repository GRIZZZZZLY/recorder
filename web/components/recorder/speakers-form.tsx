"use client";

import { useState } from "react";
import { Card, Row, Section } from "@/components/recorder/list";
import { Button } from "@/components/recorder/ui";
import { apiPost } from "@/lib/api";

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

export function SpeakersForm({ deviceId, session, speakers, onSaved }:
  { deviceId: string; session: number; speakers: Speaker[]; onSaved: () => void }) {
  const [names, setNames] = useState(() =>
    Object.fromEntries(speakers.map((s) => [keyOf(s), s.name])));
  const [me, setMe] = useState(() => {
    const found = speakers.find((s) => s.is_me);
    return found ? keyOf(found) : "";
  });
  const manyParts = new Set(speakers.map((s) => s.part)).size > 1;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (speakers.length === 0) return null;

  async function save() {
    setBusy(true);
    try {
      await apiPost(`/sessions/${encodeURIComponent(deviceId)}/${session}/speakers`,
                    { names, me: me === "" ? null : me });
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
              <input
                value={name}
                placeholder={who}
                aria-label={manyParts ? `Имя: ${who}, часть ${speaker.part}` : `Имя: ${who}`}
                onChange={(event) =>
                  setNames((old) => ({ ...old, [key]: event.target.value }))}
                className="min-w-0 flex-1 rounded-inner bg-transparent px-2 py-1 text-row text-text-primary transition-colors placeholder:text-text-tertiary hover:bg-hairline max-md:min-h-11"
              />
              {speaker.auto && name === speaker.name && (
                <span className="shrink-0 text-micro text-text-tertiary"
                      title="Имя поставлено по сходству голоса с теми, кого вы уже называли. Неверно — исправьте или сотрите.">
                  по голосу
                </span>
              )}
              <MeSwitch value={key} current={me} onChange={setMe} />
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
          {speakers.some((s) => s.auto) && " «По голосу» — догадка: сохраните исправление, и она станет вашим именем."}
        </span>
      </div>
      {error ? <p className="rise-in px-1 text-note text-accent-text">{error}</p> : null}
    </Section>
  );
}
