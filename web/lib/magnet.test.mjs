// Проверка магнита ленты дня: `node --test lib/magnet.test.mjs` (Node ≥ 22.18,
// типы из .ts вырезает сам). Отдельного тестового фреймворка у web нет — и не надо.
import { test } from "node:test";
import assert from "node:assert/strict";
import { magnetTarget } from "./magnet.ts";

const spans = [{ start: 100, end: 140 }, { start: 300, end: 310 }];

test("внутри записи магнит молчит", () => {
  assert.equal(magnetTarget(120, spans, 24), null);
  assert.equal(magnetTarget(100, spans, 24), null);
  assert.equal(magnetTarget(140, spans, 24), null);
});

test("слева от записи — к началу, справа — к концу", () => {
  assert.equal(magnetTarget(80, spans, 24), 100);
  assert.equal(magnetTarget(160, spans, 24), 140);
});

test("дальше радиуса не тянет", () => {
  assert.equal(magnetTarget(75, spans, 24), null);
  assert.equal(magnetTarget(200, spans, 24), null);
});

test("из двух соседей берёт ближнего", () => {
  assert.equal(magnetTarget(290, [{ start: 260, end: 270 }, { start: 300, end: 310 }], 24), 300);
  assert.equal(magnetTarget(278, [{ start: 260, end: 270 }, { start: 300, end: 310 }], 24), 270);
});

test("без записей — ничего", () => {
  assert.equal(magnetTarget(50, [], 24), null);
});
