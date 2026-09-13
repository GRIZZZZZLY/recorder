"use client";

import { Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { PageHeader } from "@/components/recorder/page-header";
import { SessionNo } from "@/components/recorder/list";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { useApi } from "@/lib/use-api";
import { count, formatDay } from "@/lib/format";
import { at } from "@/lib/motion";
import { sessionHref } from "@/lib/session-link";

type Feed = {
  name: string;
  sessions: {
    device_id: string;
    session: number;
    day: string | null;
    summary: string | null;
    decisions: { text: string }[];
    tasks: { text: string }[];
    open_questions: { text: string }[];
  }[];
};

/** Колонка выжимки под абзацем: заголовок и до трёх пунктов. */
function Column({ title, items }: { title: string; items: { text: string }[] }) {
  if (items.length === 0) return null;
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-text-tertiary">{title} · {items.length}</span>
      {items.slice(0, 3).map((item, index) => (
        <span key={index} className="leading-snug text-text-secondary">{item.text}</span>
      ))}
    </div>
  );
}

function Body() {
  const name = useSearchParams().get("name") ?? "";
  const { data, error, loading, reload } =
    useApi<Feed>(name ? `/projects/${encodeURIComponent(name)}` : null);

  if (!name) return <Empty title="Проект не указан" text="Откройте тему из списка проектов." />;
  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !data) return <Loading />;

  const tasks = data.sessions.reduce((sum, item) => sum + item.tasks.length, 0);
  const questions = data.sessions.reduce((sum, item) => sum + item.open_questions.length, 0);

  return (
    <>
      <PageHeader
        back={{ href: "/projects/", title: "Проекты" }}
        title={data.name}
        aside={[
          count(data.sessions.length, "разговор", "разговора", "разговоров"),
          count(tasks, "задача", "задачи", "задач"),
          count(questions, "вопрос", "вопроса", "вопросов"),
        ].join(" · ")}
      />

      {data.sessions.length === 0 ? (
        <Empty title="Разговоров по этой теме нет"
               text="Тема встречалась в саммари, но записи к ней не привязались." />
      ) : (
        <div className="relative flex max-w-[900px] flex-col gap-6 cascade" style={at(1)}>
          {/* Линия хронологии между датой и карточкой — рисуется сверху вниз
              вместе с каскадом. На телефоне колонки нет, линии тоже. */}
          <span aria-hidden className="feed-line absolute top-1 bottom-1 left-[152px] hidden w-px bg-hairline-strong sm:block" />
          {data.sessions.map((item) => (
            <article key={`${item.device_id}-${item.session}`}
                     className="grid gap-3 sm:grid-cols-[140px_1fr] sm:gap-6">
              <div className="flex flex-col gap-1 text-note sm:pt-1">
                <span>{formatDay(item.day)}</span>
                <Link href={sessionHref(item.device_id, item.session)}
                      className="press hover:text-text-primary">
                  <SessionNo session={item.session} />
                </Link>
              </div>
              <div className="flex flex-col gap-3.5 rounded-card bg-surface px-5 py-4.5 text-row">
                <p className="leading-normal">{item.summary || "саммари ещё не собрано"}</p>
                {(item.decisions.length || item.tasks.length || item.open_questions.length) ? (
                  <div className="grid gap-4 border-t border-hairline pt-3 text-note sm:grid-cols-3">
                    <Column title="Решения" items={item.decisions} />
                    <Column title="Задачи" items={item.tasks} />
                    <Column title="Вопросы" items={item.open_questions} />
                  </div>
                ) : null}
              </div>
            </article>
          ))}
        </div>
      )}
    </>
  );
}

export default function ProjectPage() {
  return (
    <Suspense fallback={<Loading />}>
      <Body />
    </Suspense>
  );
}
