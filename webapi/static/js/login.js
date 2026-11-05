/* Admin login helpers (stage 13: moved out of admin_login.html so they can be
 * unit-tested with node --test, see tests/js/).
 *
 * Security: after signing in the browser returns only to an admin page of
 * this site; anything else (other hosts, "//host", "/\host", schemes,
 * encoded tricks) falls back to /admin.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.KuradenLogin = api;
  }
})(this, function () {
  "use strict";

  var ADMIN_PATH_RE = /^\/admin(\/[A-Za-z0-9_\-\/]*)?$/;

  // search: location.search, e.g. "?next=/admin/ingestion".
  function nextPath(search) {
    var raw = null;
    try {
      raw = new URLSearchParams(search || "").get("next");
    } catch (e) {
      raw = null;
    }
    if (!raw) return "/admin";
    if (raw.indexOf("//") >= 0 || raw.indexOf("\\") >= 0) return "/admin";
    return ADMIN_PATH_RE.test(raw) ? raw : "/admin";
  }

  return { nextPath: nextPath };
});
