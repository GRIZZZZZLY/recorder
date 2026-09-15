"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowRight } from "@phosphor-icons/react/dist/ssr";
import { Card, Row, Section } from "@/components/recorder/list";
import { PageHeader } from "@/components/recorder/page-header";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { Button, Input } from "@/components/recorder/ui";
import { apiPost } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { count, formatDayShort } from "@/lib/format";
import { at } from "@/lib/motion";
import { cx } from "@/utils/cx";

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
  // Только что добавленный псевдоним въезжает в список с анимацией — остальные
  // строки на месте, им двигаться незачем.
  const [justAdded, setJustAdded] = useState<string | null>(null);

  async function save() {
    if (!alias.trim() || !canonical.trim()) {
      setSaveError("заполните оба поля");
      return;
    }
    setBusy(true);
    try {
      await apiPost("/projects/aliases", { alias, canonical });
      setSaveError(null);
      setJustAdded(alias);
      setAlias("");
      setCanonical("");
      reload();
    } catch (err) {
      setSaveError(`не удалось сохранить: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !data) return <Loading />;

  const total = data.projects.reduce((sum, project) => sum + project.sessions, 0);

  return (
    <>
      <PageHeader
        title="Проекты"
        aside={data.projects.length
          ? `${count(data.projects.length, "тема", "темы", "тем")} · ${count(total, "разговор", "разговора", "разговоров")}`
          : undefined}
      />

      {data.projects.length === 0 ? (
        <Empty title="Тем пока нет"
               text="Тема разговора появляется в саммари. Обработайте несколько записей — темы соберутся сами." />
      ) : (
        <div className="grid gap-3 cascade sm:grid-cols-2 lg:grid-cols-3" style={at(1)}>
          {data.projects.map((project) => (
            <Link
              key={project.name}
              href={`/project/?name=${encodeURIComponent(project.name)}`}
              className="press-row flex flex-col gap-3.5 rounded-card bg-surface p-5 shadow-plate transition-shadow duration-150 hover:bg-surface-elevated hover:shadow-lit"
            >
              <span className="text-card-title text-text-primary">{project.name}</span>
              <span className="text-note text-text-secondary">
                {count(project.sessions, "разговор", "разговора", "разговоров")}
                {project.last_day ? ` · последний ${formatDayShort(project.last_day)}` : ""}
              </span>
            </Link>
          ))}
        </div>
      )}

      <Section head="Псевдонимы" aside="как тема звучит в разговоре → как называется в системе" at={3}>
        <Card>
          <Row className="flex-wrap items-end gap-3">
            <Input className="min-w-40 flex-1" label="Как прозвучало" placeholder="Пози флора"
                   value={alias}
                   onChange={(value) => { setAlias(value); setSaveError(null); }} />
            <ArrowRight size={16} aria-hidden className="mb-3 hidden shrink-0 text-text-tertiary sm:block" />
            <Input className="min-w-40 flex-1" label="Название проекта" placeholder="Posiflora"
                   value={canonical} error={saveError}
                   onChange={(value) => { setCanonical(value); setSaveError(null); }} />
            <Button className="mb-0.5 shrink-0" disabled={busy} onClick={save}>Добавить</Button>
          </Row>
          {data.aliases.map((item) => (
            <Row key={item.alias} className={cx(item.alias === justAdded && "rise-in")}>
              <span className="min-w-0 flex-1 truncate">{item.alias}</span>
              <ArrowRight size={16} aria-hidden className="shrink-0 text-text-tertiary" />
              <span className="min-w-0 flex-1 truncate text-text-secondary">{item.canonical}</span>
            </Row>
          ))}
        </Card>
      </Section>
    </>
  );
}
