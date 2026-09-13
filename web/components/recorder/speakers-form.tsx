"use client";

import { useState } from "react";
import { Card, Row, Section } from "@/components/recorder/list";
import { Button } from "@/components/recorder/ui";
import { apiPost } from "@/lib/api";

type Speaker = { speaker: number; name: string; is_me: boolean };

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
    <label className="flex shrink-0 cursor-pointer items-center gap-2.5">
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
    Object.fromEntries(speakers.map((s) => [String(s.speaker), s.name])));
  const [me, setMe] = useState(() => {
    const found = speakers.find((s) => s.is_me);
    return found ? String(found.speaker) : "";
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (speakers.length === 0) return null;

  async function save() {
    setBusy(true);
    try {
      await apiPost(`/sessions/${encodeURIComponent(deviceId)}/${session}/speakers`,
                    { names, me: me === "" ? null : Number(me) });
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
          const name = names[String(speaker.speaker)] ?? "";
          return (
            <Row key={speaker.speaker}>
              <span aria-hidden
                    className="flex size-7 shrink-0 items-center justify-center rounded-chip bg-surface-elevated text-micro text-text-secondary">
                {name.trim() ? name.trim()[0].toUpperCase() : speaker.speaker + 1}
              </span>
              <input
                value={name}
                placeholder={`Говорящий ${speaker.speaker + 1}`}
                aria-label={`Имя говорящего ${speaker.speaker + 1}`}
                onChange={(event) =>
                  setNames((old) => ({ ...old, [String(speaker.speaker)]: event.target.value }))}
                className="min-w-0 flex-1 rounded-inner bg-transparent px-2 py-1 text-row text-text-primary transition-colors placeholder:text-text-tertiary hover:bg-hairline"
              />
              <MeSwitch value={String(speaker.speaker)} current={me} onChange={setMe} />
            </Row>
          );
        })}
      </Card>
      <div className="flex items-center gap-3 px-1">
        <Button variant="secondary" disabled={busy} onClick={save}>Сохранить</Button>
        <span className="text-note text-text-secondary">
          Номера — метки диаризации, транскрипт не меняется.
        </span>
      </div>
      {error ? <p className="rise-in px-1 text-note text-accent-text">{error}</p> : null}
    </Section>
  );
}
