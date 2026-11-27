// The screens of the console, each loaded on first use (its own chunk). The import function is kept by path so that
// the menu can start loading a screen when the pointer or the focus reaches its link (prefetch), before the click.
import { type ComponentType, lazy, type LazyExoticComponent } from "react";

type Loader = () => Promise<{ default: ComponentType }>;

/** A loader that runs the import at most once while it succeeds (a failed import may be tried again). */
function once(load: Loader): Loader {
  let pending: Promise<{ default: ComponentType }> | null = null;
  return () => {
    pending ??= load().catch((e: unknown) => {
      pending = null;
      throw e;
    });
    return pending;
  };
}

export const PAGE_LOADERS: Record<string, Loader> = {
  "/": once(() => import("./pages/Dashboard").then((m) => ({ default: m.DashboardPage }))),
  "/system": once(() => import("./pages/SystemStatus").then((m) => ({ default: m.SystemStatusPage }))),
  "/usage": once(() => import("./pages/Usage").then((m) => ({ default: m.UsagePage }))),
  "/limits": once(() => import("./pages/Limits").then((m) => ({ default: m.LimitsPage }))),
  "/ingestion": once(() => import("./pages/Ingestion").then((m) => ({ default: m.IngestionPage }))),
  "/sources": once(() => import("./pages/Sources").then((m) => ({ default: m.SourcesPage }))),
  "/extraction": once(() => import("./pages/Extraction").then((m) => ({ default: m.ExtractionPage }))),
  "/synonyms": once(() => import("./pages/Synonyms").then((m) => ({ default: m.SynonymsPage }))),
  "/records": once(() => import("./pages/Records").then((m) => ({ default: m.RecordsPage }))),
  "/review": once(() => import("./pages/ReviewQueue").then((m) => ({ default: m.ReviewQueuePage }))),
  "/approved-qa": once(() => import("./pages/ApprovedQa").then((m) => ({ default: m.ApprovedQaPage }))),
  "/golden": once(() => import("./pages/Golden").then((m) => ({ default: m.GoldenPage }))),
  "/changes": once(() => import("./pages/Changes").then((m) => ({ default: m.ChangesPage }))),
  "/contracts": once(() => import("./pages/Contracts").then((m) => ({ default: m.ContractsPage }))),
  "/audit": once(() => import("./pages/Audit").then((m) => ({ default: m.AuditPage }))),
  "/access": once(() => import("./pages/Access").then((m) => ({ default: m.AccessPage }))),
  "/backups": once(() => import("./pages/Backups").then((m) => ({ default: m.BackupsPage }))),
};

const cache = new Map<string, LazyExoticComponent<ComponentType>>();

/** The lazy component of the screen at `path` (made once, so React keeps its state between renders). */
export function lazyPage(path: string): LazyExoticComponent<ComponentType> {
  let page = cache.get(path);
  if (!page) {
    page = lazy(PAGE_LOADERS[path]);
    cache.set(path, page);
  }
  return page;
}

/** Start loading the screen behind a menu link; a failure here is ignored (the route shows it when opened). */
export function prefetchPage(path: string): void {
  const load = PAGE_LOADERS[path];
  if (load) load().catch(() => undefined);
}
