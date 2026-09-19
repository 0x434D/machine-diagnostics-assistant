/** Opening a citation: the three states it can be in, in one place.
 *
 * §7.2 — *"a citation you cannot open is barely a citation"* — makes the failure states as
 * load-bearing as the success one. Nine renderers each writing their own loading and
 * failure branches would be nine chances for one of them to render an empty panel where a
 * failure belongs, and an empty panel reads as *"no data"*: the exact confusion §6.5 needs
 * to keep out of an answer, since it rests on "no such id" and "nothing to show" being
 * different facts a reader acts on differently.
 */
import { useEffect, useState } from "react";

import { describeFailure, NotFoundError } from "../api";
import { useAuth } from "../AuthContext";

export type Resolution<T> =
  | { readonly state: "opening" }
  | { readonly state: "open"; readonly value: T }
  | {
      readonly state: "failed";
      readonly reason: string;
      /** The service answered and found nothing, as against every other failure, where
       * nobody found out. A citation that 404s is one the agent should not have shipped
       * (§6.5); a citation that timed out says nothing about the citation at all. */
      readonly missing: boolean;
    };

/**
 * Resolves one citation under the reader's own identity (§10.5).
 *
 * `key` is the request's identity — what has to change for the panel to ask again — and
 * the token is who is asking. A panel opened before signing in shows its 401 and re-fetches
 * when a token arrives, rather than requiring the chip to be closed and reopened.
 */
export function useResolution<T>(
  key: string,
  load: (token: string | null) => Promise<T>,
): Resolution<T> {
  const { token } = useAuth();
  const [resolution, setResolution] = useState<Resolution<T>>({
    state: "opening",
  });

  useEffect(() => {
    let current = true;
    setResolution({ state: "opening" });
    load(token)
      .then((value) => {
        if (current) setResolution({ state: "open", value });
      })
      .catch((reason: unknown) => {
        // Not a swallowed failure: every branch below renders it. This is the one place in
        // the citation path that turns a rejection into a state, which is why the reason is
        // kept whole and the 404 is kept distinguishable from the rest.
        if (current) {
          setResolution({
            state: "failed",
            reason: describeFailure(reason),
            missing: reason instanceof NotFoundError,
          });
        }
      });
    return () => {
      current = false;
    };
    // `load` is deliberately absent from the dependencies. It closes over the citation and
    // is rebuilt on every render, so including it would re-fetch on every render for ever;
    // `key` carries the identity of the request it would otherwise have stood in for.
  }, [key, token]);

  return resolution;
}
