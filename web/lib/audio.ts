/**
 * Один звуковой элемент на всё приложение.
 *
 * Клавиша ▶ на карточке записи запускает звук прямо в нажатии: iPhone
 * разрешает воспроизведение только из обработчика жеста, ждать перехода на
 * страницу и ответа сервера нельзя. Плеер на странице записи не заводит свой
 * <audio>, а подхватывает этот — уже играющий.
 */
let shared: HTMLAudioElement | null = null;
let pendingLabel: string | null = null;

export function sharedAudio(): HTMLAudioElement {
  if (!shared) {
    shared = new Audio();
    // "metadata", не "none": с "none" Chromium откладывает загрузку
    // назначенного src до play(), и loadedmetadata, которого ждёт перемотка,
    // не приходит никогда — Play и реплики молчали.
    shared.preload = "metadata";
  }
  return shared;
}

export function audioSrc(deviceId: string, session: number, part: number) {
  return `/audio/${encodeURIComponent(deviceId)}/${session}/${part}`;
}

/** Что сейчас стоит в элементе: запись и часть — или ничего. */
export function loadedPart(element: HTMLAudioElement) {
  const match = /\/audio\/([^/]+)\/(\d+)\/(\d+)$/.exec(element.src);
  if (!match) return null;
  return { deviceId: decodeURIComponent(match[1]), session: Number(match[2]), part: Number(match[3]) };
}

/** Запустить запись с начала части. Вызывать только из обработчика нажатия. */
export function startPlayback(deviceId: string, session: number, part: number, label: string) {
  const element = sharedAudio();
  element.src = audioSrc(deviceId, session, part);
  pendingLabel = label;
  // Отказ браузера плеер покажет сам: он увидит паузу и предложит нажать Play.
  element.play().catch(() => {});
}

/** Подпись, с которой звук запустили вне плеера. Отдаётся один раз. */
export function takeLabel(): string | null {
  const label = pendingLabel;
  pendingLabel = null;
  return label;
}
