// Проверка пересчёта огибающей: `node --test lib/resample.test.mjs`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { resample } from "./resample.ts";

test("вниз — пик в окне, не среднее", () => {
  assert.deepEqual(resample([10, 90, 10, 10, 20, 30], 3), [90, 10, 30]);
});

test("вверх — плавно между соседями, концы на месте", () => {
  assert.deepEqual(resample([0, 100], 5), [0, 25, 50, 75, 100]);
  assert.deepEqual(resample([20, 80, 20], 5), [20, 50, 80, 50, 20]);
});

test("вверх не выдумывает громче соседей", () => {
  const out = resample([10, 40, 90, 30], 40);
  assert.equal(Math.max(...out), 90);
  assert.equal(Math.min(...out), 10);
  assert.equal(out.length, 40);
});

test("совпало — как есть; одно значение — ровная линия", () => {
  const same = [1, 2, 3];
  assert.equal(resample(same, 3), same);
  assert.deepEqual(resample([42], 4), [42, 42, 42, 42]);
});

test("пусто и ноль столбиков — пусто", () => {
  assert.deepEqual(resample([], 10), []);
  assert.deepEqual(resample([5, 6], 0), []);
});
