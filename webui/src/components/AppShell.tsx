import { NavLink, Outlet } from "react-router-dom";
import { LOGIN_URL, csrfToken } from "../api/client";
import { t } from "../i18n/ja";
import { MENU } from "../menu";

export function AppShell() {
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
                <NavLink key={entry.path} to={entry.path} end={entry.path === "/"} className={({ isActive }) => (isActive ? "active" : undefined)}>
                  {entry.label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <main className="main">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
