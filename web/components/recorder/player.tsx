"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { Pause, Play } from "@phosphor-icons/react/dist/ssr";
import { Waveform } from "@/components/recorder/waveform";
import type { Mark } from "@/lib/api";
import { formatClock } from "@/lib/format";
import { cx } from "@/utils/cx";

type SeekFn = (part: number, seconds: number, label?: string) => void;

const SeekContext = createContext<SeekFn>(() => {});

/** Где сейчас звук. Нужен транскрипту, чтобы вести за собой реплику. */
export type Playhead = { part: number | null; position: number; playing: boolean };

const PlayheadContext = createContext<Playhead>({ part: null, position: 0, playing: false });

export function useSeek() {
  return useContext(SeekContext);
}

export function usePlayhead() {
  return useContext(PlayheadContext);
}

/** Скорости по кругу. Больше двух не нужно: речь выше ×2 не разбирается. */
const RATES = [1, 1.5, 2] as const;
const RATE_KEY = "recorder.rate";

/**
 * Плавающий плеер сессии — единственная карточка с тенью во всём интерфейсе.
 *
 * Один элемент <audio> на страницу: параллельно играющие части путают. Своя
 * кнопка и своя волна вместо `controls` — нативный виджет в тёмной теме
 * выглядит чужим, а главное, не умеет показывать, какая это часть из скольких.
 * Перемотка — пальцем по волне, а не только тычком в неё.
 *
 * Положение звука отдаётся страницей через `usePlayhead`: транскрипт подсвечивает
 * звучащую реплику и едет за ней. Контекстов два, а не один: `useSeek` меняется
 * редко, а положение — четыре раза в секунду, и подписчикам нужно разное.
 */
/** Часть, которую сервер принял: только у неё есть файл. */
export type PlayerPart = {
  part: number;
  /** Где часть начинается в разговоре, секунды — та же шкала, что у реплик. */
  offset: number;
  /** Длина части по размеру файла. */
  seconds: number;
  /** Громкость: волна плеера рисует ту часть, что играет. */
  envelope: number[];
};

