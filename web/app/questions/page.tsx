"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { CaretRight } from "@phosphor-icons/react/dist/ssr";
import { useRouter, useSearchParams } from "next/navigation";
import { Card, Row, Section } from "@/components/recorder/list";
import { PageHeader } from "@/components/recorder/page-header";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { EvidenceLink, Segmented, SourceDot } from "@/components/recorder/ui";
import { apiPost, type Question } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { formatDayShort } from "@/lib/format";
import { sessionHref } from "@/lib/session-link";
import { cx } from "@/utils/cx";

const ACTIONS: { state: Question["state"]; title: string }[] = [
  { state: "answered", title: "Ответ получен" },
  { state: "dropped", title: "Снять" },
  { state: "open", title: "Вернуть" },
];

const MARKS: Record<Question["state"], string> = {
  open: "", answered: "ответ получен", dropped: "снят",
};

/**
 * Строка вопроса. Отвеченный вопрос остаётся на месте: текст гаснет,
 * появляется отметка. Ничего не схлопывается — иначе непонятно, что
 * произошло после нажатия.
 */
function QuestionRow({ question, onChanged }: { question: Question; onChanged: () => void }) {
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
      onChanged();
    } catch (err) {
      setError(`не удалось ${title.toLowerCase()}: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Row className="flex-wrap gap-y-2">
      <SourceDot source={question.source} />

      <span className={cx("min-w-0 flex-1 transition-colors duration-150", closed && "text-text-secondary")}>
        {question.text}
        {MARKS[question.state] ? (
          <span className={cx("ml-2.5 inline-block rounded-chip border border-hairline-strong px-2 py-0.5 text-micro text-text-secondary",
                              acted && "chip-in")}>
            {MARKS[question.state]}
          </span>
        ) : null}
        {error ? <span className="rise-in block text-note text-accent-text">{error}</span> : null}
      </span>

      <span className="shrink-0 text-note text-text-secondary">{formatDayShort(question.day)}</span>

      <Link href={sessionHref(question.device_id, question.session)}
            className="press hit relative hidden shrink-0 text-text-tertiary hover:text-text-primary md:block"
            title="Открыть запись" aria-label="Открыть запись">
        <CaretRight size={14} aria-hidden />
      </Link>

      <EvidenceLink href={question.source
        ? sessionHref(question.device_id, question.session, question.source.part, question.source.idx)
        : null} />

      <span className="flex shrink-0 gap-3 text-note text-text-secondary">
        {ACTIONS.filter((action) => action.state !== question.state).map((action) => (
          <button
            key={action.state}
            type="button"
            disabled={busy}
            onClick={() => change(action.state, action.title)}
            className="press rounded-sm hover:text-text-primary disabled:opacity-40"
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
  const { data: all } = useApi<Question[]>("/questions?show=all");

  return (
    <>
      <PageHeader
        title="Вопросы без ответа"
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
                             question={question} onChanged={reload} />
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
