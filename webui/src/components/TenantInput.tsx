import type { InputHTMLAttributes } from "react";
import { useTenant } from "../lib/tenant";

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "list"> & { value: string };

/**
 * A tenant field: the known tenants are offered as suggestions (lib/tenant.tsx), typing stays possible. A tenant
 * written here becomes the chosen tenant of the console when the field is left, so the next screen starts with it.
 */
export function TenantInput({ value, onBlur, maxLength = 64, autoComplete = "off", type = "text", ...rest }: Props) {
  const { listId, setTenant } = useTenant();
  return (
    <input
      {...rest}
      type={type}
      value={value}
      list={listId || undefined}
      maxLength={maxLength}
      autoComplete={autoComplete}
      onBlur={(e) => {
        if (!rest.disabled && value.trim()) setTenant(value);
        onBlur?.(e);
      }}
    />
  );
}
