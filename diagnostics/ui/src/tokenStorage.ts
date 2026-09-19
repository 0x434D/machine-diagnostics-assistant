/** Where the token lives between reloads.
 *
 * M5 kept a *pasted* token here; M6's login puts a minted one in the same place, and the
 * choice is worth restating rather than inheriting. `localStorage` is per-origin and never
 * leaves the browser, and it survives a reload — without which a question and its follow-up
 * would each need a fresh sign-in. What it also is: readable by any script running on this
 * origin, so a script injection takes the token with it. The alternatives trade that for
 * something else (in-memory loses the token on every reload; a cookie needs a server-side
 * session this system deliberately does not have, §10.5), and the token is short-lived by
 * configuration — `ISSUER_TOKEN_LIFETIME_MINUTES` — which is the part of the exposure a
 * deployment can actually turn down.
 *
 * This file holds only the key it is stored under, never a token.
 */
export const TOKEN_STORAGE_KEY = "machine-agent.diagnostics.token";

export function loadToken(): string | null {
  return window.localStorage.getItem(TOKEN_STORAGE_KEY);
}

export function saveToken(token: string | null): void {
  if (token === null || token === "") {
    window.localStorage.removeItem(TOKEN_STORAGE_KEY);
  } else {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
  }
}
