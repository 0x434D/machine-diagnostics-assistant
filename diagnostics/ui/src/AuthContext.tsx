/** Who is asking, for every component that makes a request.
 *
 * Context rather than a prop: `CitationChip`'s `RENDERERS` is §7.3's seam -- "adding a
 * citation type means adding a renderer and nothing else" -- and threading a token through
 * its fixed `(citation) => ReactElement` signature would spend that seam on this
 * milestone's plumbing. The default value below is deliberately usable on its own, so a
 * test that renders `<CitationChip>` or `<EvidencePanel>` standalone (as `citation.test.tsx`
 * already does) keeps working without being made to know this context exists.
 */
import { createContext, useContext, useState, type ReactNode } from "react";

import { loadToken, saveToken } from "./tokenStorage";

interface Auth {
  token: string | null;
  setToken: (token: string | null) => void;
}

const AuthContext = createContext<Auth>({
  token: null,
  setToken: () => undefined,
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState<string | null>(loadToken);

  function setToken(next: string | null): void {
    setTokenState(next);
    saveToken(next);
  }

  return (
    <AuthContext.Provider value={{ token, setToken }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): Auth {
  return useContext(AuthContext);
}
