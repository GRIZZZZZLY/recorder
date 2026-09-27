"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { CaretRight } from "@phosphor-icons/react/dist/ssr";
import { useRouter, useSearchParams } from "next/navigation";
import { Card, Row, Section } from "@/components/recorder/list";
import { PageHeader } from "@/components/recorder/page-header";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { EvidenceLink, ROW_ACTION, ROW_ACTION_QUIET, Segmented, SourceDot, SourceNote, SourceOf } from "@/components/recorder/ui";
import { apiPost, type Question } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { sessionHref } from "@/lib/session-link";
import { cx } from "@/utils/cx";

const ACTIONS: { state: Question["state"]; title: string }[] = [
  { state: "answered", title: "Ответ получен" },
  { state: "dropped", title: "Убрать" },
  { state: "open", title: "Вернуть" },
];

const MARKS: Record<Question["state"], string> = {
  open: "", answered: "ответ получен", dropped: "убран",
};

/**
 * Строка вопроса. Отвеченный вопрос остаётся на месте: текст гаснет,
 * появляется отметка. Ничего не схлопывается — иначе непонятно, что
 * произошло после нажатия.
 */
function QuestionRow({ question, onChanged }: { question: Question; onChanged: (state: Question["state"]) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const closed = question.state !== "open";
  // Отметка анимируется только после действия владельца — как в строке задачи.
  const [acted, setActed] = useState(false);

  async function change(state: Question["state"], title: string) {
    setActed(true);
    setBusy(true);
    try {
      await apiPost(
        `/questions/${encodeURIComponent(question.device_id)}/${question.session}/${question.question_id}`,
        { state });
      setError(null);
      onChanged(state);
    } catch (err) {
      setError(`не удалось ${title.toLowerCase()}: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Row className="flex-wrap gap-y-2">
      <SourceDot source={question.source} />

      {/* На телефоне текст занимает всю строку, день — подписью под ним,
          «основание» и действия — второй строкой. */}
      <span className={cx("min-w-0 flex-1 transition-colors duration-150 max-md:basis-[calc(100%-7rem)]",
                          closed && "text-text-secondary")}>
        {question.text}
        {MARKS[question.state] ? (
          <span className={cx("ml-2.5 inline-block rounded-chip border border-hairline-strong px-2 py-0.5 text-micro text-text-secondary",
                              acted && "chip-in")}>
            {MARKS[question.state]}
          </span>
        ) : null}
        <SourceOf topic={question.topic} day={question.day} />
        <SourceNote source={question.source} />
        {error ? <span className="rise-in block text-note text-accent-text">{error}</span> : null}
      </span>

      {/* На телефоне к реплике ведёт «основание»; стрелка нужна там, только
          когда источника нет и вести больше нечем. */}
      <Link href={sessionHref(question.device_id, question.session)}
            className={cx("press hit relative shrink-0 text-text-tertiary hover:text-text-primary max-md:order-2",
                          question.source ? "hidden md:block" : "block")}
            title="Открыть запись" aria-label="Открыть запись">
        <CaretRight size={14} aria-hidden />
      </Link>

      {/* Время-основание — как у задач: в первой строке справа от текста. */}
      <span className="shrink-0 max-md:order-1">
        <EvidenceLink time={question.time} context={question.text} href={question.source
          ? sessionHref(question.device_id, question.session, question.source.part, question.source.idx)
          : null} />
      </span>

      <span className="flex shrink-0 gap-2 max-md:order-2 max-md:ml-auto">
        {ACTIONS.filter((action) => question.state === "open" ? action.state !== "open" : action.state === "open").map((action) => (
          <button
            key={action.state}
            type="button"
            disabled={busy}
            onClick={() => change(action.state, action.title)}
            className={action.state === "dropped" ? ROW_ACTION_QUIET : ROW_ACTION}
          >
            {action.title}
          </button>
        ))}
      </span>
    </Row>
  );
}

function Body() {
  const router = useRouter();
  const show = useSearchParams().get("show") ?? "open";
  const { data, error, loading, reload } = useApi<Question[]>(`/questions?show=${show}`);
  const { data: all, reload: recount } = useApi<Question[]>("/questions?show=all");
  // Как у задач: отвеченный вопрос остаётся на месте с отметкой и «Вернуть».
  const [touched, setTouched] = useState<{ view: string; rows: Record<string, Question> }>({ view: show, rows: {} });
  const override = touched.view === show ? touched.rows : {};
  const keyOf = (q: Question) => `${q.device_id}-${q.session}-${q.question_id}`;

  return (
    <>
      <PageHeader
        title="Вопросы"
        aside={
          <Segmented
            label="Фильтр вопросов"
            value={show}
            options={[
              { key: "open", title: "Без ответа", count: all?.filter((q) => q.state === "open").length },
              { key: "all", title: "Все", count: all?.length },
            ]}
            onChange={(key) => router.push(`/questions/?show=${key}`)}
          />
        }
      />

      {/* Пока перезапрашивается — прежний список гаснет, а не пропадает в
          скелетон: новые строки въедут каскадом на его место. */}
      {error ? <Failed error={error} onRetry={reload} />
        : !data ? <Loading />
        : data.length === 0 ? (
          <Empty title="Вопросов нет"
                 text="Саммари не нашло в разговорах ничего, что осталось без ответа." />
        ) : (
          <Section className={cx(loading && "stale")}>
            <Card>
              {data.map((question) => (
                <QuestionRow key={`${question.session}-${question.question_id}`}
                             question={override[keyOf(question)] ?? question}
                             onChanged={(state) => {
                               setTouched((prev) => ({
                                 view: show,
                                 rows: { ...(prev.view === show ? prev.rows : {}), [keyOf(question)]: { ...question, state } },
                               }));
                               recount();
                             }} />
              ))}
            </Card>
          </Section>
        )}
    </>
  );
}

export default function QuestionsPage() {
  return (
    <Suspense fallback={<Loading />}>
      <Body />
    </Suspense>
  );
}