export function PlayerProvider({ deviceId, session, parts, marks = [], children }: {
  deviceId: string;
  session: number;
  /** По возрастанию номера, как отдаёт сервер. */
  parts: PlayerPart[];
  /** Метки кнопкой: засечки на волне той части, которая сейчас открыта. */
  marks?: Mark[];
  children: React.ReactNode;
}) {
  // Play без выбранной реплики играет с начала первой принятой части — не
  // «части 1», которой могло не дойти, а той, что действительно лежит на сервере.
  const first = parts.length ? parts[0].part : null;
  const audio = useRef<HTMLAudioElement>(null);
  // Слушатель loadedmetadata предыдущей, ещё не догрузившейся части —
  // при быстром переключении отменяем его, иначе сработают все подряд.
  const pending = useRef<AbortController | null>(null);
  const [part, setPart] = useState<number | null>(null);
  const [label, setLabel] = useState<string>("");
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const [rate, setRate] = useState(1);

  // Скорость живёт между записями и между заходами: выбрал ×1.5 — слушаешь так
  // всё. Читаем после первой отрисовки, а не при инициализации состояния:
  // статический экспорт рисует страницу заранее, и разное значение на сервере и
  // в браузере ломало бы гидратацию.
  useEffect(() => {
    try {
      const saved = Number(window.localStorage.getItem(RATE_KEY));
      // Лишняя отрисовка при открытии здесь осознанная: прочитать хранилище
      // прямо в useState нельзя — статический экспорт рисует страницу заранее,
      // без хранилища, и значения на сервере и в браузере разошлись бы.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      if (RATES.includes(saved as (typeof RATES)[number])) setRate(saved);
    } catch {
      // Частное окно или запрет на хранилище: скорость просто не помнится.
    }
  }, []);

  // playbackRate сбрасывается на каждой новой части: держим его одним эффектом,
  // а не в обработчиках — ставить его приходилось бы в четырёх местах.
  useEffect(() => {
    if (audio.current) audio.current.playbackRate = rate;
  }, [rate, part]);

  function cycleRate() {
    const next = RATES[(RATES.indexOf(rate as (typeof RATES)[number]) + 1) % RATES.length];
    setRate(next);
    try {
      window.localStorage.setItem(RATE_KEY, String(next));
    } catch {
      // см. выше
    }
  }

  const seek = useCallback<SeekFn>((nextPart, seconds, nextLabel) => {
    const element = audio.current;
    if (!element) return;
    pending.current?.abort();
    if (nextLabel !== undefined) setLabel(nextLabel);
    if (part !== nextPart) {
      element.src = `/audio/${encodeURIComponent(deviceId)}/${session}/${nextPart}`;
      setPart(nextPart);
    }
    const start = () => {
      element.currentTime = seconds;
      setPosition(seconds);
      // Автовоспроизведение может быть запрещено браузером — тогда молча
      // ничего не происходит, если не сообщить об этом явно.
      element.play().then(() => setBlocked(false)).catch(() => setBlocked(true));
    };
    if (element.readyState >= 1) {
      start();
    } else {
      const controller = new AbortController();
      pending.current = controller;
      element.addEventListener("loadedmetadata", start, { once: true, signal: controller.signal });
    }
  }, [deviceId, session, part]);

  const value = useMemo(() => seek, [seek]);
  const playhead = useMemo<Playhead>(() => ({ part, position, playing }), [part, position, playing]);

  function toggle() {
    const element = audio.current;
    if (!element) return;
    if (part === null) {
      // Ничего не выбрано: клавиша Play запускает запись с самого начала.
      if (first !== null) seek(first, 0, "Запись разговора");
      return;
    }
    if (element.paused) element.play().then(() => setBlocked(false)).catch(() => setBlocked(true));
    else element.pause();
  }

  // Перемотка по волне: палец ведёт по ней, время под пальцем меняется сразу,
  // а сам звук прыгает один раз — на отпускании. Ставить currentTime на каждое
  // движение нельзя: каждая установка — новый Range-запрос к файлу.
  const scrubbing = useRef(false);

  function secondsAt(event: React.PointerEvent<HTMLDivElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    return Math.min(Math.max((event.clientX - box.left) / box.width, 0), 1) * duration;
  }

  function onScrubStart(event: React.PointerEvent<HTMLDivElement>) {
    if (idle || !duration) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    scrubbing.current = true;
    setPosition(secondsAt(event));
  }

  function onScrubMove(event: React.PointerEvent<HTMLDivElement>) {
    if (scrubbing.current) setPosition(secondsAt(event));
  }

  function onScrubEnd(event: React.PointerEvent<HTMLDivElement>) {
    if (!scrubbing.current) return;
    scrubbing.current = false;
    const element = audio.current;
    if (element) element.currentTime = secondsAt(event);
  }

  const idle = part === null;
  const current = parts.find((item) => item.part === (part ?? first));
  // Показание — время разговора, а не части: иначе «00:00:03» в плеере при
  // «00:00:42» у реплики, которую только что нажали. Волна и перемотка пока
  // остаются в пределах части.
  const total = Math.max(0, ...parts.map((item) => item.offset + item.seconds));

  // Конец части — не конец разговора: следующая часть начинается сама, иначе
  // совещание замолкало бы на каждом стыке файлов.
  function onEnded() {
    const next = parts.find((item) => part !== null && item.part > part);
    if (next) seek(next.part, 0);
    else setPlaying(false);
  }

  return (
    <SeekContext.Provider value={value}>
      <PlayheadContext.Provider value={playhead}>
        {/* Отступ снизу принадлежит плееру: иначе последняя реплика прячется
            под его карточкой. */}
        <div className="pb-32">{children}</div>

        {/* Корпус плеера, решётка динамика, размеры клавиш и отступы под
            телефон и десктоп — в styles/studio.css по хукам recorder-*. */}
        <div className="recorder-player player-in fixed z-20 flex items-center">
          <button
            type="button"
            onClick={toggle}
            disabled={idle && first === null}
            aria-label={playing ? "Пауза" : "Играть"}
            className={cx(
              "recorder-play-key press hit relative flex shrink-0 items-center justify-center rounded-chip bg-fill text-canvas disabled:opacity-40",
              // Клавиша выпуклая; нажатие коротко уводит её в поверхность.
              "shadow-key active:translate-y-px active:shadow-key-pressed",
              // Свечение — пока идёт звук; пауза гасит.
              playing && "shadow-key-lit",
            )}
          >
            {/* Обе иконки лежат друг на друге: смена play ↔ pause — кроссфейд за
                120 мс, а не подмена глифа между кадрами. */}
            <Pause size={14} weight="fill" aria-hidden
                   className={cx("absolute transition-opacity duration-120", playing ? "opacity-100" : "opacity-0")} />
            <Play size={14} weight="fill" aria-hidden
                  className={cx("absolute transition-opacity duration-120", playing ? "opacity-0" : "opacity-100")} />
          </button>

          <div className="recorder-player-body flex min-w-0 flex-1 flex-col gap-1.5">
            <div className="flex items-baseline justify-between gap-3 text-note">
              <span className="truncate">
                {idle
                  ? (first === null ? "Звук ещё не пришёл" : "Слушать запись")
                  : label || "Фрагмент записи"}
                {blocked ? <span className="text-text-secondary"> · браузер не разрешил автозапуск</span> : null}
              </span>
              <span className="flex shrink-0 items-baseline gap-1.5 text-text-secondary">
                {/* Части — внутренность прошивки (файл режется по 19,2 МБ). Номер
                    части показывается только когда их больше одной: иначе
                    «частей 1» и «01 / 01» — шум без единого сведения. */}
                {idle ? (parts.length > 1 ? `частей ${parts.length}` : null) : (
                  <>
                    {/* Показание прибора: одно крупное число — где сейчас звук.
                        Общая длительность и номер части мелко, на той же базовой линии. */}
                    <span className="font-mono text-[20px] leading-none text-text-primary lg:text-[22px]">
                      {formatClock((current?.offset ?? 0) + position)}
                    </span>
                    <span className="font-mono">/ {formatClock(total || duration)}</span>
                    {parts.length > 1 && (
                      <span className="hidden sm:inline">
                        · {String(part).padStart(2, "0")} / {String(parts.length).padStart(2, "0")}
                      </span>
                    )}
                  </>
                )}
              </span>
            </div>
            {/* Волна видна на 24 px, а под пальцем — 44 (`hit`). touch-pan-y: вести
                по ней вбок — перемотка, тянуть вверх — по-прежнему прокрутка страницы. */}
            <div
              onPointerDown={onScrubStart}
              onPointerMove={onScrubMove}
              onPointerUp={onScrubEnd}
              onPointerCancel={onScrubEnd}
              // Дорожка лежит в своей нише: у клавиши, таймера и звука разные роли.
              className={cx("recorder-player-wave hit relative flex items-center rounded-inner bg-well px-1.5 touch-pan-y select-none",
                            !idle && "cursor-pointer")}
            >
              {/* Пока ничего не выбрано, в нише лежит волна той части, которую
                  запустит Play: дисплей показывает, что будет звучать. */}
              <Waveform
                values={current?.envelope ?? []}
                height={idle ? 12 : 28}
                played={duration ? position / duration : 0}
                className="w-full"
              />
              {/* Засечки меток этой части: место, где владелец нажал кнопку. */}
              {duration > 0 && marks.filter((mark) => mark.part === part).map((mark) => (
                <span key={mark.offset} aria-hidden
                      className="absolute top-0 bottom-0 w-px bg-mark"
                      style={{ left: `${Math.min(100, (mark.offset / duration) * 100)}%` }} />
              ))}
            </div>
          </div>

          {/* Скорость: час совещания на ×2 — полчаса. Клавиша рядом с Play,
              потому что нажимают их одинаково часто. */}
          <button
            type="button"
            onClick={cycleRate}
            aria-label={`Скорость ${rate}×, нажмите чтобы сменить`}
            className={cx(
              "recorder-speed-key press hit relative flex h-9 shrink-0 items-center justify-center rounded-chip px-2.5 font-mono text-note lg:h-10",
              "shadow-key active:translate-y-px active:shadow-key-pressed",
              // Ускорение включено — клавиша светлая, как Play: состояние видно
              // с расстояния, а не только по цифре.
              rate === 1
                ? "bg-hairline text-text-secondary"
                : "is-active bg-fill text-canvas",
            )}
          >
            {rate}×
          </button>

          {/* preload="metadata", не "none": с "none" Chromium откладывает загрузку
              назначенного src до play(), и loadedmetadata, которого ждёт seek(),
              не приходит никогда — Play и реплики молчали. Пока src не назначен,
              ничего не грузится и так. */}
          <audio
            ref={audio}
            preload="metadata"
            onPlay={() => { setPlaying(true); setBlocked(false); }}
            onPause={() => setPlaying(false)}
            onEnded={onEnded}
            onLoadedMetadata={(event) => setDuration(event.currentTarget.duration || 0)}
            onTimeUpdate={(event) => { if (!scrubbing.current) setPosition(event.currentTarget.currentTime); }}
          />
        </div>
      </PlayheadContext.Provider>
    </SeekContext.Provider>
  );
}
