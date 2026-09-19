/** Every address this application answers at, in one list.
 *
 * One list rather than a `<Routes>` tree with a separate navigation menu beside it: those
 * two go out of step silently, and the failure — a view that exists but cannot be reached,
 * or a link to nothing — is invisible until someone clicks. The navigation is rendered
 * *from* this array, so a route added here is a route that is reachable by construction.
 *
 * Tasks 6, 7 and 8 replace the component behind an entry. None of them edits this file,
 * and none of them edits the shell.
 */
import type { ReactElement } from "react";

import { Chat } from "../Chat";
import { Containment } from "../views/Containment";
import { PartDetail } from "../views/PartDetail";
import { SerialSearch } from "../views/SerialSearch";
import { StopTimeline } from "../views/StopTimeline";

export interface AppRoute {
  path: string;
  /** What the navigation calls this view, or null for a view that is reached from a
   * citation or a search result rather than from a menu. A part detail page is not a
   * destination you pick; it is one you are sent to by the evidence. */
  nav: string | null;
  element: ReactElement;
}

export const ROUTES: AppRoute[] = [
  { path: "/", nav: "Ask", element: <Chat /> },
  { path: "/timeline", nav: "Stop timeline", element: <StopTimeline /> },
  { path: "/search", nav: "Serial search", element: <SerialSearch /> },
  { path: "/containment", nav: "Containment", element: <Containment /> },
  { path: "/parts/:serial", nav: null, element: <PartDetail /> },
];
