"use client";

import { useState } from "react";
import { Button } from "@/components/base/buttons/button";
import { Input } from "@/components/base/input/input";
import { Radio, RadioGroup } from "@/components/base/radio/radio";
import { apiPost } from "@/lib/api";

type Speaker = { speaker: number; name: string; is_me: boolean };

export function SpeakersForm({ deviceId, session, speakers, onSaved }:
  { deviceId: string; session: number; speakers: Speaker[]; onSaved: () => void }) {
  const [names, setNames] = useState(() =>
    Object.fromEntries(speakers.map((s) => [String(s.speaker), s.name])));
  const [me, setMe] = useState(() => {
    const found = speakers.find((s) => s.is_me);
    return found ? String(found.speaker) : "";
  });
  const [busy, setBusy] = useState(false);

  if (speakers.length === 0) return null;

  async function save() {
    setBusy(true);
    try {
      await apiPost(`/sessions/${encodeURIComponent(deviceId)}/${session}/speakers`,
                    { names, me: me === "" ? null : Number(me) });
      onSaved();
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
      <RadioGroup className="mt-3 flex flex-col gap-3" value={me} onChange={setMe}
                  aria-label="Кто из говорящих — это я">
        {speakers.map((speaker) => (
          <div key={speaker.speaker} className="flex flex-wrap items-center gap-3">
            <span className="w-32 text-body-2-regular text-text-tertiary">
              Говорящий {speaker.speaker + 1}
            </span>
            <Input className="min-w-40 flex-1" placeholder="Имя"
                   value={names[String(speaker.speaker)] ?? ""}
                   onChange={(value: string) =>
                     setNames((old) => ({ ...old, [String(speaker.speaker)]: value }))} />
            <Radio value={String(speaker.speaker)}>это я</Radio>
          </div>
        ))}
        <Radio value="">меня нет</Radio>
      </RadioGroup>
      <Button className="mt-4" variant="primary" size="small" disabled={busy} onClick={save}>
        Сохранить
      </Button>
    </section>
  );
}
