import type { ReactNode } from "react";
import { overviewApi, type MappingCount } from "../api/overview";
import { jaOverview } from "../i18n/overview";
import { useLoad } from "../lib/useLoad";
import { Block } from "./overview/Block";

const a = jaOverview.access;
const c = jaOverview.common;

function Facts({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="ov-dl">
      {items.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

const onOff = (on: boolean) => (on ? c.on : c.off);
const list = (values: string[] | null | undefined) => (values && values.length ? values.join(", ") : a.nothing);
const mapping = (m: MappingCount) => `${m.entries} ${a.entries}（${a.values}: ${list(m.values)}）`;

export function AccessPage() {
  const state = useLoad(overviewApi.access, [], a.loadFailed);
  return (
    <section>
      <h1>{a.title}</h1>
      <Block title={a.you} state={state} failureText={a.loadFailed}>
        {(x) => (
          <p>
            {x.you.reach === "installation" ? a.reachInstallation : `${a.reachTenants}: ${list(x.you.tenants)}`}
          </p>
        )}
      </Block>
      {state.data && (
        <>
          <p role="note" className="notice">
            {state.data.note}
          </p>
          <div className="ov-grid">
            <section className="panel">
              <h2>{a.adminAuth}</h2>
              <Facts
                items={[
                  [a.enabled, onOff(state.data.admin_auth.enabled)],
                  [a.installationCredential, state.data.admin_auth.installation_credential ? a.set : a.unset],
                  [a.tenantCredentials, state.data.admin_auth.tenant_credentials],
                  [a.tenantsOwnAdmin, list(state.data.admin_auth.tenants_with_own_admin)],
                  [a.sessionHours, state.data.admin_auth.session_hours],
                ]}
              />
            </section>
            <section className="panel">
              <h2>{a.apiAuth}</h2>
              <Facts
                items={[
                  [a.enabled, onOff(state.data.api_auth.enabled)],
                  [a.keys, state.data.api_auth.keys],
                  [a.tenantMap, mapping(state.data.api_auth.tenant_map)],
                ]}
              />
            </section>
            <section className="panel">
              <h2>{a.sso}</h2>
              <Facts
                items={[
                  [a.enabled, onOff(state.data.single_sign_on.enabled)],
                  [a.groupRole, mapping(state.data.single_sign_on.group_role_map)],
                  [a.groupTenant, mapping(state.data.single_sign_on.group_tenant_map)],
                ]}
              />
            </section>
            <section className="panel">
              <h2>{a.roles}</h2>
              <table>
                <thead>
                  <tr>
                    <th scope="col">{a.colRole}</th>
                    <th scope="col">{a.colDesc}</th>
                  </tr>
                </thead>
                <tbody>
                  {state.data.roles.map((r) => (
                    <tr key={r.name}>
                      <th scope="row">{r.name}</th>
                      <td>{r.description}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          </div>
        </>
      )}
    </section>
  );
}
