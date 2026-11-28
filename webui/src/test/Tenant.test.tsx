import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AccessOverview } from "../api/overview";
import { TenantInput } from "../components/TenantInput";
import { TenantPicker } from "../components/TenantPicker";
import { TenantProvider, useTenant } from "../lib/tenant";

afterEach(cleanup);
beforeEach(() => window.sessionStorage.clear());

function access(you: AccessOverview["you"], own: string[] = []): () => Promise<AccessOverview> {
  return () =>
    Promise.resolve({
      you,
      roles: [],
      admin_auth: { enabled: true, installation_credential: true, tenant_credentials: 0, tenants_with_own_admin: own, session_hours: 8 },
      api_auth: { enabled: true, keys: 0, tenant_map: { entries: 0, values: [] } },
      single_sign_on: { enabled: false, group_role_map: { entries: 0, values: [] }, group_tenant_map: { entries: 0, values: [] } },
      note: "",
    });
}

function Shown() {
  return <output aria-label="選択中">{useTenant().tenant}</output>;
}

describe("the tenant picker", () => {
  it("offers the credential's tenants as a list, and remembers the choice for the session", async () => {
    render(
      <TenantProvider load={access({ reach: "tenants", tenants: ["tenant_b", "tenant_a"] })}>
        <TenantPicker />
        <Shown />
      </TenantProvider>,
    );
    await waitFor(() => expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["（未選択）", "tenant_a", "tenant_b"]));
    const select = screen.getByRole("combobox", { name: "テナント" });
    expect(select.tagName).toBe("SELECT");
    fireEvent.change(select, { target: { value: "tenant_b" } });
    expect(screen.getByLabelText("選択中")).toHaveTextContent("tenant_b");
    expect(window.sessionStorage.getItem("console.tenant")).toBe("tenant_b");
  });

  it("chooses the only tenant of a one-tenant credential", async () => {
    render(
      <TenantProvider load={access({ reach: "tenants", tenants: ["tenant_a"] })}>
        <Shown />
      </TenantProvider>,
    );
    await waitFor(() => expect(screen.getByLabelText("選択中")).toHaveTextContent("tenant_a"));
  });

  it("keeps a typed field (with suggestions) for an installation credential or when the list cannot be read", async () => {
    const failing = () => Promise.reject(new Error("forbidden"));
    window.sessionStorage.setItem("console.tenant", "default");
    render(
      <TenantProvider load={failing}>
        <TenantPicker />
        <Shown />
      </TenantProvider>,
    );
    const field = screen.getByRole("combobox", { name: "テナント" }); // an <input list> is a combobox
    expect(field.tagName).toBe("INPUT");
    expect(field).toHaveValue("default");
    fireEvent.change(field, { target: { value: "tenant_c" } });
    expect(screen.getByLabelText("選択中")).toHaveTextContent("tenant_c");
  });

  it("gives a screen's tenant field the suggestions, and makes a tenant typed there the chosen one", async () => {
    render(
      <TenantProvider load={access({ reach: "installation", tenants: null }, ["tenant_x"])}>
        <label>
          対象
          <TenantInput value="tenant_y" onChange={vi.fn()} />
        </label>
        <Shown />
      </TenantProvider>,
    );
    const field = screen.getByLabelText("対象");
    expect(field).toHaveAttribute("list", "console-tenants");
    await waitFor(() => expect(document.querySelector("#console-tenants option")).toHaveAttribute("value", "tenant_x"));
    fireEvent.blur(field);
    expect(screen.getByLabelText("選択中")).toHaveTextContent("tenant_y");
  });

  it("works without a provider (a screen rendered on its own): a plain text field", () => {
    render(
      <label>
        対象
        <TenantInput value="" onChange={vi.fn()} />
      </label>,
    );
    expect(screen.getByRole("textbox", { name: "対象" })).not.toHaveAttribute("list");
  });
});
