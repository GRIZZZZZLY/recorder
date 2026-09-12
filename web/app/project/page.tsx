"use client";

import { Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { PageTitle } from "@/components/recorder/eyebrow";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { useApi } from "@/lib/use-api";
import { formatDay } from "@/lib/format";

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

function Group({ title, items }: { title: string; items: { text: string }[] }) {
  if (items.length === 0) return null;
  return (
    <>
      <p className="mt-4 text-caption-1-medium uppercase tracking-[0.12em] text-text-tertiary">
        {title}
      </p>
      <ul className="mt-1 list-disc pl-5">
        {items.map((item, index) => (
          <li key={index} className="text-body-regular text-text-secondary">{item.text}</li>
        ))}
      </ul>
    </>
  );
}

function Body() {
  const name = useSearchParams().get("name") ?? "";
  const { data, error, loading, reload } =
    useApi<Feed>(name ? `/projects/${encodeURIComponent(name)}` : null);

  if (!name) return <Empty text="Проект не указан." />;
  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !data) return <Loading />;

  return (
    <>
      <PageTitle eyebrow="ПРОЕКТ" plain={data.name} />
      {data.sessions.length === 0 ? <Empty text="Разговоров по этой теме нет." /> : (
        <ol className="flex flex-col gap-6">
          {data.sessions.map((item) => (
            <li key={`${item.device_id}-${item.session}`}
                className="rounded-2xl border border-separator-border bg-background-primary-default p-6">
              <p className="font-[family-name:var(--font-literata)] text-title-3-semibold text-text-primary">
                <span className="nums">{formatDay(item.day)}</span>{" "}
                <Link href={`/session/?d=${encodeURIComponent(item.device_id)}&n=${item.session}`}
                      className="text-body-2-medium text-text-tertiary underline-offset-2 hover:underline">
                  сессия <span className="nums">{String(item.session).padStart(4, "0")}</span>
                </Link>
              </p>
              <p className="mt-2 text-body-regular leading-6 text-text-secondary">{item.summary}</p>
              <Group title="Решения" items={item.decisions} />
              <Group title="Задачи" items={item.tasks} />
              <Group title="Вопросы" items={item.open_questions} />
            </li>
          ))}
        </ol>
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
