// node --test tests/js   (Node 18+, no dependency)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const R = require(path.join(__dirname, "..", "..", "webapi", "static", "js", "render.js"));

test("document text cannot inject markup or script", () => {
  const html = R.renderAnswerHtml('<img src=x onerror="alert(1)"> と <script>alert(2)</script> [1]', { 1: "1" });
  assert.ok(!html.includes("<img"), html);
  assert.ok(!html.includes("<script"), html);
  assert.ok(html.includes("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;"), html);
  assert.ok(html.includes('<button type="button" class="cite" data-n="1"'), html);
});

test("quotes and ampersands are escaped", () => {
  assert.equal(R.esc(`a&b<"c">'d'`), "a&amp;b&lt;&quot;c&quot;&gt;&#39;d&#39;");
  assert.equal(R.esc(null), "");
});

test("citation markers [n] and [Sn] become buttons, mapped to panel numbers", () => {
  const html = R.renderAnswerHtml("申請期限は月末です[S2]。", { 2: "1" });
  assert.ok(html.includes('data-n="1"') && html.includes(">1</button>"), html);
  assert.ok(!html.includes("[S2]"), html);
});

test("a citation number from the map is escaped too", () => {
  const html = R.renderAnswerHtml("本文[1]", { 1: '"><img src=x>' });
  assert.ok(!html.includes("<img"), html);
});

test("bullet lines become a list, other lines paragraphs", () => {
  const html = R.renderAnswerHtml("手順は次のとおりです。\n- 申請書を出す\n・承認を待つ\n1. 支払い\n以上です。", null);
  assert.equal(
    html,
    "<p>手順は次のとおりです。</p><ul><li>申請書を出す</li><li>承認を待つ</li><li>支払い</li></ul><p>以上です。</p>"
  );
});

test("the trailing reference list is removed", () => {
  const text = "回答本文です[1]\n参照:\n- [1] 規程.pdf p3\n- [2] 手順書.pdf p1";
  assert.equal(R.stripReferenceBlock(text), "回答本文です[1]");
  const html = R.renderAnswerHtml(text, null);
  assert.ok(!html.includes("規程.pdf"), html);
});

test("answer kind: approved, abstain, grounded", () => {
  assert.equal(R.answerKind({ answer_mode: "approved_exact_match", answer_text: "x" }), "approved");
  assert.equal(R.answerKind({ answer_text: "x", guard_reason: "soft_distance" }), "abstain");
  assert.equal(R.answerKind({ answer_text: "x", used_fallback: true }), "abstain");
  assert.equal(R.answerKind({ answer_text: "" }), "abstain");
  assert.equal(R.answerKind(null), "abstain");
  assert.equal(R.answerKind({ answer_text: "本文", answer_mode: "grounded" }), "grounded");
});
