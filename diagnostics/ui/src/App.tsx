import { BrowserRouter, Route, Routes } from "react-router";

import { AuthProvider, useAuth } from "./AuthContext";
import { Login } from "./Login";
import { AppShell } from "./shell/AppShell";
import { ROUTES } from "./shell/routes";
import { NotFound } from "./views/NotFound";

/** The routed application, without a router around it.
 *
 * Split from `App` so a test can put a `MemoryRouter` here instead of the browser's
 * history -- which is the only way to assert that moving between views keeps what the
 * shell is holding, and that is the property the shell exists to have.
 */
export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        {ROUTES.map((route) => (
          <Route key={route.path} path={route.path} element={route.element} />
        ))}
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}

/** The login screen, or everything behind it.
 *
 * **In front of the router, not inside it.** A `/login` route would be one address among
 * several, and a view added later that forgot to check would be reachable without a token —
 * which is the shape of every accidental hole. Here there is no address to reach: without a
 * token the application that answers questions is not mounted at all, so an unauthenticated
 * visitor gets this screen and no data at whatever address they arrived on.
 *
 * It is not the security boundary and must never be mistaken for one. The services refuse
 * every request that carries no valid token (§10.5, §14) and that is what protects the
 * data; this is what stops the screen showing a shell full of failed requests to someone who
 * has not signed in yet.
 */
function Authenticated() {
  const { token } = useAuth();
  if (token === null) return <Login />;
  return (
    // Real paths rather than a hash, because a citation is a link someone sends to a
    // colleague and `#/parts/A-88431` reads like a workaround. `nginx.conf` already falls
    // back to index.html for any address it does not have a file for, which is the server
    // half of the same decision.
    <BrowserRouter>
      <AppRoutes />
    </BrowserRouter>
  );
}

export function App() {
  return (
    <AuthProvider>
      <Authenticated />
    </AuthProvider>
  );
}
