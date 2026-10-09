"use client";

import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { BookmarkSimple, CaretDown, Trash } from "@phosphor-icons/react/dist/ssr";
import { Card, Row, Section } from "@/components/recorder/list";
import { PageHeader } from "@/components/recorder/page-header";
import { Transcript } from "@/components/recorder/transcript";
import { PlayerProvider, useSeek } from "@/components/recorder/player";
import { ShareLinks } from "@/components/recorder/share-links";
import { SpeakersForm } from "@/components/recorder/speakers-form";
import { SummaryList } from "@/components/recorder/summary-list";
import { TaskRow } from "@/components/recorder/task-row";
import { Empty, EmptyNote, Failed, Loading } from "@/components/recorder/states";
import { Button, Dot, EvidenceTime, Retry, SourceNote, Tag } from "@/components/recorder/ui";
import { useApi } from "@/lib/use-api";
import { revealUtterance } from "@/lib/reveal";
import { groupRows, nameCounts, withVoiceLabels } from "@/lib/speakers";
import { statusTone } from "@/lib/session-status";
import { minutesSince, useNow } from "@/lib/use-now";
import { ApiError, apiDelete, apiPost } from "@/lib/api";
import type { Mark, Question, Summary, Task, Utterance } from "@/lib/api";
import { count, formatClock, formatDay, formatTime, humanError, keepNumbers, normalizeClock, recordTitle, todayIso } from "@/lib/format";
import { cx } from "@/utils/cx";

type Part = {
  part: number;
  recovered: number;
  /** Когда часть пришла на сервер: по последней видно, стоит ли обработка. */
  received_at: string;
  transcript_status: string | null;
  last_error: string | null;
  /** Громкость части: считается сервером из файла при приёме. */
  envelope: number[];
  /** Начало части в разговоре и её длина, секунды — для часов плеера. */
  offset: number;
  seconds: number;
};

type Detail = {
  device_id: string;
  session: number;
  day: string;
  first_at: string | null;
  parts: Part[];
  summary: Summary | null;
  summary_status: string | null;
  summary_error: string | null;
  timeline: Utterance[];
  marks: Mark[];
  speakers: { part: number; speaker: number; name: string; is_me: boolean; auto: boolean }[];
  /** Как владелец пишет своё имя (по строкам «это я» во всём архиве); пусто — не отмечал. */
  me_name: string;
  /** Номера («часть:номер»), собранные по голосу, крупные первыми. */
  voice_groups: { keys: string[]; seconds: number; hint?: { name: string; score: number } | null }[];
  /** Номера без отпечатка голоса и без имени: меньше 3 секунд речи. */
  short_keys: string[];
  tasks: Task[];
  questions: Question[];
};

/** Части записи по порядку номеров: каждая подписана словами, а не только
 * цветом, пропавшая — пустым сегментом. */
const PART_STATE: Record<string, { text: string; bar: string }> = {
  done: { text: "готова", bar: "bg-fill" },
  failed: { text: "ошибка", bar: "bg-accent" },
  missing: { text: "не пришла", bar: "border border-dashed border-text-tertiary" },
  pending: { text: "в очереди", bar: "bg-waveform" },
};

function Parts({ parts, missing }: { parts: Part[]; missing: number[] }) {
  const rows = [
    ...parts.map((part) => ({ part: part.part, state: part.transcript_status === "done" ? "done"
      : part.transcript_status === "failed" ? "failed" : "pending" })),
    ...missing.map((number) => ({ part: number, state: "missing" })),
  ].sort((a, b) => a.part - b.part);
  return (
    <Section note={`Части · ${rows.length}`}>
      <div className="flex gap-2 rounded-card bg-surface shadow-plate px-4 py-3.5">
        {rows.map((row) => (
          <span key={row.part} className="flex min-w-0 flex-1 flex-col gap-1.5">
            <span aria-hidden className={cx("h-1.5 rounded-sm", PART_STATE[row.state].bar)} />
            <span className="truncate text-micro text-text-secondary">
              <span className="font-mono">{String(row.part).padStart(2, "0")}</span> {PART_STATE[row.state].text}
            </span>
          </span>
        ))}
      </div>
    </Section>
  );
}

