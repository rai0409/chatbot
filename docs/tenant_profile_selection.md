# Tenant Profile Selection

Tenant profile selection maps a `tenant_id` or `customer_id` to one of the allowed product operation profiles from PR40. It gives commercial deployments a controlled way to pin different tenants to profiles such as `production_safe`, `production_low_cost`, or `pilot_high_accuracy` without changing code.

## Config Schema

The default mapping lives at `configs/product_tenants/default.json`.

```json
{
  "mapping_version": "1",
  "default_profile": "production_safe",
  "unknown_tenant_policy": "default_profile",
  "tenants": {
    "default": {
      "tenant_id": "default",
      "customer_id": "default",
      "profile": "production_safe",
      "allowed_profiles": ["production_safe", "production_low_cost", "pilot_high_accuracy"],
      "status": "active",
      "notes": "Default tenant mapping"
    }
  }
}
```

Supported `unknown_tenant_policy` values are:

- `default_profile`: unknown tenants resolve to the configured safe default.
- `reject`: unknown tenants return rejection metadata and no tenant-selected profile.

Supported tenant statuses are:

- `active`: resolve to the tenant profile or an allowed request override.
- `pilot`: same resolution behavior as active, intended for rollout tracking.
- `disabled`: return disabled metadata and do not select the tenant profile.

## Allowed Profiles

`allowed_profiles` is the tenant-level allowlist. A request-level profile can only resolve when it appears in that list. A request cannot escalate to `dev_debug`, `evaluation`, or any profile outside the tenant allowlist.

The tenant's configured `profile` must also be present in `allowed_profiles`; validation reports an error otherwise.

## Safe Unknown Tenant Behavior

Unknown tenants must not default to `dev_debug` or `evaluation`. Those profiles are non-serving or diagnostic profiles and are not safe commercial defaults. Unknown tenants should normally resolve to `production_safe`, or be rejected by setting `unknown_tenant_policy` to `reject`.

Tenant selection also does not enable similar auto-answering. The existing PR40 route policy still forces `allow_similar_auto_answer` to `false`.

## Product Preview Relationship

PR41 already lets `/chat/product-preview` load product profiles. This PR does not wire tenant mapping into routes by default, so existing `/chat/product-preview` behavior is unchanged.

The helper `build_tenant_route_policy` can be used later to combine:

- tenant/customer profile resolution
- `load_product_profile`
- `build_route_policy`

It returns both profile resolution metadata and the route policy.

## Future Runtime Wiring Plan

Future route wiring should:

- Resolve the tenant from authenticated request context, not untrusted UI text.
- Load a pinned tenant mapping by name from `configs/product_tenants`.
- Pass request-level profile overrides only through the tenant allowlist.
- Include resolution metadata in traces and audit events.
- Preserve `/chat` behavior unless an explicit product runtime rollout requires otherwise.

## Production Guidance

Pin each tenant to a reviewed profile. Use `production_safe` as the normal default and `production_low_cost` for cost-constrained tenants. Move tenants to `pilot_high_accuracy` only after reviewing promotion gates and rollout risks.

Keep rollback simple by switching the tenant profile back to `production_safe` or `production_low_cost`. Do not allow request-level profile escalation, and do not set `dev_debug` or `evaluation` as defaults for unknown tenants.
