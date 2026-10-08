/** Клиент JSON-API. Basic-заголовок ставит браузер: origin тот же. */

export class AuthError extends Error {
  constructor() {
    super("нужен вход заново");
  }
}

/** Ошибка сервера с кодом: экран решает, что сказать — «такого нет» или
 * «не удалось загрузить», — а текст сервера берётся из поля detail. */
export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/ui${path}`, { credentials: "same-origin", ...init });
  if (response.status === 401) throw new AuthError();
  if (!response.ok) {
    const body = await response.text();
    let detail = body.slice(0, 200);
    try {
      const parsed = JSON.parse(body) as { detail?: unknown };
      if (typeof parsed.detail === "string") detail = parsed.detail;
    } catch {
      // не JSON — показываем как есть
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

export function apiGet<T>(path: string, init?: RequestInit): Promise<T> {
  return request<T>(path, init);
}

export function apiPost<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function apiDelete<T>(path: string): Promise<T> {
  return request<T>(path, { method: "DELETE" });
}

/** Ссылка гостя, как её видит владелец. Время — UTC из SQLite. */
export type Share = { token: string; created_at: string; expires_at: string };

/** Запись по ссылке гостя: только то, что сервер разрешил показать (server/share.py). */
export type SharedDetail = {
  day: string;
  first_at: string | null;
  parts: { part: number; offset: number; seconds: number; envelope: number[] }[];
  summary: Summary | null;
  timeline: Utterance[];
  speakers: { part: number; speaker: number; name: string; auto: boolean }[];
  voice_groups: { keys: string[]; seconds: number }[];
  short_keys: string[];
};

/** Запись по ключу из ссылки. Мимо /api/ui: там всё под паролем владельца. */
export async function sharedGet(token: string): Promise<SharedDetail> {
  const response = await fetch(`/api/share/${encodeURIComponent(token)}`);
  if (!response.ok) throw new ApiError(response.status, response.status === 404 ? "ссылка не работает" : `ошибка ${response.status}`);
  return (await response.json()) as SharedDetail;
}

export type Task = {
  device_id: string;
  session: number;
  task_id: string;
  text: string;
  owner: string;
  due_said: string;
  due_date: string | null;
  source: Source;
  status: "open" | "done" | "dismissed";
  vanished: number;
  mine: boolean;
  overdue: boolean;
  /** Из какого разговора: тема саммари и день. */
  day: string | null;
  topic: string | null;
  tape?: Tape | null;
};

/** Плёнка записи: волна целиком и длина — на ней засечка места пункта. */
export type Tape = { envelope: number[]; seconds: number };

export type Source = {
  part: number;
  idx: number;
  start: number;
  start_abs: number;
  confirmed: boolean;
} | null;

export type SummaryItem = {
  text: string;
  time: string;
  quote: string;
  owner?: string;
  due?: string;
  source?: Source;
};

export type Summary = {
  summary: string;
  topics: string[];
  decisions: SummaryItem[];
  facts: SummaryItem[];
  tasks: SummaryItem[];
  open_questions: SummaryItem[];
};

export type Utterance = {
  part: number;
  idx: number;
  start: number;
  end: number;
  start_abs: number;
  speaker: number | null;
  label: string;
  text: string;
};

export type SessionRow = {
  device_id: string;
  session: number;
  parts: number;
  done_parts: number;
  seconds: number;
  day: string | null;
  /** Метки базы: "2026-09-12 16:40:07". Первая и последняя часть сессии. */
  first_at: string | null;
  last_at: string | null;
  /** Первая тема саммари — имя разговора в списке. Пусто, пока саммари нет. */
  topic: string | null;
  summary_status: string | null;
  /** Номер первой принятой части: с неё играет ▶ на карточке. */
  first_part: number;
  /** Части, которые так и не расшифровались: запись с ними не «готово». */
  failed_parts: number;
  /** Метки владельца: секунды от начала сессии. Кнопка на диктофоне. */
  marks: number[];
  /** Названные участники разговора. Безымянных сервер не отдаёт. */
  speakers: string[];
  /** Громкость сессии целиком, 60 значений 0..100. Считана из файлов. */
  envelope: number[];
};

/** Метка кнопкой на диктофоне и реплика, прозвучавшая перед ней. */
export type Mark = {
  part: number;
  offset: number;
  start_abs: number;
  time: string;
  source: Source;
  text: string;
  label: string;
};

export type Counts = {
  days: number;
  minutes: number;
  sessions: number;
  new_tasks: number;
  my_open_tasks: number;
  open_tasks: number;
  overdue_tasks: number;
  open_questions: number;
  review: number;
  failed_parts: number;
  failed_summaries: number;
};

export type DigestBody = {
  day: Counts;
  week: Counts;
  my_tasks: Task[];
  has_me: boolean;
  recent: SessionRow[];
};

export type Question = {
  device_id: string;
  session: number;
  question_id: string;
  state: "open" | "answered" | "dropped";
  text: string;
  time: string;
  source: Source;
  day: string | null;
  topic: string | null;
  tape?: Tape | null;
};

export type ReviewItem = {
  kind: string;
  device_id: string;
  session: number;
  day: string | null;
  text: string;
  detail: string;
  source: Source;
  tape?: Tape | null;
};

export type SearchHit = {
  device_id: string;
  session: number;
  part: number;
  idx: number;
  start_abs: number;
  label: string;
  text: string;
  /** Имя разговора в выдаче: тема саммари, без неё — день. */
  day: string;
  topic: string | null;
  segments: { text: string; hit: boolean }[];
};
