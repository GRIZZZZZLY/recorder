/**
 * Что форма имён отправляет на сервер: только поля, которые владелец изменил,
 * плюс прежняя и новая строка «это я», если она сменилась.
 *
 * Отправлять всё нельзя. Страница могла открыться до того, как сервер
 * подписал номера по голосу: тогда пустое поле ушло бы как «владелец стёр
 * имя», и номер больше никогда не узнавался бы.
 */
/**
 * Сколько голосов без имени и сколько имён угадано по голосу. Догадку нельзя
 * считать именем: иначе надпись «N голосов без имени» пропадёт, и владелец не
 * откроет список, чтобы проверить угаданное.
 */
export function nameCounts(speakers: { name: string; auto: boolean }[]): { unnamed: number; guessed: number } {
  return {
    unnamed: speakers.filter((s) => !s.name).length,
    guessed: speakers.filter((s) => s.name && s.auto).length,
  };
}

/**
 * Показывать ли пометку «по голосу». Догадка перестаёт быть догадкой, как
 * только владелец исправил имя или отметил строку «это я»: при сохранении
 * сервер запишет её как слово владельца, и страница не должна говорить иное.
 */
export function isGuess(speaker: { part: number; speaker: number; name: string; auto: boolean },
                        name: string, me: string): boolean {
  return speaker.auto && name === speaker.name && me !== `${speaker.part}:${speaker.speaker}`;
}

type Row = { part: number; speaker: number; name: string; is_me: boolean; auto: boolean };

/**
 * Имена после того, как «это я» перешло со строки prevMe на строку me.
 *
 * На угаданной строке догадку заменяет ваше имя — из этой записи или, если
 * здесь вы ещё не отмечены, из других (meName); не известно — пустое поле.
 * Иначе сохранение записало бы чужое имя как ваше: ваш голос ушёл бы в чужой
 * профиль, а задачи того человека стали бы вашими. Если «это я» перещёлкнули
 * на другую строку, прежней строке возвращается её догадка — пока владелец
 * сам не вписал туда что-то другое.
 */
export function pickMe(speakers: Row[], shown: Record<string, string>, names: Record<string, string>,
                       prevMe: string, me: string, meName: string): Record<string, string> {
  const mine = speakers.find((s) => s.is_me && !s.auto && s.name)?.name || meName;
  const guessed = (key: string) => speakers.some((s) => `${s.part}:${s.speaker}` === key && s.auto);
  const next = { ...names };
  if (prevMe && prevMe !== me && guessed(prevMe) && next[prevMe] === mine && shown[prevMe] !== mine) {
    next[prevMe] = shown[prevMe] ?? "";
  }
  if (me && guessed(me) && next[me] === (shown[me] ?? "")) next[me] = mine;
  return next;
}

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
