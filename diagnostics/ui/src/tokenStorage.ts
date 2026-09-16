/** Where a pasted token lives between reloads (M5 Task 6).
 *
 * There is no issuer in this milestone (see the M5 plan's "line this milestone must not
 * cross"): a developer pastes a token minted by `scripts/mint-fixtures.py` or the auth
 * workspace's dev issuer, and it has to survive a reload or the UI is unusable for more
 * than one question at a time. `localStorage` is per-origin and never leaves the browser,
 * so this file holds only the key it is stored under -- never a token.
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
