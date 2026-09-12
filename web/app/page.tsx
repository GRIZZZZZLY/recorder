import { Button } from "@/components/base/buttons/button";
import { Badge } from "@/components/base/badges/badge";
import { formatDay } from "@/lib/format";

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-start gap-4 p-8">
      <h1 className="text-2xl font-medium text-text-primary">Диктофон</h1>
      <p className="text-text-secondary">{formatDay(new Date().toISOString())}</p>
      <div className="flex items-center gap-3">
        <Button size="medium">Записать</Button>
        <Badge>Черновик</Badge>
      </div>
    </main>
  );
}
