"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { ArrowUp, Pause, Play } from "@phosphor-icons/react/dist/ssr";
import { Waveform } from "@/components/recorder/waveform";
import type { Mark } from "@/lib/api";
import { audioSrc, loadedPart, sharedAudio, takeLabel } from "@/lib/audio";
import { formatClock } from "@/lib/format";
import { onReveal } from "@/lib/reveal";
import { cx } from "@/utils/cx";

/** `play: false` — только поставить звук на место («курсор»), не запуская:
 * так открывается страница по ссылке на реплику, Play начнёт оттуда. */
type SeekFn = (part: number, seconds: number, label?: string, play?: boolean) => void;

const SeekContext = createContext<SeekFn>(() => {});

/** Где сейчас звук. Нужен транскрипту, чтобы вести за собой реплику.
 * `position` — секунды внутри части: на этой шкале лежат `start` реплик. */
/** `level` — громкость в эту секунду (0..100) из огибающей файла: индикатор у
 * звучащей реплики показывает настоящий звук, а не анимацию. */
export type Playhead = { part: number | null; position: number; playing: boolean; label: string; level: number };

const PlayheadContext = createContext<Playhead>({ part: null, position: 0, playing: false, label: "", level: 0 });

export function useSeek() {
  return useContext(SeekContext);
}

export function usePlayhead() {
  return useContext(PlayheadContext);
}

/** Скорости по кругу. Больше двух не нужно: речь выше ×2 не разбирается. */
const RATES = [1, 1.5, 2] as const;
const RATE_KEY = "recorder.rate";

/** Шаг перемотки стрелками по волне; PageUp/PageDown — минута. */
const KEY_STEPS: Record<string, number> = {
  ArrowRight: 5, ArrowUp: 5, ArrowLeft: -5, ArrowDown: -5, PageUp: 60, PageDown: -60,
};

/** Общий звуковой элемент через ссылку плеера: первым может позвать кто
 * угодно — и эффект страницы, и эффект подключения. */
function attached(ref: React.RefObject<HTMLAudioElement | null>): HTMLAudioElement {
  if (!ref.current) ref.current = sharedAudio();
  return ref.current;
}

/** Часть, которую сервер принял: только у неё есть файл. */
export type PlayerPart = {
  part: number;
  /** Где часть начинается в разговоре, секунды — та же шкала, что у реплик. */
  offset: number;
  /** Длина части по размеру файла. */
  seconds: number;
  /** Громкость части; плеер склеивает их в одну волну разговора. */
  envelope: number[];
};

/**
 * Плавающий плеер записи — единственная карточка с тенью во всём интерфейсе.
 *
 * Свой вид вместо `controls`: нативный виджет в тёмной теме выглядит чужим.
 * Разговор для владельца один, даже если диктофон разрезал его на части:
 * часы, волна и перемотка — по всему разговору, а конец части сам запускает
 * следующую. Звуковой элемент общий на приложение (lib/audio.ts): так ▶ на
 * карточке записи начинает звук ещё до перехода сюда.
 *
 * Положение звука отдаётся страницей через `usePlayhead`: транскрипт подсвечивает
 * звучащую реплику и едет за ней. Контекстов два, а не один: `useSeek` меняется
 * редко, а положение — четыре раза в секунду, и подписчикам нужно разное.
 */
