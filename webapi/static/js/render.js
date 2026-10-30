/* Answer rendering for the chat workspace (stage 13: moved out of chat.html
 * so it can be unit-tested with node --test, see tests/js/).
 *
 * Security: the answer text is HTML-escaped first; only citation markers
 * ([1] / [S1]) and simple bullet lines are turned into markup afterwards, so
 * text from documents can never inject HTML or script.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.KuradenRender = api;
  }
})(this, function () {
  "use strict";

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // Drops the trailing reference list ("- [1] doc p3" lines and its title):
  // the citation panel shows the sources.
  function stripReferenceBlock(text) {
    var lines = String(text || "").split("\n");
    while (lines.length && /^\s*-\s*\[\d+\]/.test(lines[lines.length - 1])) lines.pop();
    while (lines.length && /^\s*(参照|出典|References?)\s*[:：]?\s*$/.test(lines[lines.length - 1])) lines.pop();
    return lines.join("\n").replace(/\s+$/, "");
  }

  // "- " / "• " / "1. " need a space after the mark; "・" (Japanese) does not.
  var BULLET_RE = /^\s*(?:[-•]\s+|・\s*|\d+[.)．]\s+)/;

  // citeMap: answer marker number -> citation number shown in the panel.
  function renderAnswerHtml(text, citeMap) {
    var html = [], list = [];
    function flush() { if (list.length) { html.push("<ul>" + list.join("") + "</ul>"); list = []; } }
    stripReferenceBlock(text).split("\n").forEach(function (raw) {
      var line = esc(raw).replace(/\[S?(\d{1,2})\]/g, function (m, n) {
        var num = citeMap && citeMap[n] ? citeMap[n] : n;
        return '<button type="button" class="cite" data-n="' + esc(num) + '" aria-label="出典 ' + esc(num) + '">' + esc(num) + "</button>";
      });
      if (BULLET_RE.test(raw)) { list.push("<li>" + line.replace(BULLET_RE, "") + "</li>"); return; }
      flush();
      if (line.trim()) html.push("<p>" + line + "</p>");
    });
    flush();
    return html.join("");
  }

  // "approved" | "abstain" | "grounded" for the badge above an answer.
  function answerKind(data) {
    var mode = (data && (data.answer_mode || data.intent)) || "";
    if (/^approved_/.test(mode)) return "approved";
    var txt = data && (data.answer_text || data.answer_with_footnotes);
    if (!data || data.guard_reason || data.used_fallback || !txt) return "abstain";
    return "grounded";
  }

  return { esc: esc, stripReferenceBlock: stripReferenceBlock, renderAnswerHtml: renderAnswerHtml, answerKind: answerKind };
});
