// node --test tests/js   (Node 18+, no dependency)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const { nextPath } = require(path.join(__dirname, "..", "..", "webapi", "static", "js", "login.js"));

test("admin pages of this site are accepted", () => {
  assert.equal(nextPath("?next=/admin"), "/admin");
  assert.equal(nextPath("?next=/admin/ingestion"), "/admin/ingestion");
  assert.equal(nextPath("?next=%2Fadmin%2Freview"), "/admin/review");
});

test("missing or empty next goes to the dashboard", () => {
  assert.equal(nextPath(""), "/admin");
  assert.equal(nextPath("?next="), "/admin");
  assert.equal(nextPath(undefined), "/admin");
});

test("other sites and tricks are refused", () => {
  for (const bad of [
    "?next=https://evil.example.co.jp/admin",
    "?next=//evil.example.co.jp",
    "?next=/admin//evil.example.co.jp",
    "?next=%2F%2Fevil.example.co.jp",
    "?next=/\\evil.example.co.jp",
    "?next=/admin\\..\\x",
    "?next=javascript:alert(1)",
    "?next=/chat-ui",
    "?next=/administrator",
    "?next=/admin/../chat-ui",
    "?next=/admin?x=1",
  ]) {
    assert.equal(nextPath(bad), "/admin", bad);
  }
});
