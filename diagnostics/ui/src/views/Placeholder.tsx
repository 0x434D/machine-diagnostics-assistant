/** A route that exists, for a view that does not yet.
 *
 * The shell and the navigation are M6 Task 3's; the views behind four of these links are
 * Tasks 6, 7 and 8's. A placeholder rather than a first draft on purpose: a screen that
 * renders plausible-looking nothing is the exact failure M6 is about — pixels are believed
 * — so this says what is missing, and which task brings it, in words.
 */
import type { ReactNode } from "react";

export function Placeholder({
  title,
  task,
  children,
}: {
  title: string;
  /** The task that replaces this file's body. Named so a reader of the running
   * application knows this is unbuilt rather than broken. */
  task: string;
  /** What this view will show, and anything the route already knows — a serial from the
   * URL, say — so the wiring is demonstrably real even though the view is not. */
  children?: ReactNode;
}) {
  return (
    <section className="placeholder">
      <h2>{title}</h2>
      <p className="placeholder__note">
        Not built yet — {task}. This route, its navigation and its place in the
        layout are real; what it will show is not here.
      </p>
      {children}
    </section>
  );
}
