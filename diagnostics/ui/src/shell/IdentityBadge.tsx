/** Whose questions these are, in one fixed place on every view.
 *
 * §10.5 makes every diagnostics endpoint refuse an unauthenticated request and §14 makes
 * the role decide what a request may reach, which means "signed in as whom, with what
 * role" is a fact that changes what the reader sees — not decoration. Keeping it in the
 * shell rather than on a settings page is the point: the answer to "why does this return
 * nothing" is on screen while the nothing is.
 *
 * Sign-out is here for the same reason it is not on a settings page: the identity and the
 * way to leave it belong together, and a reader who has noticed they are the wrong person
 * should not have to go looking.
 */
import { useAuth } from "../AuthContext";

export function IdentityBadge() {
  const { token, identity, setToken } = useAuth();

  return (
    <div className="identity">
      <p className="identity__who">
        {identity !== null ? (
          <>
            <span className="identity__subject">{identity.subject}</span>
            <span className="identity__role">
              {identity.role ?? "no role claim"}
            </span>
          </>
        ) : token !== null ? (
          // A token that is not a readable JWT. The services will refuse it, and saying
          // which of the two things is wrong saves the reader guessing from a 401.
          <span className="identity__subject">identity unreadable</span>
        ) : (
          <span className="identity__subject">not signed in</span>
        )}
      </p>
      <button
        type="button"
        className="identity__signout"
        onClick={() => {
          // The whole of signing out: the token is the only thing this browser holds. It
          // leaves `localStorage` with it (`tokenStorage.saveToken`), and there is no
          // server-side session to end — the development issuer keeps none, and neither
          // will the real one as far as this application is concerned (§10.5: no
          // revocation, a token is good until it expires).
          setToken(null);
        }}
      >
        Sign out
      </button>
    </div>
  );
}
