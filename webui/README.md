# Administration console (webui)

A single-page app (React, TypeScript, Vite) that the web API serves as static files under `/console/`.
There is no Node in the image that runs: `npm run build` runs in a build stage (see `Dockerfile`) and only `dist/` is copied.

## Commands

```
npm ci            # install exactly what package-lock.json says (never `npm install` in CI)
npm run typecheck
npm test          # Vitest, jsdom
npm run build     # type check, then dist/
npm run dev       # development server; /admin and /system go to http://localhost:8000
```

The API serves `webui/dist` (or the directory in `CONSOLE_DIST_DIR`). Without a build, `/console/` answers 404 and says so.

## Rules that keep the supply chain small

- **Exact versions** in `package.json` (`.npmrc`: `save-exact`), the lock file is committed, CI installs with `npm ci`.
- **No install scripts** (`.npmrc`: `ignore-scripts`): a package cannot run code when it is installed.
- **No package younger than 14 days** when a version is chosen (the publish date is checked on the registry before a version
  is written into `package.json`), and an update is a reviewed change of `package.json` and the lock file.
- `npm audit --audit-level=high` runs in CI.
- **No server-side rendering and no React Server Components**: the app is static files, so the vulnerabilities of those features
  do not apply. Do not add a framework that needs a Node server.
- Few dependencies on purpose: React, React Router, and the test and build tools. Adding one needs a reason.

## Structure

- `src/menu.ts` – the menu is data; a screen is a route in `App.tsx` plus an entry here.
- `src/i18n/ja.ts` – every text, by key (a second language is a second file). Reason codes from the server are shown through it.
- `src/api/` – the calls to the API (session cookie, CSRF token on every change, back to the login page on 401).
- `src/pages/` – one file per screen. Every screen shows loading, error (with retry), empty and success states.
- `src/test/` – the tests.

## Screens

Done: system status and the integration switches. The older admin pages (`/admin/...`) stay until each has an equivalent here;
the screens and the operation do not change when a page moves.
