import type { SystemStatus } from "../api/types";

export const STATUS: SystemStatus = {
  tenant_id: null,
  revision: 3,
  environment: { external_integrations: ["slack"] },
  integrations: [
    { name: "llm", label: "LLM（AI による文章生成）", state: "failing", enabled: true, configured: true, layer: "breaker", reason: "breaker_open" },
    { name: "sharepoint", label: "SharePoint 同期", state: "disabled", enabled: false, configured: true, layer: "environment", reason: "env_not_allowed" },
    { name: "slack", label: "Slack 通知", state: "ok", enabled: true, configured: true, layer: "", reason: "" },
    { name: "oidc", label: "シングルサインオン（OIDC）", state: "disabled", enabled: false, configured: true, layer: "environment", reason: "oidc_not_enabled" },
  ],
  llm: {
    profiles: {
      local: { breaker: "open", model: "local-model", location: "local", host: "localhost" },
      cloud: { breaker: "closed", model: "cloud-model", location: "external", host: "llm.example.co.jp" },
    },
    profiles_file_problems: [],
  },
  warnings: ["sharepoint: configured but off (env_not_allowed)"],
};

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
