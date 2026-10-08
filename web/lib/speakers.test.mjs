// Что форма имён отправляет на сервер: `node --test lib/speakers.test.mjs`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { changedNames } from "./speakers.ts";

test("нетронутые поля не уходят: пустое поле не должно стереть догадку, пришедшую позже", () => {
  const shown = { "1:0": "Игорь", "1:1": "" };
  assert.deepEqual(changedNames(shown, { ...shown }, "", ""), {});
});

test("уходит только исправленное поле", () => {
  const shown = { "1:0": "Игорь", "1:1": "" };
  assert.deepEqual(changedNames(shown, { "1:0": "Игорь", "1:1": "Мария" }, "", ""),
                   { "1:1": "Мария" });
});

test("смена «это я» отправляет прежнюю и новую строку", () => {
  const shown = { "1:0": "Игорь", "1:1": "Мария" };
  assert.deepEqual(changedNames(shown, { ...shown }, "1:0", "1:1"),
                   { "1:0": "Игорь", "1:1": "Мария" });
  assert.deepEqual(changedNames(shown, { ...shown }, "1:0", ""), { "1:0": "Игорь" });
});

test("угаданные по голосу имена считаются отдельно от безымянных", async () => {
  const { nameCounts } = await import("./speakers.ts");
  const speakers = [
    { name: "Игорь", auto: false }, { name: "Мария", auto: true },
    { name: "Мария", auto: true }, { name: "", auto: false },
  ];
  assert.deepEqual(nameCounts(speakers), { unnamed: 1, guessed: 2 });
});
