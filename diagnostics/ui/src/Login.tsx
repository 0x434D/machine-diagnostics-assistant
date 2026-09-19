/** The front door. Credentials in, a token held, and the application behind it.
 *
 * M5's `TokenField` stood here and asked a human to paste a JWT. It was honest about being
 * a stand-in and it is deleted rather than kept beside this, because two ways to acquire a
 * token is two things to reason about when one of them stops working.
 *
 * **The notice at the bottom is not decoration.** A login screen is the strongest security
 * claim an application makes to the person looking at it, and this one is backed by a
 * development issuer whose accounts are in a Compose file. Saying so, on the screen, in
 * words, is the difference between a demo that is honest and one that implies a security
 * story it does not have — §7.2's users are plant staff, and they have no other way to know.
 *
 * Nothing here is a trust decision. The token this returns is checked by the services
 * against their own key on every request (§10.5); the browser holds it and displays what it
 * says, and could not usefully verify it if it tried.
 */
import { useState, type FormEvent } from "react";

import { signIn } from "./api";
import { useAuth } from "./AuthContext";

export function Login() {
  const { setToken } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [refusal, setRefusal] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent): Promise<void> {
    event.preventDefault();
    setRefusal(null);
    setPending(true);
    try {
      setToken(await signIn(username, password));
    } catch (reason) {
      // A branch on a known condition, not a swallowed failure (CLAUDE.md): a refusal is
      // the ordinary outcome of typing the wrong password, and the recovery is to render
      // what the issuer said and let the reader try again. The alternative — letting it
      // propagate — takes the whole screen down over a typo.
      setRefusal(String(reason instanceof Error ? reason.message : reason));
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="login">
      <h1 className="login__title">Machine Diagnostics</h1>
      <p className="login__claim">
        Read-only. This answers from what the line recorded, and it cannot act
        on the plant.
      </p>

      <form
        className="login__form"
        onSubmit={(event) => {
          // The handler is async and a form submit handler is not; without this the
          // returned promise is dropped and a rejection would surface as an unhandled one
          // rather than as the message below.
          void submit(event);
        }}
      >
        <label htmlFor="login-user">User</label>
        <input
          id="login-user"
          name="username"
          autoComplete="username"
          autoFocus
          value={username}
          onChange={(event) => {
            setUsername(event.target.value);
          }}
        />

        <label htmlFor="login-password">Password</label>
        <input
          id="login-password"
          name="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => {
            setPassword(event.target.value);
          }}
        />

        <button type="submit" disabled={pending}>
          {pending ? "Signing in…" : "Sign in"}
        </button>
      </form>

      {refusal !== null && (
        <p className="error" role="alert">
          {refusal}
        </p>
      )}

      <aside className="login__development">
        <h2 className="login__development-title">
          This is a development issuer
        </h2>
        <p>
          It signs its own tokens with a key from this stack&rsquo;s
          configuration, and its accounts are configuration too — there is
          nothing to register here, nothing to reset, and no password on this
          screen protects anything real.
        </p>
        <p>
          A production deployment points the stack at a real identity provider
          instead, and replaces this screen with its login. Nothing else in the
          application changes when it does: the services check a signature, an
          audience and a role, and they do not care who signed.
        </p>
      </aside>
    </main>
  );
}