export function PlayerProvider({ deviceId, session, title, parts, marks = [], children }: {
  deviceId: string;
  session: number;
  /** Имя записи — подпись плеера, когда звук идёт не от конкретного пункта. */
  title: string;
  /** По возрастанию номера, как отдаёт сервер. */
  parts: PlayerPart[];
  /** Метки кнопкой: засечки на волне. */
  marks?: Mark[];
  children: React.ReactNode;
}) {
  // Play без выбранной реплики играет с начала первой принятой части — не
  // «части 1», которой могло не дойти, а той, что действительно лежит на сервере.
  const first = parts.length ? parts[0].part : null;
  const audio = useRef<HTMLAudioElement | null>(null);
  // Слушатель loadedmetadata предыдущей, ещё не догрузившейся части —
  // при быстром переключении отменяем его, иначе сработают все подряд.
  const pending = useRef<AbortController | null>(null);
  const [part, setPart] = useState<number | null>(null);
  const [label, setLabel] = useState<string>("");
  const [position, setPosition] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const [rate, setRate] = useState(1);
  // Секунда разговора под пальцем, пока идёт перемотка: время меняется сразу,
  // а звук прыгает один раз — на отпускании. Ставить currentTime на каждое
  // движение нельзя: каждая установка — новый Range-запрос к файлу.
  const [scrub, setScrub] = useState<number | null>(null);
  // Кнопка «основание», с которой ушли к реплике: чип «↑ к пункту» ведёт назад.
  const [returnTo, setReturnTo] = useState<HTMLElement | null>(null);

  const offsets = useMemo(() => new Map(parts.map((item) => [item.part, item.offset])), [parts]);
  const total = Math.max(0, ...parts.map((item) => item.offset + item.seconds));
  // Волна разговора — огибающие частей подряд. Часть без огибающей (старая
  // или нечитаемая) занимает своё место тишиной, иначе волна съехала бы.
  // ponytail: число столбиков на секунду у старых частей другое (2 с против
  // 0,5 с) — такая часть на волне уже своей длины; пересчитать огибающую из FLAC.
  const wave = useMemo(() => parts.flatMap((item) => item.envelope.length
    ? item.envelope : Array<number>(Math.max(1, Math.round(item.seconds * 2))).fill(0)), [parts]);

  const idle = part === null;
  const at = scrub ?? (idle ? 0 : (offsets.get(part) ?? 0) + position);

  // Скорость живёт между записями и между заходами: выбрал ×1.5 — слушаешь так
  // всё. Читаем после первой отрисовки, а не при инициализации состояния:
  // статический экспорт рисует страницу заранее, и разное значение на сервере и
  // в браузере ломало бы гидратацию.
  useEffect(() => {
    try {
      const saved = Number(window.localStorage.getItem(RATE_KEY));
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

  const seek = useCallback<SeekFn>((nextPart, seconds, nextLabel, play = true) => {
    // Страница зовёт seek из своего эффекта (ссылка на реплику) раньше, чем
    // сработает эффект подключения ниже: эффекты детей идут первыми.
    const element = attached(audio);
    pending.current?.abort();
    if (nextLabel !== undefined) setLabel(nextLabel);
    const src = audioSrc(deviceId, session, nextPart);
    const switching = !element.src.endsWith(src);
    if (switching) element.src = src;
    // Часть и место — сразу, не дожидаясь загрузки: иначе показание на
    // мгновение складывает новую часть со старым местом, и следующий шаг
    // перемотки считается от неправды.
    setPart(nextPart);
    setPosition(seconds);
    const start = () => {
      element.currentTime = seconds;
      setPosition(seconds);
      if (!play) return;
      // Автовоспроизведение может быть запрещено браузером — тогда молча
      // ничего не происходит, если не сообщить об этом явно.
      element.play().then(() => setBlocked(false)).catch(() => setBlocked(true));
    };
    if (!switching && element.readyState >= 1) {
      start();
    } else {
      const controller = new AbortController();
      pending.current = controller;
      element.addEventListener("loadedmetadata", start, { once: true, signal: controller.signal });
    }
  }, [deviceId, session]);

  // Где владелец остановился в этой записи: Play из покоя продолжает оттуда.
  // Хранилище браузера, не сервер: это привычка одного телефона, а не данные.
  const resumeKey = `recorder.pos.${deviceId}.${session}`;
  const [resumeAt, setResumeAt] = useState<number | null>(null);
  useEffect(() => {
    try {
      const saved = Number(window.localStorage.getItem(resumeKey));
      // eslint-disable-next-line react-hooks/set-state-in-effect -- хранилище читается только в браузере
      setResumeAt(Number.isFinite(saved) && saved > 5 ? saved : null);
    } catch {
      // Частное окно: место не помнится.
    }
  }, [resumeKey]);

  function remember(at: number) {
    try {
      window.localStorage.setItem(resumeKey, String(Math.floor(at)));
    } catch {
      // см. выше
    }
  }

  function forget() {
    setResumeAt(null);
    try {
      window.localStorage.removeItem(resumeKey);
    } catch {
      // см. выше
    }
  }

  // Секунда разговора → часть и место в ней.
  function jumpTo(target: number, play = audio.current ? !audio.current.paused : false) {
    if (!parts.length) return;
    const clamped = Math.min(Math.max(target, 0), Math.max(total - 0.25, 0));
    const found = [...parts].reverse().find((item) => item.offset <= clamped) ?? parts[0];
    const seconds = clamped - found.offset;
    const element = audio.current;
    if (element && found.part === part && element.readyState >= 1) {
      element.currentTime = seconds;
      setPosition(seconds);
    } else {
      seek(found.part, seconds, idle ? title : undefined, play);
    }
  }

  function toggle() {
    const element = audio.current;
    if (!element) return;
    if (idle) {
      // Ничего не выбрано: клавиша Play запускает запись с самого начала.
      // Ничего не выбрано, но владелец уже слушал эту запись — с того места.
      if (resumeAt !== null) jumpTo(resumeAt, true);
      else if (first !== null) seek(first, 0, title);
      return;
    }
    if (element.paused) element.play().then(() => setBlocked(false)).catch(() => setBlocked(true));
    else element.pause();
  }

  // Конец части — не конец разговора: следующая часть начинается сама, иначе
  // совещание замолкало бы на каждом стыке файлов. Подпись реплики, с которой
  // начали, к следующей части уже не относится.
  function onEnded() {
    const next = parts.find((item) => part !== null && item.part > part);
    if (next) seek(next.part, 0, title);
    else {
      setPlaying(false);
      forget();
    }
  }

  // Слушатели общего элемента живут дольше одной отрисовки, а обработчики
  // выше меняются на каждой: держим свежие в ссылках.
  const save = () => { if (!idle) remember(at); };
  const handlers = useRef({ onEnded, toggle, save });
  useEffect(() => {
    handlers.current = { onEnded, toggle, save };
  });

  // Раз в пять секунд игры место записывается: закрыли вкладку посреди
  // совещания — вернулись туда же.
  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => handlers.current.save(), 5000);
    return () => clearInterval(id);
  }, [playing]);

  // Подключение к общему звуковому элементу. Если ▶ на карточке уже запустил
  // эту запись, плеер подхватывает звук с того места, где он есть; звук чужой
  // записи останавливается. Уход со страницы глушит звук: играть без клавиш
  // паузы нельзя.
  useEffect(() => {
    const element = sharedAudio();
    audio.current = element;
    const loaded = loadedPart(element);
    const started = takeLabel();
    if (loaded && loaded.deviceId === deviceId && loaded.session === session) {
      // Подхватываем только звук, запущенный клавишей ▶ на карточке (у него
      // есть подпись). Курсор, который страница только что поставила на
      // реплику, и звук прошлого захода сюда не трогаем.
      if (started !== null) {
        /* eslint-disable react-hooks/set-state-in-effect -- подхват звука,
           запущенного до появления плеера: узнать о нём раньше нельзя. */
        setPart(loaded.part);
        setLabel(started);
        setPosition(element.currentTime);
        setPlaying(!element.paused);
        /* eslint-enable react-hooks/set-state-in-effect */
      }
    } else if (loaded) {
      element.pause();
      element.removeAttribute("src");
      element.load();
    }
    const onPlay = () => { setPlaying(true); setBlocked(false); };
    const onPause = () => { setPlaying(false); handlers.current.save(); };
    const onTime = () => setPosition(element.currentTime);
    const onEnd = () => handlers.current.onEnded();
    element.addEventListener("play", onPlay);
    element.addEventListener("pause", onPause);
    element.addEventListener("timeupdate", onTime);
    element.addEventListener("ended", onEnd);
    return () => {
      element.removeEventListener("play", onPlay);
      element.removeEventListener("pause", onPause);
      element.removeEventListener("timeupdate", onTime);
      element.removeEventListener("ended", onEnd);
      pending.current?.abort();
      element.pause();
    };
  }, [deviceId, session]);

  useEffect(() => onReveal(setReturnTo), []);

  // Читаешь вниз — плеер ужимается до строки (волна прячется), тянешь вверх —
  // возвращается целиком: плеер с панелью разделов занимали четверть экрана
  // телефона. На десктопе места хватает, там правило в CSS не действует.
  const [compact, setCompact] = useState(false);
  useEffect(() => {
    let last = window.scrollY;
    let frame = 0;
    function onScroll() {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const y = window.scrollY;
        if (Math.abs(y - last) < 12) return;
        setCompact(y > last && y > 200);
        last = y;
      });
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(frame);
    };
  }, []);

  // Чип живёт, пока пункт вне экрана: вернулся прокруткой — он больше не нужен.
  // Сразу после нажатия пункт ещё виден (страница только поехала), поэтому
  // гасим только после того, как он хоть раз ушёл с экрана.
  useEffect(() => {
    if (!returnTo) return;
    let away = false;
    const observer = new IntersectionObserver(([entry]) => {
      if (!entry.isIntersecting) away = true;
      else if (away) setReturnTo(null);
    });
    observer.observe(returnTo);
    return () => observer.disconnect();
  }, [returnTo]);

  function goBack() {
    if (!returnTo) return;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    returnTo.scrollIntoView({ behavior: still ? "auto" : "smooth", block: "center" });
    returnTo.focus({ preventScroll: true });
    setReturnTo(null);
  }

  // Пробел — пауза и продолжение с любого места страницы, кроме полей и
  // кнопок: у кнопки пробел — её собственное нажатие. Исключение — реплика
  // транскрипта: после нажатия на неё фокус остаётся на ней, и пробел
  // перематывал бы к её началу вместо паузы.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key !== " " || event.defaultPrevented || event.repeat) return;
      const target = event.target as HTMLElement | null;
      if (!target?.closest("[data-line]")
          && target?.closest("input, textarea, select, button, a, summary, [contenteditable='true']")) return;
      event.preventDefault();
      handlers.current.toggle();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  function secondsAt(event: React.PointerEvent<HTMLDivElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    return Math.min(Math.max((event.clientX - box.left) / box.width, 0), 1) * total;
  }

  function onScrubStart(event: React.PointerEvent<HTMLDivElement>) {
    if (!total) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    setScrub(secondsAt(event));
  }

  function onScrubMove(event: React.PointerEvent<HTMLDivElement>) {
    if (scrub !== null) setScrub(secondsAt(event));
  }

  function onScrubEnd(event: React.PointerEvent<HTMLDivElement>) {
    if (scrub === null) return;
    setScrub(null);
    jumpTo(secondsAt(event));
  }

  function onWaveKey(event: React.KeyboardEvent<HTMLDivElement>) {
    if (!total) return;
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      jumpTo(event.key === "Home" ? 0 : total);
      return;
    }
    if (event.key === "]" || event.key === "[") {
      // К началу реплики с меткой: нажатие кнопки приходится на середину фразы.
      const times = marks.map((mark) => mark.source ? mark.source.start_abs : (offsets.get(mark.part) ?? 0) + mark.offset)
        .sort((a, b) => a - b);
      const target = event.key === "]"
        ? times.find((time) => time > shown + 0.5)
        : [...times].reverse().find((time) => time < shown - 1);
      if (target === undefined) return;
      event.preventDefault();
      jumpTo(target);
      return;
    }
    const step = KEY_STEPS[event.key];
    if (step === undefined) return;
    event.preventDefault();
    jumpTo(shown + step);
  }

  const value = useMemo(() => seek, [seek]);
  const resuming = idle && scrub === null && resumeAt !== null;
  const shown = resuming ? resumeAt : at;
  const envelope = parts.find((item) => item.part === part)?.envelope;
  // Огибающая — пик на полсекунды (server/envelope.py): индекс — позиция / 0,5.
  const level = playing && envelope ? envelope[Math.floor(position / 0.5)] ?? 0 : 0;
  const playhead = useMemo<Playhead>(() => ({ part, position, playing, label, level }),
                                     [part, position, playing, label, level]);

  return (
    <SeekContext.Provider value={value}>
      <PlayheadContext.Provider value={playhead}>
        {/* Корпус плеера, решётка динамика, размеры клавиш и отступы под
            телефон и десктоп — в styles/studio.css по хукам recorder-*. */}
        {/* В DOM плеер стоит перед содержимым: с клавиатуры до него несколько
            нажатий Tab, а не сотни реплик часового разговора. На экране он
            по-прежнему внизу. */}
        <div className={cx("recorder-player player-in fixed z-20 flex items-center", compact && scrub === null && "is-compact")}>
          {/* «↑ к пункту» — над плеером, пока пункт, с которого ушли к
              реплике-основанию, вне экрана. */}
          <span className="recorder-keys-hint absolute -top-9 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-chip bg-surface-elevated px-3 py-1.5 text-note text-text-secondary shadow-plate">
            ← → 5 с · PgUp PgDn минута · [ ] метки · пробел пауза
          </span>
          {returnTo ? (
            <button type="button" onClick={goBack}
                    className="press hit absolute -top-12 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-chip bg-surface-elevated px-4 py-2 text-note text-text-primary shadow-plate hover:bg-hairline-strong">
              <ArrowUp size={14} aria-hidden /> к пункту
            </button>
          ) : null}

          <button
            type="button"
            onClick={toggle}
            disabled={idle && first === null}
            aria-label={playing ? "Пауза" : "Играть"}
            aria-keyshortcuts="Space"
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
                  ? (first === null ? "Звук ещё не пришёл"
                    : resumeAt !== null ? "Продолжить" : "Слушать запись")
                  : label || "Фрагмент записи"}
                {blocked ? <span className="text-text-secondary"> · браузер не разрешил автозапуск</span> : null}
              </span>
              <span className="flex shrink-0 items-baseline gap-1.5 text-text-secondary">
                {idle && scrub === null && !resuming ? (total ? <span className="font-mono">{formatClock(total)}</span> : null) : (
                  <>
                    {/* Показание прибора: одно крупное число — где сейчас звук
                        в разговоре. Длительность разговора мелко, на той же
                        базовой линии. */}
                    <span className="font-mono text-[20px] leading-none text-text-primary lg:text-[22px]">
                      {formatClock(shown)}
                    </span>
                    <span className="font-mono">/ {formatClock(total)}</span>
                  </>
                )}
              </span>
            </div>
            {/* Волна разговора — ползунок: пальцем вбок перемотка (touch-pan-y:
                тянуть вверх — по-прежнему прокрутка страницы), с клавиатуры —
                стрелки по 5 с, PageUp/PageDown по минуте, Home/End. Видна на
                24 px, под пальцем — 44 (`hit`). */}
            <div
              role="slider"
              tabIndex={total ? 0 : -1}
              aria-label="Место в разговоре"
              aria-valuemin={0}
              aria-valuemax={Math.round(total)}
              aria-valuenow={Math.round(shown)}
              aria-valuetext={`${formatClock(shown)} из ${formatClock(total)}`}
              aria-keyshortcuts="ArrowLeft ArrowRight PageUp PageDown Home End [ ]"
              title="← → 5 с · PageUp/PageDown — минута · [ ] — метки"
              onKeyDown={onWaveKey}
              onPointerDown={onScrubStart}
              onPointerMove={onScrubMove}
              onPointerUp={onScrubEnd}
              onPointerCancel={onScrubEnd}
              // Дорожка лежит в своей нише: у клавиши, таймера и звука разные роли.
              className={cx("recorder-player-wave hit relative flex items-center rounded-inner bg-well px-1.5 touch-pan-y select-none",
                            total > 0 && "cursor-pointer")}
            >
              <Waveform
                values={wave}
                height={idle && scrub === null && !resuming ? 12 : 28}
                played={total ? shown / total : 0}
                className="w-full"
              />
              {/* Засечки меток: место, где владелец нажал кнопку на диктофоне. */}
              {total > 0 && marks.map((mark) => (
                <span key={`${mark.part}-${mark.offset}`} aria-hidden
                      className="absolute top-0 bottom-0 w-px bg-mark"
                      style={{ left: `${Math.min(100, (((offsets.get(mark.part) ?? 0) + mark.offset) / total) * 100)}%` }} />
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
        </div>
        {/* Отступ снизу принадлежит плееру: иначе последняя реплика прячется
            под его карточкой. */}
        <div className="flex flex-col gap-[23px] pb-32 lg:gap-[30px]">{children}</div>

      </PlayheadContext.Provider>
    </SeekContext.Provider>
  );
}
