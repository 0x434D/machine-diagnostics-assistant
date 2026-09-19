/** The frame every view lives in.
 *
 * One responsive application, not a desktop layout with a mobile afterthought. §7.2 is
 * plain about why: every user is a browser client, whether that browser is on a tablet at
 * the line or on a desk, and there is no second build for either. So the layout is a
 * single column that gains width rather than a grid that collapses, the touch targets come
 * from `--control-height` (which the pointer type already sets), and nothing in the
 * stylesheet insists on more width than `--viewport-min`.
 *
 * What is fixed here and shared by every view: where the identity is, where the navigation
 * is, and the key to the colour language. A view that moved any of them would be teaching
 * the reader a second application.
 */
import { NavLink, Outlet } from "react-router";

import { StateLegend } from "../design/StateLegend";
import { IdentityBadge } from "./IdentityBadge";
import { ROUTES } from "./routes";

export function AppShell() {
  return (
    <div className="shell">
      {/* Before everything, and visible only once focused: the navigation is short, but a
          keyboard reader should not walk it on every view change. */}
      <a className="shell__skip" href="#view">
        Skip to the view
      </a>

      <header className="shell__bar">
        <div className="shell__brand">
          <span className="shell__title">Machine Diagnostics</span>
          <span className="shell__claim">
            Read-only. This answers from what the line recorded, and it cannot
            act on the plant.
          </span>
        </div>
        <IdentityBadge />
      </header>

      <nav className="shell__nav" aria-label="Views">
        <ul>
          {ROUTES.filter((route) => route.nav !== null).map((route) => (
            <li key={route.path}>
              {/* `end` on the root only: without it "Ask" is marked current on every
                  address in the application, because every path begins with "/". */}
              <NavLink to={route.path} end={route.path === "/"}>
                {route.nav}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>

      <main className="shell__view" id="view">
        <Outlet />
      </main>

      <footer className="shell__footer">
        <StateLegend />
      </footer>
    </div>
  );
}
