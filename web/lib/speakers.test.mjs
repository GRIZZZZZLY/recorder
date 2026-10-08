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

test("«это я» на угаданной строке — уже подтверждение, пометка «по голосу» снимается", async () => {
  const { isGuess } = await import("./speakers.ts");
  const row = { part: 1, speaker: 0, name: "Игорь", auto: true };
  assert.equal(isGuess(row, "Игорь", ""), true);
  assert.equal(isGuess(row, "Игорь", "1:0"), false);   // выбрано «это я»
  assert.equal(isGuess(row, "Пётр", ""), false);       // имя исправлено
  assert.equal(isGuess({ ...row, auto: false }, "Игорь", ""), false);
});

test("«это я» на угаданной строке ставит ваше имя, а не догадку", async () => {
  const { pickMe } = await import("./speakers.ts");
  const speakers = [
    { part: 1, speaker: 0, name: "Игорь", is_me: true, auto: false },
    { part: 2, speaker: 0, name: "Мария", is_me: false, auto: true },
    { part: 2, speaker: 1, name: "Пётр", is_me: false, auto: false },
  ];
  const shown = { "1:0": "Игорь", "2:0": "Мария", "2:1": "Пётр" };
  assert.deepEqual(pickMe(speakers, shown, shown, "1:0", "2:0", "", ""),
                   { names: { ...shown, "2:0": "Игорь" }, filled: "2:0" });
  assert.deepEqual(pickMe(speakers, shown, shown, "1:0", "2:1", "", ""),
                   { names: shown, filled: "" });   // имя владельца не трогаем
  const nobody = speakers.map((s) => ({ ...s, is_me: false }));
  // В этой записи своего имени нет — берём из других записей, иначе пустое поле.
  assert.deepEqual(pickMe(nobody, shown, shown, "", "2:0", "Игорь", "").names,
                   { ...shown, "2:0": "Игорь" });
  assert.deepEqual(pickMe(nobody, shown, shown, "", "2:0", "", "").names, { ...shown, "2:0": "" });
});

test("«это я» перещёлкнули на другую строку — прежняя догадка возвращается", async () => {
  const { pickMe } = await import("./speakers.ts");
  const speakers = [
    { part: 1, speaker: 0, name: "Мария", is_me: false, auto: true },
    { part: 1, speaker: 1, name: "Пётр", is_me: false, auto: true },
  ];
  const shown = { "1:0": "Мария", "1:1": "Пётр" };
  const first = pickMe(speakers, shown, shown, "", "1:0", "Игорь", "");
  assert.deepEqual(first, { names: { "1:0": "Игорь", "1:1": "Пётр" }, filled: "1:0" });
  assert.deepEqual(pickMe(speakers, shown, first.names, "1:0", "1:1", "Игорь", first.filled),
                   { names: { "1:0": "Мария", "1:1": "Игорь" }, filled: "1:1" });
});

test("имя, вписанное руками, при перещёлкивании «это я» не теряется", async () => {
  const { pickMe } = await import("./speakers.ts");
  const speakers = [
    { part: 1, speaker: 0, name: "Мария", is_me: false, auto: true },
    { part: 1, speaker: 3, name: "", is_me: false, auto: false },
  ];
  const shown = { "1:0": "Мария", "1:3": "" };
  const typed = { "1:0": "Игорь", "1:3": "" };     // владелец сам исправил догадку
  const first = pickMe(speakers, shown, typed, "", "1:0", "Игорь", "");
  assert.deepEqual(first, { names: typed, filled: "" });
  assert.deepEqual(pickMe(speakers, shown, first.names, "1:0", "1:3", "Игорь", first.filled).names,
                   typed);
});
