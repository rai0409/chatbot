import { Suspense, useEffect } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { LOGIN_URL, csrfToken } from "../api/client";
import { t } from "../i18n/ja";
import { MENU } from "../menu";
import { prefetchPage } from "../routes";
import { Loading } from "./Feedback";
import { ScreenBoundary } from "./ScreenBoundary";
import { TenantPicker } from "./TenantPicker";

/** The label of the menu entry for a path (the browser tab shows it). */
function labelOf(pathname: string): string | undefined {
  const path = pathname.replace(/\/+$/, "") || "/";
  for (const group of MENU) for (const entry of group.entries) if (entry.path === path) return entry.label;
  return undefined;
}

export function AppShell() {
  const location = useLocation();
  useEffect(() => {
    const label = labelOf(location.pathname);
    document.title = label ? `${label} | ${t.app.title}` : t.app.title;
  }, [location.pathname]);

  async function logout() {
    try {
      await fetch("/admin/logout", { method: "POST", credentials: "same-origin", headers: { "X-CSRF-Token": csrfToken() } });
    } finally {
      window.location.assign(LOGIN_URL);
    }
  }
  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">{t.app.title}</span>
        <span className="spacer" />
        <TenantPicker />
        <button type="button" onClick={logout}>
          {t.app.logout}
        </button>
      </header>
      <div className="body">
        <nav className="side" aria-label={t.app.title}>
          {MENU.map((group) => (
            <div key={group.title} className="group">
              <h2>{group.title}</h2>
              {group.entries.map((entry) => (
                <NavLink
                  key={entry.path}
                  to={entry.path}
                  end={entry.path === "/"}
                  className={({ isActive }) => (isActive ? "active" : undefined)}
                  onMouseEnter={() => prefetchPage(entry.path)}
                  onFocus={() => prefetchPage(entry.path)}
                >
                  {entry.label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <main className="main">
          {/* A new key per screen: a failure on one screen does not stay when another one is opened. */}
          <ScreenBoundary key={location.pathname}>
            <Suspense fallback={<Loading />}>
              <Outlet />
            </Suspense>
          </ScreenBoundary>
        </main>
      </div>
    </div>
  );
}
