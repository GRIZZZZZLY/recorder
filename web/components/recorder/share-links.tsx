"use client";

import { useState } from "react";
import { LinkSimple } from "@phosphor-icons/react/dist/ssr";
import { Card, Row, Section } from "@/components/recorder/list";
import { Button } from "@/components/recorder/ui";
import { apiDelete, apiPost } from "@/lib/api";
import type { Share } from "@/lib/api";
import { formatDay } from "@/lib/format";
import { useApi } from "@/lib/use-api";

function shareUrl(token: string) {
  return `${window.location.origin}/shared/?t=${encodeURIComponent(token)}`;
}

/**
 * Ссылки на эту запись для другого человека: слушать и читать без пароля,
 * 7 дней (server/share.py). Копирование — только по нажатию самой кнопки:
 * Safari пускает в буфер обмена лишь из жеста, а не после ответа сервера.
 */
export function ShareLinks({ deviceId, session }: { deviceId: string; session: number }) {
  const path = `/sessions/${encodeURIComponent(deviceId)}/${session}/shares`;
  const { data: links, reload } = useApi<Share[]>(path);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function create() {
    setBusy(true);
    setError(null);
    try {
      await apiPost<Share>(path, {});
      reload();
    } catch (err) {
      setError(`не удалось создать ссылку: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  async function close(token: string) {
    setError(null);
    try {
      await apiDelete(`/shares/${encodeURIComponent(token)}`);
      reload();
    } catch (err) {
      setError(`не удалось закрыть доступ: ${(err as Error).message}`);
    }
  }

  async function copy(token: string) {
    try {
      await navigator.clipboard.writeText(shareUrl(token));
      setCopied(token);
    } catch {
      // Буфер недоступен — ссылка видна текстом, её можно выделить руками.
      setError("не удалось скопировать: выделите ссылку и скопируйте её");
    }
  }

  return (
    <Section note="Поделиться записью">
      <Card>
        {(links ?? []).map((link) => (
          <Row key={link.token} className="flex-col items-start gap-2">
            <span className="w-full select-all break-all font-mono text-micro text-text-secondary">
              {shareUrl(link.token)}
            </span>
            <span className="text-note text-text-tertiary">работает до {formatDay(link.expires_at)}</span>
            <span className="flex flex-wrap gap-2">
              <Button onClick={() => copy(link.token)}>
                {copied === link.token ? "Скопировано" : "Скопировать"}
              </Button>
              <Button variant="ghost" onClick={() => close(link.token)}>Закрыть доступ</Button>
            </span>
          </Row>
        ))}
        <Row className="flex-col items-start gap-3">
          <span className="text-note text-text-secondary">
            По ссылке человек без пароля послушает запись и прочтёт главное и расшифровку.
            Задачи, другие записи и правка ему не видны. Ссылка работает 7 дней.
          </span>
          <Button icon={LinkSimple} disabled={busy} onClick={create}>
            {busy ? "Создаю…" : "Создать ссылку"}
          </Button>
        </Row>
      </Card>
      {error ? <p className="rise-in px-1 text-note text-accent-text">{error}</p> : null}
    </Section>
  );
}
