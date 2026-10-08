// Проверка формата времени: `node --test lib/format.test.mjs`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { formatClock, formatLocalDay, humanError, keepNumbers, normalizeClock } from "./format.ts";

test("короче часа — без часов, длиннее — с часами", () => {
  assert.equal(formatClock(19), "0:19");
  assert.equal(formatClock(754), "12:34");
  assert.equal(formatClock(3723), "1:02:03");
  assert.equal(formatClock(-5), "0:00");
});

test("разряды числа не рвутся переносом", () => {
  assert.equal(keepNumbers("Предел: 2 200 000 и 12 дней"), "Предел: 2\u00a0200\u00a0000 и 12 дней");
});

test("ошибка расшифровщика — словами", () => {
  assert.equal(humanError("Deepgram отказал 400: corrupt or unsupported data"), "звук повреждён или в незнакомом формате");
  assert.equal(humanError("HTTP 503 from Deepgram"), "сбой на стороне расшифровщика");
  assert.equal(humanError(null), "причина не записана");
});

test("время сервера приводится к тому же виду", () => {
  assert.equal(normalizeClock("0:00:27"), "0:27");
  assert.equal(normalizeClock("1:02:03"), "1:02:03");
  assert.equal(normalizeClock("вчера"), "вчера");
});

test("срок ссылки: метка базы в UTC показывается местной датой", () => {
  // Дата считается теми же местными часами, что и в самой проверке, поэтому
  // тест верен в любом часовом поясе, а полночь по Гринвичу — самый острый случай.
  const at = new Date("2026-10-15T00:30:00Z");
  const months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
  assert.equal(formatLocalDay("2026-10-15 00:30:00"), `${at.getDate()} ${months[at.getMonth()]}`);
  assert.equal(formatLocalDay(null), "дата неизвестна");
});
