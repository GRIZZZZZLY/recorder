/**
 * Удалить по очереди и вернуть то, что не удалилось, с причиной.
 *
 * По очереди, а не разом: каждое удаление стирает файлы на сервере, и пачка
 * одновременных запросов ничего не выигрывает. Сбой одной записи не
 * останавливает остальные — у каждой свой запрос и свой итог.
 */
export async function removeEach<T>(items: T[], remove: (item: T) => Promise<unknown>) {
  const failed: { item: T; reason: string }[] = [];
  for (const item of items) {
    try {
      await remove(item);
    } catch (err) {
      failed.push({ item, reason: (err as Error).message });
    }
  }
  return failed;
}
