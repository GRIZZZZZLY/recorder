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

test("ваше имя стало известно между щелчками — подставленное раньше всё равно возвращается", async () => {
  const { pickMe } = await import("./speakers.ts");
  const speakers = [
    { part: 2, speaker: 0, name: "Мария", is_me: false, auto: true },
    { part: 2, speaker: 1, name: "Пётр", is_me: false, auto: true },
  ];
  const shown = { "2:0": "Мария", "2:1": "Пётр" };
  const first = pickMe(speakers, shown, shown, "", "2:0", "", null);      // имени ещё нет
  assert.deepEqual(first.names, { "2:0": "", "2:1": "Пётр" });
  const second = pickMe(speakers, shown, first.names, "2:0", "2:1", "Игорь", first.filled);
  assert.deepEqual(second.names, { "2:0": "Мария", "2:1": "Игорь" });
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

test("короткие реплики не считаются голосами без имени", async () => {
  const { nameCounts } = await import("./speakers.ts");
  assert.deepEqual(nameCounts([{ name: "", auto: false, short: false },
                               { name: "", auto: false, short: true },
                               { name: "Мария", auto: true, short: false }]),
                   { unnamed: 1, guessed: 1 });
});

test("«это я» и пометка «по голосу» работают и для строки-группы", async () => {
  const { isGuess, pickMe } = await import("./speakers.ts");
  const group = { key: "1:0", keys: ["1:0", "1:10"], name: "Мария", is_me: false, auto: true };
  assert.equal(isGuess(group, "Мария", ""), true);
  assert.equal(isGuess(group, "Мария", "1:0"), false);
  assert.deepEqual(pickMe([group], { "1:0": "Мария" }, { "1:0": "Мария" }, "", "1:0", "Игорь", null).names,
                   { "1:0": "Игорь" });
});

test("реплики подписаны голосом, а не номером порции", async () => {
  const { groupRows, withVoiceLabels } = await import("./speakers.ts");
  const speakers = [
    { part: 1, speaker: 0, name: "", is_me: false, auto: false },
    { part: 1, speaker: 10, name: "", is_me: false, auto: false },
    { part: 2, speaker: 0, name: "Мария", is_me: false, auto: false },
    { part: 2, speaker: 4, name: "", is_me: false, auto: false },
  ];
  const rows = groupRows(speakers, [{ keys: ["1:0", "1:10"], seconds: 600 },
                                    { keys: ["2:0"], seconds: 60 }], ["2:4"]);
  const timeline = [
    { part: 1, speaker: 0, label: "Говорящий 1" }, { part: 1, speaker: 10, label: "Говорящий 11" },
    { part: 2, speaker: 0, label: "Мария" }, { part: 2, speaker: 4, label: "Говорящий 5" },
    { part: 2, speaker: null, label: "Говорящий ?" },
  ];
  // «Говорящий 1» части 1 и части 2 — разные люди; один человек — один голос.
  assert.deepEqual(withVoiceLabels(timeline, rows).map((u) => u.label),
                   ["Голос 1", "Голос 1", "Мария", "Без имени", "Говорящий ?"]);
});
