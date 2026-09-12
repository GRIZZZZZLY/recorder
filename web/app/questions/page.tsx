"use client";

import { useState } from "react";
import { Button } from "@/components/base/buttons/button";
import { Filters } from "@/components/recorder/filters";
import { PageTitle } from "@/components/recorder/eyebrow";
import { ListenLink, SourceDot } from "@/components/recorder/source-link";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { apiPost, type Question } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { formatDay } from "@/lib/format";

const FILTERS = [{ key: "open", title: "Без ответа" }, { key: "all", title: "Все" }];
const ACTIONS: { state: Question["state"]; title: string }[] = [
  { state: "answered", title: "Ответ получен" },
  { state: "dropped", title: "Снять" },
  { state: "open", title: "Вернуть" },
];
const MARKS: Record<Question["state"], string> = {
  open: "", answered: "ответ получен", dropped: "снят",
};

/** Строка вопроса: своё состояние занятости и ошибки, как в TaskRow —
 *  ошибка запроса не должна проходить молча. */
function QuestionRow({ question, onChanged }: { question: Question; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function change(state: Question["state"], title: string) {
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
    <li className="rounded-2xl border border-separator-border bg-background-primary-default p-6">
      <p className="text-headline-medium text-text-primary">{question.text}</p>
      <p className="mt-1 text-body-2-regular text-text-tertiary nums">
        {formatDay(question.day)} · сессия {String(question.session).padStart(4, "0")}
        {MARKS[question.state] ? ` · ${MARKS[question.state]}` : ""}
      </p>
      <div className="mt-3 flex items-center gap-2">
        <SourceDot source={question.source} />
        <ListenLink source={question.source} deviceId={question.device_id}
                    session={question.session} />
      </div>
      <div className="mt-4 flex flex-wrap gap-3">
        {ACTIONS.filter((action) => action.state !== question.state).map((action) => (
          <Button key={action.state} variant="ghost" size="small" disabled={busy}
                  onClick={() => change(action.state, action.title)}>
            {action.title}
          </Button>
        ))}
      </div>
      {error && (
        <p className="mt-2 text-body-2-regular text-[var(--color-overdue)]">{error}</p>
      )}
    </li>
  );
}

export default function QuestionsPage() {
  const [show, setShow] = useState("open");
  const { data, error, loading, reload } = useApi<Question[]>(`/questions?show=${show}`);

  return (
    <>
      <PageTitle eyebrow="ДИКТОФОН" plain="Вопросы" italic="без ответа" />
      <Filters value={show} options={FILTERS} onChange={setShow} />

      <div className="mt-6">
        {error ? <Failed error={error} onRetry={reload} />
          : loading || !data ? <Loading />
          : data.length === 0 ? <Empty text="Ничего нет." />
          : (
            <ul className="flex flex-col gap-3">
              {data.map((question) => (
                <QuestionRow key={`${question.session}-${question.question_id}`}
                             question={question} onChanged={reload} />
              ))}
            </ul>
          )}
      </div>
    </>
  );
}
