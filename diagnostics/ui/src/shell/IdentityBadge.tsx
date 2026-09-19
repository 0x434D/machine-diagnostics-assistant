/** Whose questions these are, in one fixed place on every view.
 *
 * §10.5 makes every diagnostics endpoint refuse an unauthenticated request and §14 makes
 * the role decide what a request may reach, which means "signed in as whom, with what
 * role" is a fact that changes what the reader sees — not decoration. Keeping it in the
 * shell rather than on a settings page is the point: the answer to "why does this return
 * nothing" is on screen while the nothing is.
 *
 * The token field below it is M5's stand-in and is deliberately untouched here. Task 1
 * deletes it and puts a real login in this same slot.
 */
import { useAuth } from "../AuthContext";
import { TokenField } from "../TokenField";

export function IdentityBadge() {
  const { token, identity } = useAuth();

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
      <TokenField />
    </div>
  );
}