/**
 * День разговора. По умолчанию это день выгрузки первой части: часов у
 * диктофона нет. Запись, сделанная в понедельник и выгруженная в среду,
 * превращает «сделаю завтра» в четверг — а от дня разговора считаются все
 * сроки задач. Поэтому дату можно поправить; сроки пересчитываются сразу.
 */
/** Удаление без корзины: сервер стирает звук и всё, что из него выросло.
 * Поэтому два нажатия, и второе — на кнопке, которая говорит, что сотрётся. */
function DeleteSession({ deviceId, session }: { deviceId: string; session: number }) {
  const router = useRouter();
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function remove() {
    setBusy(true);
    try {
      await apiDelete(`/sessions/${encodeURIComponent(deviceId)}/${session}`);
      // replace, а не push: «назад» не должен вести на стёртую запись.
      router.replace("/records/");
    } catch (err) {
      setError(`не удалось удалить: ${(err as Error).message}`);
      setBusy(false);
    }
  }

  if (!asking) {
    return (
      <Button variant="ghost" icon={Trash} className="self-start" onClick={() => setAsking(true)}>
        Удалить запись
      </Button>
    );
  }
  return (
    <Section note="Удалить запись">
      <Card>
        <Row className="flex-col items-start gap-3">
          <span className="text-note text-text-secondary">
            Звук, расшифровка, саммари и задачи этой записи сотрутся. Вернуть их будет нельзя.
          </span>
          <span className="flex flex-wrap gap-2">
            <Button variant="primary" disabled={busy} onClick={remove}>
              {busy ? "Удаляю…" : "Удалить навсегда"}
            </Button>
            <Button variant="ghost" disabled={busy} onClick={() => { setAsking(false); setError(null); }}>
              Отмена
            </Button>
          </span>
        </Row>
      </Card>
      {error ? <p className="rise-in px-1 text-note text-accent-text">{error}</p> : null}
    </Section>
  );
}

