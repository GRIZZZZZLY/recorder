"use client";

import { useState } from "react";
import { Button, Input } from "@/components/recorder/ui";
import { apiPost } from "@/lib/api";

type Speaker = { speaker: number; name: string; is_me: boolean };

/** Выбор «это я»: нативная радиокнопка с подписью-обёрткой. */
function Me({ value, current, onChange, children }:
  { value: string; current: string; onChange: (value: string) => void; children: React.ReactNode }) {
  return (
    <label className="flex items-center gap-2 text-body-regular text-text-secondary">
      <input
        type="radio"
        name="me"
        value={value}
        checked={current === value}
        onChange={() => onChange(value)}
        className="size-4 accent-accent-500"
      />
      {children}
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
    <section className="mt-8">
      <h2 className="text-title-3-semibold text-text-primary">Говорящие</h2>
      <p className="mt-1 text-body-2-regular text-text-tertiary">
        Номера — метки диаризации, не личности. Исходный транскрипт не меняется.
      </p>
      <fieldset className="mt-3 flex flex-col gap-3">
        <legend className="sr-only">Кто из говорящих — это я</legend>
        {speakers.map((speaker) => (
          <div key={speaker.speaker} className="flex flex-wrap items-center gap-3">
            <span className="w-32 text-body-2-regular text-text-tertiary">
              Говорящий {speaker.speaker + 1}
            </span>
            <Input className="min-w-40 flex-1" placeholder="Имя"
                   value={names[String(speaker.speaker)] ?? ""}
                   onChange={(value: string) =>
                     setNames((old) => ({ ...old, [String(speaker.speaker)]: value }))} />
            <Me value={String(speaker.speaker)} current={me} onChange={setMe}>это я</Me>
          </div>
        ))}
        <Me value="" current={me} onChange={setMe}>меня нет</Me>
      </fieldset>
      <Button className="mt-4" variant="primary" disabled={busy} onClick={save}>
        Сохранить
      </Button>
      {error && (
        <p className="mt-2 text-body-2-regular text-text-error-primary">{error}</p>
      )}
    </section>
  );
}
