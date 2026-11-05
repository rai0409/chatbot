/** The name of the person at the keyboard. The admin credential says which tenants a request reaches, not who is
 *  acting, so decisions that need a person (corrections, approvals, setting changes) ask for a name. It is kept for
 *  the browser session only. */
const KEY = "console.actor";

export function storedActor(): string {
  try {
    return window.sessionStorage.getItem(KEY) ?? "";
  } catch {
    return ""; // storage can be blocked: the name is then asked again
  }
}

export function rememberActor(value: string): void {
  try {
    window.sessionStorage.setItem(KEY, value);
  } catch {
    /* the name just is not remembered */
  }
}