function RecordedOn({ deviceId, session, day, onSaved }: {
  deviceId: string;
  session: number;
  day: string;
  onSaved: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Сохранение — когда владелец закончил: при наборе с клавиатуры каждое
  // изменение поля отправлялось бы на сервер и пересчитывало сроки задач.
  const [value, setValue] = useState(day);

  async function save(value: string) {
    if (!value || value === day) return;
    setBusy(true);
    try {
      await apiPost(`/sessions/${encodeURIComponent(deviceId)}/${session}/day`, { day: value });
      setError(null);
      onSaved();
    } catch (err) {
      setError(`не удалось сохранить: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section note="День разговора">
      <Card>
        <Row>
          {/* Нативный выбор даты: календарь телефона и клавиатурный ввод
              бесплатно, и он уже знает про тёмную тему страницы. */}
          <input
            type="date"
            max={todayIso()}
            disabled={busy}
            aria-label="День разговора"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onBlur={() => save(value)}
            onKeyDown={(event) => { if (event.key === "Enter") save(value); }}
            className="min-w-0 flex-1 rounded-inner bg-transparent px-2 py-1 text-row text-text-primary transition-colors hover:bg-hairline max-md:min-h-11"
          />
        </Row>
      </Card>
      <p className="px-1 text-note text-text-secondary">
        По умолчанию — день выгрузки. От него считаются сроки: «в пятницу» из понедельника
        и из среды — разные даты.
      </p>
      {error ? <p className="rise-in px-1 text-note text-accent-text">{error}</p> : null}
    </Section>
  );
}

/**
 * Кто сколько говорил. Считается из шкалы реплик, без сервера и без единого
 * нового поля: у каждой реплики есть начало и конец.
 *
 * Это первый кусок речевых метрик, и намеренно самый скромный: доля времени —
 * факт, его видно в данных. Оценок «много перебивал», «говорил слишком долго»
 * здесь нет и не будет без отдельного разговора.
 */
function SpeechShare({ timeline }: { timeline: Utterance[] }) {
  const shares = useMemo(() => {
    const seconds = new Map<string, number>();
    for (const item of timeline) {
      seconds.set(item.label, (seconds.get(item.label) ?? 0) + Math.max(0, item.end - item.start));
    }
    const total = [...seconds.values()].reduce((sum, value) => sum + value, 0);
    if (!total) return [];
    return [...seconds.entries()]
      .map(([label, value]) => ({ label, seconds: value, share: value / total }))
      .sort((a, b) => b.seconds - a.seconds);
  }, [timeline]);

  if (shares.length < 2) return null;

  // Ступени серого вместо цветов: красный в этом интерфейсе означает «запись
  // и внимание», раздавать его говорящим нельзя.
  const TONES = ["bg-fill", "bg-waveform", "bg-control", "bg-hairline-strong"];

  return (
    <Section note="Кто сколько говорил">
      <Card className="flex flex-col gap-3 p-4">
        <div className="flex h-2 gap-px overflow-hidden rounded-chip">
          {shares.map((item, index) => (
            <span key={item.label} className={TONES[index % TONES.length]}
                  style={{ width: `${item.share * 100}%` }} />
          ))}
        </div>
        <div className="flex flex-col gap-1.5">
          {shares.map((item, index) => (
            <div key={item.label} className="flex items-center gap-2.5 text-note">
              <span aria-hidden className={cx("size-2 shrink-0 rounded-chip shadow-lens",
                                              TONES[index % TONES.length])} />
              <span className="min-w-0 flex-1 truncate">{item.label}</span>
              <span className="shrink-0 font-mono text-text-secondary">{formatClock(item.seconds)}</span>
              <span className="w-10 shrink-0 text-right font-mono text-text-primary">
                {Math.round(item.share * 100)}%
              </span>
            </div>
          ))}
        </div>
      </Card>
    </Section>
  );
}

/** Содержимое страницы. Живёт внутри плеера: перемотка нужна всем секциям. */
function Content({ data, reload }: { data: Detail; reload: () => void }) {
  const seek = useSeek();
  const now = useNow();

  // Ссылки на реплику-основание ведут на #u-{часть}-{индекс}: элемент с этим
  // id появляется в DOM только после загрузки данных, а браузер уже
  // попытался прокрутить к фрагменту при открытии страницы. Дублируем
  // прокрутку вручную один раз, когда данные готовы.
  //
  // ?t= — секунда сессии: так открывает лента дня, когда головка стоит внутри
  // записи. Точной перемотки по секунде нет, и выдумывать её незачем: ведём на
  // реплику, которая в этот момент звучала.
  const scrolled = useRef(false);
  const jump = useSearchParams().get("t");
  useEffect(() => {
    if (scrolled.current) return;
    scrolled.current = true;
    const parsed = /^u-(\d+)-(\d+)$/.exec(window.location.hash.slice(1));
    if (parsed) {
      const part = Number(parsed[1]);
      const idx = Number(parsed[2]);
      // Пришли на реплику (из поиска, задачи, спорного места): звук встаёт на
      // неё же, Play начнёт отсюда, а не с начала записи.
      const cue = data.timeline.find((u) => u.part === part && u.idx === idx);
      if (cue) seek(cue.part, cue.start, `${cue.label} · ${formatClock(cue.start_abs)}`, false);
      revealUtterance(part, idx);
      return;
    }
    const seconds = Number(jump);
    if (!jump || !Number.isFinite(seconds)) return;
    const before = data.timeline.filter((u) => u.start_abs <= seconds + 0.5);
    const target = before[before.length - 1] ?? data.timeline[0];
    if (!target) return;
    seek(target.part, target.start, `${target.label} · ${formatClock(target.start_abs)}`, false);
    revealUtterance(target.part, target.idx);
  }, [data.timeline, jump, seek]);

  // Реплики, на которых стоит метка кнопкой: флажок в транскрипте.
  const marked = new Set(
    data.marks.filter((mark) => mark.source)
      .map((mark) => `u-${mark.source!.part}-${mark.source!.idx}`),
  );

  // Длительность — по размеру файлов, как у плеера: последняя реплика
  // кончается раньше записи, и шапка с плеером показывали бы разное.
  const duration = data.parts.length > 0
    ? Math.max(...data.parts.map((part) => part.offset + part.seconds))
    : null;
  const failedParts = data.parts.filter((part) => part.transcript_status === "failed");
  // Та же функция, что у карточки в списке: иначе список писал «ошибка», а
  // шапка той же записи — «обрабатывается».
  const tone = statusTone({ summary_status: data.summary_status, failed_parts: failedParts.length });
  // Больше часа без новых частей — уже «ждёт с …», а не «обрабатывается».
  const lastPartAt = data.parts.reduce<string | null>((latest, part) =>
    !latest || part.received_at > latest ? part.received_at : latest, null);
  const stalled = tone === "warn" && minutesSince(lastPartAt, now) > 60;
  const toneText = tone === "ok" ? "саммари готово"
    : tone === "warn" ? (minutesSince(lastPartAt, now) > 60 ? `ждёт с ${formatDay(data.day).replace(/ \d{4}$/, "")}` : "обрабатывается")
    : failedParts.length ? "часть не расшифровалась" : "саммари не собралось";
  // Номера частей, которые так и не пришли с диктофона: между первой и
  // последней принятой. Потерянное время иначе прячется в шкале.
  const numbers = data.parts.map((part) => part.part);
  const missing = numbers.length
    ? Array.from({ length: Math.max(...numbers) - Math.min(...numbers) + 1 }, (_, i) => Math.min(...numbers) + i)
        .filter((n) => !numbers.includes(n))
    : [];
  const topic = data.summary?.topics?.[0];
  const when = data.first_at ? `${formatDay(data.day)}, ${formatTime(data.first_at)}` : null;
  const openQuestionsForNav = data.questions.filter((question) => question.state === "open");
  const sections = [
    data.tasks.length ? { href: "#tasks", title: `Задачи · ${data.tasks.length}` } : null,
    openQuestionsForNav.length ? { href: "#questions", title: `Вопросы · ${openQuestionsForNav.length}` } : null,
    data.summary && data.summary.decisions.length
      ? { href: "#decisions", title: `Решения · ${data.summary.decisions.length}` } : null,
    data.marks.length ? { href: "#marks", title: `Метки · ${data.marks.length}` } : null,
    data.timeline.length ? { href: "#transcript", title: "Транскрипт" } : null,
  ].filter((item) => item !== null);
  const openQuestions = data.questions.filter((question) => question.state === "open");
  const doneParts = data.parts.filter((part) => part.transcript_status === "done").length;
  // Части, которые ещё ждут расшифровки. Упавшая — не ждёт: о ней карточка ошибок.
  const queued = data.parts.filter((part) => !part.transcript_status || part.transcript_status === "pending").length;
  const { unnamed, guessed } = nameCounts(groupRows(data.speakers, data.voice_groups, data.short_keys));
  const namesNote = [
    unnamed > 0 ? count(unnamed, "голос без имени", "голоса без имени", "голосов без имени") : "",
    guessed > 0 ? count(guessed, "имя угадано по голосу", "имени угаданы по голосу", "имён угаданы по голосу") : "",
  ].filter(Boolean).join(" · ");

  return (
    <>
      {/* Без темы разговор называется своим часом, а не номером: «Сессия 0001»
          ничего не говорит владельцу, «13 сентября, 17:10» говорит всё.
          Состояние саммари — только пока оно не готово: «готово» и так видно
          по тексту под заголовком. */}
      <PageHeader
        back={{ href: "/records/", title: "Записи" }}
        title={recordTitle({ topic: topic ?? null, day: data.day, first_at: data.first_at })}
        aside={
          <span className="flex flex-wrap items-center gap-x-3.5 gap-y-1">
            {topic && when ? <span>{when}</span> : null}
            {duration !== null ? <span className="font-mono">{formatClock(duration)}</span> : null}
            {tone !== "ok" && <span className="flex items-center gap-2"><Dot tone={tone} />{toneText}</span>}
            <span className="studio-rec-no">REC / {String(data.session).padStart(4, "0")}</span>
          </span>
        }
      />

      {/* Колонка не уже нуля: иначе длинная тема или цитата раздвигает сетку
          и страница уезжает вбок. */}
      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="flex min-w-0 flex-col gap-6">
          {/* Ошибки — первыми: если кусок звука не расшифровался, это главное,
              что надо знать о записи, а не приписка под формой говорящих. */}
          {(data.summary_error || failedParts.length > 0 || missing.length > 0) && (
            <Section note="Ошибки обработки">
              <Card>
                {data.summary_error ? (
                  <Row className="items-start">
                    <Dot tone="accent" />
                    <span className="min-w-0 flex-1">
                      Саммари не собралось: {humanError(data.summary_error)}
                      <details className="text-micro">
                        <summary className="hit relative inline cursor-pointer list-none text-text-tertiary underline decoration-dotted underline-offset-4 [&::-webkit-details-marker]:hidden">
                          подробности
                        </summary>
                        <span className="block font-mono text-text-tertiary">{data.summary_error}</span>
                      </details>
                    </span>
                    <Retry path={`/sessions/${encodeURIComponent(data.device_id)}/${data.session}/retry-summary`}
                           onDone={reload} />
                  </Row>
                ) : null}
                {failedParts.map((part) => (
                  <Row key={part.part} className="items-start">
                    <Dot tone="accent" />
                    <span className="min-w-0 flex-1">
                      Часть {String(part.part).padStart(2, "0")} не расшифровалась: {humanError(part.last_error)}
                      {part.last_error ? (
                        <details className="text-micro">
                          <summary className="hit relative inline cursor-pointer list-none text-text-tertiary underline decoration-dotted underline-offset-4 [&::-webkit-details-marker]:hidden">
                            подробности
                          </summary>
                          <span className="block font-mono text-text-tertiary">{part.last_error}</span>
                        </details>
                      ) : null}
                    </span>
                    <Retry path={`/parts/${encodeURIComponent(data.device_id)}/${data.session}/${part.part}/retry`}
                           onDone={reload} />
                  </Row>
                ))}
                {missing.map((number) => (
                  <Row key={`missing-${number}`} className="items-start">
                    <Dot tone="warn" />
                    <span className="min-w-0 flex-1">
                      Часть {String(number).padStart(2, "0")} не пришла
                      <span className="block text-note text-text-secondary">
                        диктофон её не выгрузил: звук этого куска на сервере отсутствует
                      </span>
                    </span>
                  </Row>
                ))}
              </Card>
            </Section>
          )}

          {data.summary ? (
            <>
              {/* Табличка «Главное из разговора»: оформление в styles/studio.css. */}
              <section className="studio-summary" aria-labelledby="summary-label">
                <h2 id="summary-label" className="studio-summary-label">Главное из разговора</h2>
                <p>{keepNumbers(data.summary.summary)}</p>
              </section>
              {data.summary.topics.length > 0 && (
                <div className="flex flex-wrap items-center gap-2">
                  <span className="px-1 text-note text-text-tertiary">Проекты:</span>
                  {data.summary.topics.map((item) => (
                    <Tag key={item} href={`/project/?name=${encodeURIComponent(item)}`}>{item}</Tag>
                  ))}
                </div>
              )}
            </>
          ) : data.summary_status === "failed" || failedParts.length ? null : (
            // Обещание — только пока работа идёт; при упавшей части о ней уже
            // сказано в карточке ошибок, и повторять её здесь незачем.
            stalled ? (
              // Больше часа без движения — не обещание, а факт и куда смотреть.
              <Empty title="Саммари пока не собрано"
                     text={`Ждёт с ${formatDay(data.day)}. Транскрипт готов ниже; что с обработкой — на странице «Состояние».`}
                     action={<Link href="/status/" className="press hit relative rounded-inner bg-hairline px-4 py-2 text-note hover:bg-hairline-strong">Состояние</Link>} />
            ) : (
              <Empty title="Саммари ещё не готово"
                     text="Решения, факты и задачи появятся здесь, как только расшифровка закончится." />
            )
          )}

          {/* Оглавление: страница длинная, а транскрипт — в самом низу. Только
              те разделы, что есть. */}
          {sections.length > 1 && (
            // Строка ссылок, а не пилюли: пилюли выше — темы, они ведут на
            // проекты; эти ссылки двигают страницу. Одинаковый вид путал.
            // Одна строка с прокруткой: в две строки невидимые слои нажатия
            // накрывали соседей, и «Задачи» открывали транскрипт.
            <nav aria-label="Разделы записи" className="no-scrollbar -mx-1 flex items-center gap-x-4 overflow-x-auto px-1 text-note">
              <span className="shrink-0 text-text-tertiary">Перейти:</span>
              {sections.map((item) => (
                <a key={item.href} href={item.href}
                   className="inline-flex min-h-11 shrink-0 items-center text-text-secondary underline decoration-hairline-strong underline-offset-4 hover:text-text-primary">
                  {item.title}
                </a>
              ))}
            </nav>
          )}

          {/* Порядок — от того, что требует действия, к тому, что можно
              перечитать: задачи и вопросы, потом решения и факты, метки,
              и только потом весь разговор. */}
          {/* Пустая секция — две строки шума ради «нет». Нет задач — нет секции. */}
          {data.tasks.length > 0 && (
            <Section id="tasks" note={`Задачи · ${data.tasks.length}`}>
              <Card>
                {data.tasks.map((task) => (
                  <TaskRow key={task.task_id} task={task} onChanged={reload} showSource={false} />
                ))}
              </Card>
            </Section>
          )}

          {openQuestions.length > 0 && (
            <Section id="questions" note={`Вопросы без ответа · ${openQuestions.length}`}>
              <Card>
                {openQuestions.map((question) => (
                  <Row key={question.question_id} className="items-start gap-3.5">
                    <EvidenceTime time={question.time} label={question.text} context={question.text}
                                  source={question.source} />
                    <span className="min-w-0 flex-1 pt-1">
                      {question.text}
                      <SourceNote source={question.source} />
                    </span>
                  </Row>
                ))}
              </Card>
            </Section>
          )}

          {data.summary ? (
            <>
              <SummaryList id="decisions" title="Решения" items={data.summary.decisions} />
              <SummaryList title="Факты" items={data.summary.facts} />
            </>
          ) : null}

          {data.marks.length > 0 && (
            <Section id="marks" note={`Метки · ${data.marks.length}`}
                     aside="кнопка на диктофоне во время записи">
              <Card>
                {data.marks.map((mark) => (
                  <Row key={`${mark.part}-${mark.offset}`} className="items-start gap-3.5">
                    <BookmarkSimple size={14} weight="fill" aria-hidden className="mt-2 shrink-0 text-mark" />
                    <EvidenceTime time={mark.source ? formatClock(mark.source.start_abs) : mark.time}
                                  label={`метка · ${mark.source ? formatClock(mark.source.start_abs) : normalizeClock(mark.time)}`}
                                  context={mark.text || `метка ${mark.time}`} source={mark.source} />
                    <span className="min-w-0 flex-1 pt-1">
                      {mark.text || <span className="text-text-secondary">часть ещё не расшифрована</span>}
                      <span className="block text-note text-text-secondary">
                        {[mark.label, `кнопка нажата в ${normalizeClock(mark.time)}`].filter(Boolean).join(" · ")}
                      </span>
                    </span>
                  </Row>
                ))}
              </Card>
            </Section>
          )}


        </div>

        {/* Боковая колонка на десктопе; на телефоне она идёт до транскрипта:
            «это я» и имена говорящих не должны прятаться за сотней реплик. */}
        {/* Свёрнутая колонка прилипает к экрану; раскрытый список имён длиннее
            окна, поэтому тогда она едет вместе со страницей (session-aside в
            styles/studio.css) — иначе её низ был недостижим. */}
        <div className="session-aside flex min-w-0 flex-col gap-6 lg:sticky lg:top-6 lg:col-start-2 lg:row-span-2 lg:row-start-1 lg:self-start">
          {/* Говорящие — не чтение: свёрнуты, но сами говорят, есть ли в них
              дело («1 голос без имени»). Доли речи — там же, к именам. */}
          <details className="group flex flex-col gap-6">
            <summary className="press-row flex cursor-pointer list-none items-center justify-between gap-3 rounded-card bg-surface px-4 py-3.5 shadow-plate hover:bg-surface-elevated [&::-webkit-details-marker]:hidden">
              <span>
                Имена и дата
                {namesNote ? (
                  <span className="block text-note text-text-secondary">{namesNote}</span>
                ) : null}
              </span>
              <CaretDown size={16} aria-hidden className="shrink-0 text-text-tertiary transition-transform group-open:rotate-180" />
            </summary>
            <div className="mt-6 flex flex-col gap-6">
              <SpeechShare timeline={data.timeline} />
              <SpeakersForm key={JSON.stringify([data.speakers, data.voice_groups, data.short_keys])}
                            deviceId={data.device_id} session={data.session} speakers={data.speakers}
                            groups={data.voice_groups} shortKeys={data.short_keys} meName={data.me_name}
                            onSaved={reload} />
              <RecordedOn key={data.day} deviceId={data.device_id} session={data.session} day={data.day.slice(0, 10)}
                          onSaved={reload} />
            </div>
          </details>
          {/* Полоски частей — пока есть что сказать: часть ещё в очереди или
              упала. Когда всё расшифровано, это внутренность диктофона. */}
          {data.parts.length > 1 && queued > 0 && <Parts parts={data.parts} missing={missing} />}

          {/* Счёт частей нужен, пока расшифровка не догнала запись; потом это
              техническая подробность, которой владелец не пользуется. */}
          {queued > 0 && (
            <p className="px-1 text-note text-text-tertiary">
              расшифровано {doneParts} из {count(data.parts.length, "части", "частей", "частей")}
            </p>
          )}
          <ShareLinks deviceId={data.device_id} session={data.session} />
          <DeleteSession deviceId={data.device_id} session={data.session} />
        </div>

        <div className="min-w-0 lg:col-start-1">
          {data.timeline.length === 0
            ? <EmptyNote>Расшифровки пока нет.</EmptyNote>
            : <Transcript timeline={data.timeline} marked={marked} received={new Set(numbers)} />}
        </div>
      </div>
    </>
  );
}

function Body() {
  const params = useSearchParams();
  const deviceId = params.get("d") ?? "";
  const session = Number(params.get("n") ?? 0);
  const ready = Boolean(deviceId) && Number.isFinite(session) && session > 0;
  const { data: raw, error, loading, reload } =
    useApi<Detail>(ready ? `/sessions/${encodeURIComponent(deviceId)}/${session}` : null);
  // Реплики подписаны голосом, а не номером порции: «Говорящий 1» в разных
  // частях — разные люди, и доли речи, транскрипт и плеер путали бы их.
  const data = useMemo(() => raw && {
    ...raw,
    timeline: withVoiceLabels(raw.timeline, groupRows(raw.speakers, raw.voice_groups, raw.short_keys)),
  }, [raw]);

  if (!ready) return <Empty title="Сессия не указана" text="Откройте запись из списка." />;
  if (error) {
    return (
      <>
        <PageHeader back={{ href: "/records/", title: "Записи" }}
                    title={error instanceof ApiError && error.status === 404 ? "Такой записи нет" : "Запись"} />
        {error instanceof ApiError && error.status === 404
          ? <p className="px-1 text-[15px] text-text-secondary">Ссылка устарела или запись удалена. Остальные записи на месте.</p>
          : <Failed error={error} onRetry={reload} />}
      </>
    );
  }
  if (loading || !data) return <Loading />;

  return (
    <PlayerProvider deviceId={data.device_id} session={data.session} parts={data.parts}
                    title={recordTitle({ topic: data.summary?.topics?.[0] ?? null, day: data.day, first_at: data.first_at })}
                    marks={data.marks}>
      <Content data={data} reload={reload} />
    </PlayerProvider>
  );
}

export default function SessionPage() {
  // Статический экспорт требует Suspense вокруг useSearchParams.
  return (
    <Suspense fallback={<Loading />}>
      <Body />
    </Suspense>
  );
}
