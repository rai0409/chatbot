// The tenant the administrator works on, chosen once in the top bar (components/TenantPicker.tsx) and offered as the
// starting value of the tenant fields of the screens. The candidates come from GET /admin/access (`you.tenants`, or the
// tenants that have their own administrator when the credential reaches the whole installation). Typing a tenant by
// hand stays possible: the list is a help, the server decides what a request may reach.
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { overviewApi } from "../api/overview";

const KEY = "console.tenant";
export const TENANT_LIST_ID = "console-tenants";

export interface TenantState {
  /** The chosen tenant ("" when none is chosen). */
  tenant: string;
  setTenant: (value: string) => void;
  /** The known tenants (empty while they are not known). */
  candidates: string[];
  /** The credential reaches only these tenants (the list is complete). */
  restricted: boolean;
  /** The id of the <datalist> of the candidates ("" without a provider). */
  listId: string;
}

const NONE: TenantState = { tenant: "", setTenant: () => undefined, candidates: [], restricted: false, listId: "" };
const TenantContext = createContext<TenantState>(NONE);

function stored(): string {
  try {
    return window.sessionStorage.getItem(KEY) ?? "";
  } catch {
    return ""; // storage can be blocked: the tenant is then chosen again
  }
}

function remember(value: string): void {
  try {
    if (value) window.sessionStorage.setItem(KEY, value);
    else window.sessionStorage.removeItem(KEY);
  } catch {
    /* the choice just is not remembered */
  }
}

export function TenantProvider({ children, load = overviewApi.access }: { children: ReactNode; load?: typeof overviewApi.access }) {
  const [tenant, setTenantState] = useState(stored);
  const [candidates, setCandidates] = useState<string[]>([]);
  const [restricted, setRestricted] = useState(false);

  const setTenant = useCallback((value: string) => {
    const clean = value.trim();
    setTenantState(clean);
    remember(clean);
  }, []);

  useEffect(() => {
    let live = true;
    load()
      .then((access) => {
        if (!live) return;
        const own = access.you?.reach === "tenants" && Array.isArray(access.you.tenants);
        const list = own ? (access.you.tenants ?? []) : (access.admin_auth?.tenants_with_own_admin ?? []);
        const unique = [...new Set(list.filter(Boolean))].sort();
        setCandidates(unique);
        setRestricted(own);
        // A credential that reaches one tenant only: that tenant is the obvious choice.
        if (own && unique.length === 1) setTenantState((current) => current || unique[0]);
      })
      .catch(() => {
        /* no list (no right to read it, or offline): the tenant is typed by hand */
      });
    return () => {
      live = false;
    };
  }, [load]);

  const value = useMemo(() => ({ tenant, setTenant, candidates, restricted, listId: TENANT_LIST_ID }), [tenant, setTenant, candidates, restricted]);
  return (
    <TenantContext.Provider value={value}>
      <datalist id={TENANT_LIST_ID}>
        {candidates.map((c) => (
          <option key={c} value={c} />
        ))}
      </datalist>
      {children}
    </TenantContext.Provider>
  );
}

/** The chosen tenant and the candidates. Without a provider (a screen rendered on its own) nothing is chosen. */
export function useTenant(): TenantState {
  return useContext(TenantContext);
}
