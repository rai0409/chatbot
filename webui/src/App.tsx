import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { PAGE_LOADERS, lazyPage } from "./routes";
import { TenantProvider } from "./lib/tenant";

/** One route for each entry of the menu (src/menu.ts); each screen is loaded when first opened (src/routes.tsx). */
export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        {Object.keys(PAGE_LOADERS).map((path) => {
          const Page = lazyPage(path);
          return path === "/" ? <Route key={path} index element={<Page />} /> : <Route key={path} path={path.slice(1)} element={<Page />} />;
        })}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

export function App() {
  return (
    <BrowserRouter basename="/console">
      <TenantProvider>
        <AppRoutes />
      </TenantProvider>
    </BrowserRouter>
  );
}
