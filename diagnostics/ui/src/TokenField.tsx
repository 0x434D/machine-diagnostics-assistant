/** §10.5's stand-in for the login screen M6 builds. Paste a token, kept in `localStorage`
 * so it survives a reload. No redirect flow and no silent renewal -- those need the issuer
 * this milestone deliberately does not build (M5 plan, Task 6).
 */
import { useAuth } from "./AuthContext";

export function TokenField() {
  const { token, setToken } = useAuth();

  return (
    <label className="token-field">
      token
      <input
        type="password"
        value={token ?? ""}
        placeholder="paste a bearer token"
        onChange={(event) => {
          setToken(event.target.value === "" ? null : event.target.value);
        }}
      />
      <span className="token-field__status">
        {token === null ? "not signed in" : "signed in"}
      </span>
    </label>
  );
}
