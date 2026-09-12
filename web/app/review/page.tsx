"use client";

import { PageTitle } from "@/components/recorder/eyebrow";
import { ListenLink, SourceDot } from "@/components/recorder/source-link";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { useApi } from "@/lib/use-api";
import type { ReviewItem } from "@/lib/api";
import { formatDay, plural } from "@/lib/format";

export default function ReviewPage() {
  const { data, error, loading, reload } = useApi<ReviewItem[]>("/review");

  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !data) return <Loading />;

  const groups = new Map<string, ReviewItem[]>();
  for (const item of data) {
    const bucket = groups.get(item.kind) ?? [];
    bucket.push(item);
    groups.set(item.kind, bucket);
  }

  return (
    <>
      <PageTitle eyebrow="ДИКТОФОН" plain="Требует" italic="владельца" />
      <p className="text-body-regular text-text-secondary">
        Обработка ничего из этого не ждёт: это подсказка, а не блокировка.
      </p>

      <div className="mt-6">
        {data.length === 0 ? <Empty text="Нечего проверять." /> : (
          [...groups.entries()].map(([kind, items]) => (
            <section key={kind} className="mt-8">
              <h2 className="text-title-3-semibold text-text-primary">
                {kind}{" "}
                <span className="nums text-text-tertiary">
                  · {items.length} {plural(items.length, "спорное место", "спорных места", "спорных мест")}
                </span>
              </h2>
              <ul className="mt-3 flex flex-col gap-3">
                {items.map((item, index) => (
                  <li key={`${item.session}-${index}`}
                      className="rounded-2xl border border-separator-border bg-background-primary-default p-6">
                    <p className="text-body-regular text-text-primary">{item.text}</p>
                    <p className="mt-1 text-body-2-regular text-text-tertiary nums">
                      {item.detail} · {formatDay(item.day)} ·
                      сессия {String(item.session).padStart(4, "0")}
                    </p>
                    <div className="mt-3 flex items-center gap-2">
                      <SourceDot source={item.source} />
                      <ListenLink source={item.source} deviceId={item.device_id}
                                  session={item.session} />
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          ))
        )}
      </div>
    </>
  );
}
