/** Who the token says is asking, so the shell can show it.
 *
 * **For display only.** Nothing here is a trust decision: the payload is read without
 * checking the signature, because a browser cannot check one usefully — it would be
 * verifying a claim against a key the same untrusted source could supply. Every access
 * decision is made by the services, against `AUTH_PUBLIC_KEY` (§10.5), and this file
 * exists so that a reader can see which identity their requests are going out under and
 * notice when it is the wrong one.
 *
 * The token now comes from a login against the development issuer (`Login.tsx`) rather than
 * from a paste. Nothing in this file changed when it did, which was the point: where a token
 * comes from and what it says are two questions, and only the first one moved.
 */

export interface Identity {
  /** The OIDC `sub`. `operator-7` from the development issuer. */
  subject: string;
  /** §10.5's role, or null when the token carries no such claim — which is a token the
   * services will refuse, and worth showing as missing rather than as absent. */
  role: string | null;
}

/** Which claim carries the role.
 *
 * Matches `auth.config.Settings.role_claim`, whose default is this. It is configuration
 * on the server because an issuer decides the claim shape — Zitadel nests it under a
 * namespaced key — and it is a constant here because there is nothing in this bundle that
 * could be told the answer: the value would have to be baked in at build time, and a knob
 * no deployment can turn is worse than a stated assumption. The issuer service does have
 * somewhere it could be asked — its key set is served, and a claim-shape document could join
 * it — but nothing reads either today, and inventing the endpoint before there is a reader
 * would be a second place for this constant to be wrong.
 */
const ROLE_CLAIM = "role";

/** The identity a token carries, or null when there is no token or it is not a JWT. */
export function identityFrom(token: string | null): Identity | null {
  if (token === null || token === "") return null;

  const payload = token.split(".")[1];
  if (payload === undefined) return null;

  const claims = decodeClaims(payload);
  if (claims === null) return null;

  const subject = claims["sub"];
  if (typeof subject !== "string") return null;

  const role = claims[ROLE_CLAIM];
  return { subject, role: typeof role === "string" ? role : null };
}

/** A JWT's middle segment as an object, or null when it is not one.
 *
 * The `catch` here is a recovery and not a swallow: `atob` and `JSON.parse` are the two
 * calls that throw on a string which is not a base64url-encoded JSON object, and a string
 * that is not a JWT is precisely what a stale `localStorage` entry from an older build, or
 * a hand-edited one, contains. Null is rendered as "identity unreadable" — a branch the
 * shell draws, not a blank where a name should be.
 */
function decodeClaims(payload: string): Record<string, unknown> | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(utf8(base64UrlToBytes(payload)));
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return null;
  }
  return parsed as Record<string, unknown>;
}

function base64UrlToBytes(value: string): Uint8Array {
  const base64 = value.replaceAll("-", "+").replaceAll("_", "/");
  // `atob` rejects an unpadded string; base64url omits the padding by definition.
  const padded = base64.padEnd(Math.ceil(base64.length / 4) * 4, "=");
  return Uint8Array.from(atob(padded), (character) => character.charCodeAt(0));
}

function utf8(bytes: Uint8Array): string {
  // Not `atob` alone: it yields one code unit per byte, which mangles any subject that is
  // not ASCII into mojibake rather than failing.
  return new TextDecoder().decode(bytes);
}
