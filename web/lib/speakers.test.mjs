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


test("«это я» на угаданной строке ставит ваше имя, а не догадку", async () => {
  const { pickMe } = await import("./speakers.ts");
  const speakers = [
    { part: 1, speaker: 0, name: "Игорь", is_me: true, auto: false },
    { part: 2, speaker: 0, name: "Мария", is_me: false, auto: true },
    { part: 2, speaker: 1, name: "Пётр", is_me: false, auto: false },
  ];
  const shown = { "1:0": "Игорь", "2:0": "Мария", "2:1": "Пётр" };
  assert.deepEqual(pickMe(speakers, shown, shown, "1:0", "2:0", "", null),
                   { names: { ...shown, "2:0": "Игорь" }, filled: { key: "2:0", value: "Игорь" } });
  assert.deepEqual(pickMe(speakers, shown, shown, "1:0", "2:1", "", null),
                   { names: shown, filled: null });   // имя владельца не трогаем
  const nobody = speakers.map((s) => ({ ...s, is_me: false }));
  // В этой записи своего имени нет — берём из других записей, иначе пустое поле.
  assert.deepEqual(pickMe(nobody, shown, shown, "", "2:0", "Игорь", null).names,
                   { ...shown, "2:0": "Игорь" });
  assert.deepEqual(pickMe(nobody, shown, shown, "", "2:0", "", null).names, { ...shown, "2:0": "" });
});

test("«это я» перещёлкнули на другую строку — прежняя догадка возвращается", async () => {
  const { pickMe } = await import("./speakers.ts");
  const speakers = [
    { part: 1, speaker: 0, name: "Мария", is_me: false, auto: true },
    { part: 1, speaker: 1, name: "Пётр", is_me: false, auto: true },
  ];
  const shown = { "1:0": "Мария", "1:1": "Пётр" };
  const first = pickMe(speakers, shown, shown, "", "1:0", "Игорь", null);
  assert.deepEqual(first, { names: { "1:0": "Игорь", "1:1": "Пётр" }, filled: { key: "1:0", value: "Игорь" } });
  assert.deepEqual(pickMe(speakers, shown, first.names, "1:0", "1:1", "Игорь", first.filled),
                   { names: { "1:0": "Мария", "1:1": "Игорь" }, filled: { key: "1:1", value: "Игорь" } });
});

test("имя, вписанное руками, при перещёлкивании «это я» не теряется", async () => {
  const { pickMe } = await import("./speakers.ts");
  const speakers = [
    { part: 1, speaker: 0, name: "Мария", is_me: false, auto: true },
    { part: 1, speaker: 3, name: "", is_me: false, auto: false },
  ];
  const shown = { "1:0": "Мария", "1:3": "" };
  const typed = { "1:0": "Игорь", "1:3": "" };     // владелец сам исправил догадку
  const first = pickMe(speakers, shown, typed, "", "1:0", "Игорь", null);
  assert.deepEqual(first, { names: typed, filled: null });
  assert.deepEqual(pickMe(speakers, shown, first.names, "1:0", "1:3", "Игорь", first.filled).names,
                   typed);
});


test("номера одного голоса — одна строка с общим именем", async () => {
  const { groupRows } = await import("./speakers.ts");
  const speakers = [
    { part: 1, speaker: 0, name: "Игорь", is_me: true, auto: false },
    { part: 1, speaker: 10, name: "Игорь", is_me: false, auto: true },
    { part: 2, speaker: 0, name: "", is_me: false, auto: false },
    { part: 2, speaker: 3, name: "Мария", is_me: false, auto: true },
    { part: 2, speaker: 4, name: "", is_me: false, auto: false },
  ];
  const rows = groupRows(speakers, [
    { keys: ["1:0", "1:10", "2:0"], seconds: 1500 },
    { keys: ["2:3"], seconds: 40 },
  ], ["2:4"]);
  assert.deepEqual(rows.map((r) => [r.key, r.keys.length, r.name, r.auto, r.is_me, r.short]), [
    ["1:0", 3, "Игорь", false, true, false],
    ["2:3", 1, "Мария", true, false, false],
    ["2:4", 1, "", false, false, true],
  ]);
});

test("имя группы уходит на все её номера", async () => {
  const { expandNames } = await import("./speakers.ts");
  const rows = [{ key: "1:0", keys: ["1:0", "1:10", "2:0"] }, { key: "2:3", keys: ["2:3"] }];
  assert.deepEqual(expandNames(rows, { "1:0": "Пётр" }),
                   { "1:0": "Пётр", "1:10": "Пётр", "2:0": "Пётр" });
});


