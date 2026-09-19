import { BrowserRouter, Route, Routes } from "react-router";

import { AuthProvider } from "./AuthContext";
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

/** Real paths rather than a hash, because a citation is a link someone sends to a
 * colleague and `#/parts/A-88431` reads like a workaround. `nginx.conf` already falls back
 * to index.html for any address it does not have a file for, which is the server half of
 * the same decision. */
export function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </AuthProvider>
  );
}
