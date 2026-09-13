"use client";

import { useState } from "react";
import Link from "next/link";
import { PageTitle } from "@/components/recorder/eyebrow";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { Button, Input } from "@/components/recorder/ui";
import { apiPost } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { formatDay, plural } from "@/lib/format";

type Listing = {
  projects: { name: string; sessions: number; last_day: string | null }[];
  aliases: { alias: string; canonical: string }[];
};

export default function ProjectsPage() {
  const { data, error, loading, reload } = useApi<Listing>("/projects");
  const [alias, setAlias] = useState("");
  const [canonical, setCanonical] = useState("");
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  async function save() {
    if (!alias.trim() || !canonical.trim()) return;
    setBusy(true);
    try {
      await apiPost("/projects/aliases", { alias, canonical });
      setSaveError(null);
      setAlias("");
      setCanonical("");
      reload();
    } catch (err) {
      setSaveError(`не удалось сохранить: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageTitle eyebrow="ДИКТОФОН" plain="Разговоры по" italic="проектам" />

      {error ? <Failed error={error} onRetry={reload} />
        : loading || !data ? <Loading />
        : (
          <>
            {data.projects.length === 0 ? (
              <Empty text="Пока пусто: тема разговора появляется в саммари после обработки следующих записей." />
            ) : (
              <ul className="grid gap-3 sm:grid-cols-2">
                {data.projects.map((project) => (
                  <li key={project.name}>
                    <Link href={`/project/?name=${encodeURIComponent(project.name)}`}
                          className="recorder-press block rounded-2xl border border-separator-border bg-background-primary-default p-6 hover:bg-background-primary-hover">
                      <p className="text-headline-medium text-text-primary">{project.name}</p>
                      <p className="nums mt-1 text-body-2-regular text-text-tertiary">
                        {project.sessions} {plural(project.sessions, "разговор", "разговора", "разговоров")} · последний {formatDay(project.last_day)}
                      </p>
                    </Link>
                  </li>
                ))}
              </ul>
            )}

            <section className="mt-12">
              <h2 className="text-title-3-semibold text-text-primary">Псевдонимы</h2>
              <p className="mt-1 text-body-2-regular text-text-tertiary">
                «Пози флора» и «Posiflora» — один проект: сведите их здесь.
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <Input className="min-w-48 flex-1" placeholder="Как прозвучало"
                       value={alias}
                       onChange={(value: string) => { setAlias(value); setSaveError(null); }} />
                <Input className="min-w-48 flex-1" placeholder="Название проекта"
                       value={canonical}
                       onChange={(value: string) => { setCanonical(value); setSaveError(null); }} />
                <Button variant="primary" disabled={busy} onClick={save}>
                  Сохранить
                </Button>
              </div>
              {saveError && (
                <p className="mt-2 text-body-2-regular text-text-error-primary">{saveError}</p>
              )}
              {data.aliases.length > 0 && (
                <ul className="mt-4 flex flex-col gap-2">
                  {data.aliases.map((item) => (
                    <li key={item.alias} className="text-body-2-regular text-text-tertiary">
                      {item.alias} → {item.canonical}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )}
    </>
  );
}
