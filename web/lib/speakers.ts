/**
 * Строка списка «Имена и дата». Сервер отдаёт номера говорящих (часть:номер),
 * а форма показывает голоса: Gemini нумерует людей заново в каждой
 * 5-минутной порции и дробит их, и в часовой записи бывает 77 номеров на
 * 4–6 человек. Строка-голос несёт свой ключ (первый номер группы) и все
 * номера; строка-номер — только часть и номер.
 */
type Row = { key?: string; part?: number; speaker?: number; name: string; is_me: boolean; auto: boolean };

const keyOf = (row: { key?: string; part?: number; speaker?: number }) =>
  row.key ?? `${row.part}:${row.speaker}`;

export type GroupRow = {
  key: string;
  keys: string[];
  name: string;
  auto: boolean;
  is_me: boolean;
  seconds: number;
  /** Номер без отпечатка голоса и без имени: говорил меньше 8 секунд. */
  short: boolean;
};

/** Самое частое имя среди строк; пусто, если имён нет. */
function commonName(rows: Row[]): string {
  const counts = new Map<string, number>();
  for (const row of rows) if (row.name) counts.set(row.name, (counts.get(row.name) ?? 0) + 1);
  let best = "";
  for (const [name, n] of counts) if (n > (counts.get(best) ?? 0)) best = name;
  return best;
}

/**
 * Строки формы: группы голосов (крупные первыми, как их отдал сервер), затем
 * короткие номера по одному. Имя группы — имя владельца, если он уже назвал
 * кого-то в ней, иначе догадка по голосу.
 */
export function groupRows(speakers: { part: number; speaker: number; name: string; is_me: boolean; auto: boolean }[],
                          groups: { keys: string[]; seconds: number }[],
                          shortKeys: string[]): GroupRow[] {
  const byKey = new Map(speakers.map((s) => [keyOf(s), s]));
  const row = (keys: string[], seconds: number, short: boolean): GroupRow => {
    const members = keys.map((k) => byKey.get(k)).filter((m): m is NonNullable<typeof m> => Boolean(m));
    const own = commonName(members.filter((m) => !m.auto));
    const name = own || commonName(members.filter((m) => m.auto));
    return { key: keys[0], keys, name, auto: !own && Boolean(name),
             is_me: members.some((m) => m.is_me), seconds, short };
  };
  return [...groups.map((g) => row(g.keys, g.seconds, false)),
          ...shortKeys.map((k) => row([k], 0, true))];
}

/**
 * Реплики с подписью голоса вместо номера порции. Сервер подписывает реплику
 * «Говорящий N» по номеру внутри её части, и «Говорящий 1» в разных частях —
 * разные люди: доли речи складывались бы не по людям. Здесь подпись та же, что
 * в списке имён: имя, «Голос N» по порядку голосов или «Без имени» для коротких
 * реплик. Реплика без номера остаётся как была.
 */
export function withVoiceLabels<T extends { part: number; speaker: number | null; label: string }>(
  timeline: T[], rows: GroupRow[]): T[] {
  const labels = new Map<string, string>();
  rows.filter((r) => !r.short).forEach((row, index) => {
    for (const key of row.keys) labels.set(key, row.name || `Голос ${index + 1}`);
  });
  for (const row of rows.filter((r) => r.short)) {
    for (const key of row.keys) labels.set(key, row.name || "Без имени");
  }
  return timeline.map((u) => {
    const label = u.speaker === null ? undefined : labels.get(`${u.part}:${u.speaker}`);
    return label && label !== u.label ? { ...u, label } : u;
  });
}

/** Имена строк-групп — на все номера группы: назвали голос, подписаны все его номера. */
export function expandNames(rows: { key: string; keys: string[] }[],
                            names: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const row of rows) {
    if (row.key in names) for (const key of row.keys) out[key] = names[row.key];
  }
  return out;
}

/**
 * Сколько голосов без имени и сколько имён угадано по голосу. Догадку нельзя
 * считать именем: иначе надпись о безымянных голосах пропадёт, и владелец не
 * откроет список, чтобы проверить угаданное. Короткие номера (реплики меньше
 * 8 секунд) голосами не считаются.
 */
export function nameCounts(rows: { name: string; auto: boolean; short?: boolean }[]): { unnamed: number; guessed: number } {
  const voices = rows.filter((r) => !r.short);
  return {
    unnamed: voices.filter((r) => !r.name).length,
    guessed: voices.filter((r) => r.name && r.auto).length,
  };
}

/**
 * Показывать ли пометку «по голосу». Догадка перестаёт быть догадкой, как
 * только владелец исправил имя или отметил строку «это я»: при сохранении
 * сервер запишет её как слово владельца, и страница не должна говорить иное.
 */
export function isGuess(row: Row, name: string, me: string): boolean {
  return row.auto && name === row.name && me !== keyOf(row);
}

/** Строка и имя, которые pickMe подставило само. */
type Filled = { key: string; value: string } | null;

/**
 * Имена после того, как «это я» перешло со строки prevMe на строку me.
 *
 * На угаданной строке догадку заменяет ваше имя — из этой записи или, если
 * здесь вы ещё не отмечены, из других (meName); не известно — пустое поле.
 * Иначе сохранение записало бы чужое имя как ваше: ваш голос ушёл бы в чужой
 * профиль, а задачи того человека стали бы вашими.
 *
 * filled — строка и имя, которые подставила сама эта функция. Если «это я»
 * перещёлкнули дальше, догадка возвращается только туда и только пока там
 * стоит подставленное: имя, вписанное владельцем руками, не трогаем, даже
 * если оно совпало с его; а сменившееся между щелчками meName не мешает
 * узнать своё подставленное.
 */
export function pickMe(rows: Row[], shown: Record<string, string>, names: Record<string, string>,
                       prevMe: string, me: string, meName: string,
                       filled: Filled): { names: Record<string, string>; filled: Filled } {
  if (me === prevMe) return { names, filled };
  const mine = rows.find((r) => r.is_me && !r.auto && r.name)?.name || meName;
  const guessed = (key: string) => rows.some((r) => keyOf(r) === key && r.auto);
  const next = { ...names };
  if (filled && filled.key === prevMe && next[prevMe] === filled.value) {
    next[prevMe] = shown[prevMe] ?? "";
  }
  let nowFilled: Filled = null;
  if (me && guessed(me) && next[me] === (shown[me] ?? "")) {
    next[me] = mine;
    nowFilled = { key: me, value: mine };
  }
  return { names: next, filled: nowFilled };
}

/**
 * Что форма имён отправляет на сервер: только поля, которые владелец изменил,
 * плюс прежняя и новая строка «это я», если она сменилась.
 *
 * Отправлять всё нельзя. Страница могла открыться до того, как сервер
 * подписал номера по голосу: тогда пустое поле ушло бы как «владелец стёр
 * имя», и номер больше никогда не узнавался бы.
 */
export function changedNames(shown: Record<string, string>, names: Record<string, string>,
                             shownMe: string, me: string): Record<string, string> {
  const changed: Record<string, string> = {};
  for (const [key, name] of Object.entries(names)) {
    if (name !== (shown[key] ?? "")) changed[key] = name;
  }
  if (me !== shownMe) {
    for (const key of [shownMe, me]) {
      if (key) changed[key] = names[key] ?? "";
    }
  }
  return changed;
}
