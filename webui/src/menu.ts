// The menu of the console is data: a screen is added here (and as a route in App.tsx), and the shell shows it.
// `roles` is who sees the entry (empty: every administrator); the server still decides what a request may do.
import { t } from "./i18n/ja";

export interface MenuEntry {
  path: string;
  label: string;
  roles?: string[];
}

export interface MenuGroup {
  title: string;
  entries: MenuEntry[];
}

export const MENU: MenuGroup[] = [
  {
    title: t.menu.overview,
    entries: [
      { path: "/", label: t.nav.dashboard },
      { path: "/system", label: t.nav.system },
      { path: "/usage", label: t.nav.usage },
      { path: "/limits", label: t.nav.limits },
    ],
  },
  {
    title: t.menu.documents,
    entries: [
      { path: "/ingestion", label: t.nav.ingestion },
      { path: "/sources", label: t.nav.sources },
      { path: "/extraction", label: t.nav.extraction },
      { path: "/synonyms", label: t.nav.synonyms },
    ],
  },
  {
    title: t.menu.quality,
    entries: [
      { path: "/records", label: t.nav.records },
      { path: "/review", label: t.nav.reviewQueue },
      { path: "/golden", label: t.nav.golden },
      { path: "/changes", label: t.nav.changes },
    ],
  },
  { title: t.menu.contracts, entries: [{ path: "/contracts", label: t.nav.contracts }] },
  {
    title: t.menu.operations,
    entries: [
      { path: "/audit", label: t.nav.audit },
      { path: "/access", label: t.nav.access },
    ],
  },
];
