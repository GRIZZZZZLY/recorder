"use client";

import { memo, useCallback, useEffect, useMemo, useRef } from "react";
import { BookmarkSimple } from "@phosphor-icons/react/dist/ssr";
import { Card, Section, Time } from "@/components/recorder/list";
import { usePlayhead, useSeek } from "@/components/recorder/player";
import type { Utterance } from "@/lib/api";
import { formatClock } from "@/lib/format";
import { cx } from "@/utils/cx";

/** Индикатор уровня у звучащей реплики: три столбика по настоящей громкости
 * из огибающей файла — как стрелка VU на приборе, только честная. */
function LevelMeter({ level }: { level: number }) {
  const heights = [0.55, 1, 0.75].map((k) => Math.max(3, Math.round((level / 100) * 12 * k)));
  return (
    <span aria-hidden className="ml-1.5 inline-flex h-3 items-end gap-px">
      {heights.map((height, index) => (
        <span key={index} className="w-[3px] rounded-[1px] bg-fill transition-[height] duration-150"
              style={{ height }} />
      ))}
    </span>
  );
}

/** Оттенки подписей говорящих: голоса различимы на глаз, как в «Кто сколько
 * говорил». Все ступени — не ниже 4,5:1 на поверхности. */
const VOICE_SHADES = ["text-text-primary", "text-[#d6ccb8]", "text-text-secondary", "text-text-tertiary"];

/**
 * Строка транскрипта. Вынесена и запомнена (`memo`) не ради красоты: положение
 * звука приходит четыре раза в секунду, и без этого на каждом такте
 * перерисовывались бы все реплики часового разговора, а не две изменившиеся.
 */
const Line = memo(function Line({ utterance, marked, active, level, shade, onPlay }: {
  utterance: Utterance;
  marked: boolean;
  active: boolean;
  /** Оттенок подписи говорящего. */
  shade: string;
  /** Громкость в эту секунду — только у звучащей реплики, пока идёт звук. */
  level?: number;
  onPlay: (utterance: Utterance) => void;
}) {
  const id = `u-${utterance.part}-${utterance.idx}`;
  return (
    <button
      id={id}
      type="button"
      data-line
      aria-label={`${formatClock(utterance.start_abs)}, ${utterance.label}${utterance.by_voice ? " (по голосу)" : ""}: ${utterance.text}`}
      onClick={() => onPlay(utterance)}
      className={cx(
        // scroll-my-24: якорь иначе прижимает реплику к самому
        // верху окна, под шапку страницы.
        "press-row grid w-full scroll-my-24 grid-cols-[76px_1fr] gap-x-3 px-4 py-2 text-left",
        "hover:bg-surface-elevated sm:grid-cols-[76px_90px_1fr]",
        // Звучит прямо сейчас: подложка и светлый текст. Полоска слева — там же,
        // где у реплики-основания вспышка, поэтому цвет другой, светлый.
        // Звучит — горит: светится то, что живо. Раньше полоска в 2 px и
        // бледная подложка терялись, и самый живой момент был почти не виден.
        active && "bg-surface-elevated shadow-[inset_3px_0_0_var(--color-fill),inset_0_0_28px_rgb(217_211_199/0.09)]",
      )}
      aria-current={active ? "true" : undefined}
    >
      <Time className={cx(marked && "flex items-center gap-1.5", (marked || active) && "text-text-primary")}>
        {marked && (
          <BookmarkSimple size={12} weight="fill" aria-label="отмечено кнопкой" className="text-mark" />
        )}
        {formatClock(utterance.start_abs)}
        {level !== undefined && level >= 8 ? <LevelMeter level={level} /> : null}
      </Time>
      {/* Звёздочка, а не слово: колонка имён узкая, и подпись «по голосу»
          съела бы имя. Звёздочка вне обрезки — длинное имя её не скроет;
          объяснение — строкой над транскриптом (подсказки на телефоне нет). */}
      <span className={cx("flex min-w-0 text-note", shade)}
            title={utterance.by_voice ? "Распознавание записало фразу на другой номер; имя поставлено по голосу самой фразы." : undefined}>
        <span className="truncate">{utterance.label}</span>
        {utterance.by_voice ? <span aria-hidden className="shrink-0 text-text-tertiary">*</span> : null}
      </span>
      <span className={cx("col-span-2 text-row leading-relaxed sm:col-span-1",
                          active && "text-text-primary")}>
        {utterance.text}
      </span>
    </button>
  );
});

/** Весь разговор, сгруппированный по частям; едет за звуком. Общий для
 * страницы записи и страницы гостя (/shared/). */
