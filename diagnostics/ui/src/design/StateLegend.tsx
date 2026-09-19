/** The key to the colour language, on screen rather than in a comment.
 *
 * §15's idea only works if the reader knows what the colours mean — *the visual language
 * would teach the diagnostic model* is a claim about a viewer who has been told which
 * colour is the cause and which is the consequence. Every plant screen that means it
 * carries its legend; this one carries it in the shell, collapsed, under every view.
 *
 * It is also the one place all five categories are on screen at once, which is what makes
 * "every colour-coded state carries a second channel" checkable by a person and by Task 9's
 * proof against a running browser, rather than only by a unit test.
 */
import { StateBadge } from "./StateBadge";
import { CATEGORIES, ENCODINGS, statesIn } from "./stateCategory";

export function StateLegend() {
  return (
    <details className="legend">
      <summary>How the line is coloured</summary>
      <p className="legend__lede">
        By category, not by state. A station that is <em>waiting on others</em>{" "}
        is a consequence of someone else stopping; a station{" "}
        <em>held by its own fault</em> is the one to go and look at. Every
        colour is written out beside itself, so the distinction survives a
        greyscale print and a reader who does not see the difference.
      </p>
      <dl className="legend__list">
        {CATEGORIES.map((category) => {
          const states = statesIn(category);
          return (
            <div className="legend__row" key={category}>
              <dt>
                {/* Rendered through the badge every view uses, not through markup of its
                    own: a legend that drifts from the thing it explains is worse than no
                    legend. `states[0]` is safe because the mapping is exhaustive over the
                    five, and the fallback is deliberately a non-state so a category that
                    somehow has none reads as broken rather than as plausible. */}
                <StateBadge state={states[0] ?? "—"} />
              </dt>
              <dd>
                {ENCODINGS[category].meaning}
                <span className="legend__states">
                  PackML: {states.join(" · ")}
                </span>
              </dd>
            </div>
          );
        })}
      </dl>
    </details>
  );
}
