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