export function Transcript({ timeline, marked, received }: {
  timeline: Utterance[];
  marked: Set<string>;
  /** Номера пришедших частей: пропуск в транскрипте — либо не пришла, либо
   * пришла, но не расшифровалась. */
  received: Set<number>;
}) {
  const seek = useSeek();
  const { part, position, playing, level } = usePlayhead();
  // Группировка заранее: заголовок части ставится по данным, а не по счётчику,
  // который менялся бы прямо во время отрисовки.
  // Оттенок — по порядку первого появления голоса в разговоре.
  const shades = useMemo(() => {
    const order: string[] = [];
    for (const item of timeline) if (!order.includes(item.label)) order.push(item.label);
    return new Map(order.map((label, index) => [label, VOICE_SHADES[index % VOICE_SHADES.length]]));
  }, [timeline]);

  const byPart = useMemo(() => {
    const groups: { part: number; items: Utterance[] }[] = [];
    for (const item of timeline) {
      const last = groups[groups.length - 1];
      if (last && last.part === item.part) last.items.push(item);
      else groups.push({ part: item.part, items: [item] });
    }
    return groups;
  }, [timeline]);

  // Звучащая реплика. Между репликами бывают паузы, и на них подсветка не
  // гаснет: держим последнюю начавшуюся, иначе она мигала бы на каждом вдохе.
  const activeId = useMemo(() => {
    if (part === null) return null;
    let found: Utterance | null = null;
    for (const item of timeline) {
      if (item.part !== part || item.start > position + 0.25) continue;
      found = item;
    }
    return found ? `u-${found.part}-${found.idx}` : null;
  }, [timeline, part, position]);

  // Транскрипт едет за звуком, но только пока звук идёт, только если реплика
  // ушла с экрана и только если владелец смотрит на транскрипт: предыдущая
  // звучавшая реплика была на экране (или, в самом начале, сам транскрипт).
  // Читаешь саммари под звук — страница стоит; раньше её уносило на 1900 px.
  const followed = useRef<string | null>(null);
  useEffect(() => {
    if (!playing || !activeId || followed.current === activeId) return;
    const previous = followed.current ? document.getElementById(followed.current) : document.getElementById("transcript");
    followed.current = activeId;
    const element = document.getElementById(activeId);
    if (!element) return;
    const box = element.getBoundingClientRect();
    if (box.top >= 80 && box.bottom <= window.innerHeight - 160) return;
    const seen = previous?.getBoundingClientRect();
    if (!seen || seen.bottom < 0 || seen.top > window.innerHeight) return;
    element.scrollIntoView({
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      block: "center",
    });
  }, [activeId, playing]);

  const play = useCallback((utterance: Utterance) => {
    seek(utterance.part, utterance.start, `${utterance.label} · ${formatClock(utterance.start_abs)}`);
  }, [seek]);

  return (
    <Section id="transcript" note="Транскрипт" aside="нажмите реплику, чтобы послушать">
      <div className="flex flex-col gap-4">
        {timeline.some((u) => u.by_voice) && (
          <p className="px-1 text-note text-text-secondary">
            * — имя поставлено по голосу самой фразы: распознавание записало её на номер другого человека.
          </p>
        )}
        {byPart.map((group, index) => (
          <div key={group.part} className="flex flex-col gap-1.5">
            {byPart.length > 1 && (
              <span className="px-1 font-mono text-micro text-text-tertiary">
                часть {String(group.part).padStart(2, "0")}
                {/* Пропуск между частями — потерянный звук: называем его здесь,
                    а не только в ошибках наверху. */}
                {index > 0 && group.part - byPart[index - 1].part > 1 ? (
                  <span className="font-sans text-warn">
                    {" · "}
                    {Array.from({ length: group.part - byPart[index - 1].part - 1 },
                                (_, i) => byPart[index - 1].part + 1 + i)
                      .map((n) => `часть ${String(n).padStart(2, "0")} ${received.has(n) ? "не расшифровалась" : "не пришла"}`)
                      .join(", ")}
                  </span>
                ) : null}
              </span>
            )}
            <Card className="bg-well py-1.5 shadow-well">
              {group.items.map((utterance) => {
                const id = `u-${utterance.part}-${utterance.idx}`;
                return (
                  <Line key={id} utterance={utterance} marked={marked.has(id)}
                        active={id === activeId} level={id === activeId && playing ? level : undefined}
                        shade={shades.get(utterance.label) ?? "text-text-secondary"}
                        onPlay={play} />
                );
              })}
            </Card>
          </div>
        ))}
      </div>
    </Section>
  );
}
