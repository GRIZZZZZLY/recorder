// Удаление пачкой: `node --test lib/remove-each.test.mjs`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { removeEach } from "./remove-each.ts";

test("сбой одной записи не останавливает остальные", async () => {
  const removed = [];
  const failed = await removeEach(["a", "b", "c"], async (item) => {
    if (item === "b") throw new Error("сеть пропала");
    removed.push(item);
  });
  assert.deepEqual(removed, ["a", "c"]);
  assert.deepEqual(failed, [{ item: "b", reason: "сеть пропала" }]);
});

test("по очереди, а не разом: следующая — после ответа на предыдущую", async () => {
  const log = [];
  await removeEach([1, 2], async (item) => {
    log.push(`start ${item}`);
    await new Promise((resolve) => setTimeout(resolve, 5));
    log.push(`end ${item}`);
  });
  assert.deepEqual(log, ["start 1", "end 1", "start 2", "end 2"]);
});
