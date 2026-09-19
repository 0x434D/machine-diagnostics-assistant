/** §6.5's first-class disagreement, on screen.
 *
 * > The agent receiving the propagation derivation may disagree with it [...] and must then
 * > populate `contradiction` with its own root and its reasoning. **The UI renders this
 * > prominently.** Where the mechanics fall short, it is visible rather than silently wrong.
 *
 * M4 put the field in the answer object; nothing has ever shown it, which left the whole
 * mechanism unobservable — an answer that quietly overrides §5.4's computation is exactly
 * what DP-11 forbids, and a field nobody renders cannot prevent it.
 *
 * **Not a warning decoration.** It is two claims about the same stop, from two methods that
 * disagree, and the reader has to choose between them — so both are stated at the same
 * weight, with the agent's reasoning underneath as the only thing that can settle it. There
 * is no styling here that suggests which of the two is right, because this application does
 * not know.
 */
import type { Contradiction } from "../generated/answer";

export function ContradictionBanner({
  contradiction,
}: {
  contradiction: Contradiction;
}) {
  return (
    <section
      className="contradiction"
      // Labelled rather than left to the heading alone: read aloud, this has to announce
      // itself as a disagreement before either root is spoken, or the two roots arrive as
      // one confusing list.
      aria-label="The agent disagrees with the computed propagation"
    >
      <h3 className="contradiction__title">
        <span aria-hidden="true">⚠</span> The agent disagrees with the computed
        propagation
      </h3>
      <p className="contradiction__lede">
        Two claims about the same stop, and only one of them can be the root.
        The computation walked §5.4&apos;s chain over the recorded state
        history; the agent read the same evidence and concluded otherwise.
      </p>
      <dl className="contradiction__sides">
        <div className="contradiction__side">
          <dt>Computed root</dt>
          <dd className="mono">{contradiction.derived_root}</dd>
        </div>
        <div className="contradiction__side">
          <dt>The agent&apos;s root</dt>
          <dd className="mono">{contradiction.agent_root}</dd>
        </div>
      </dl>
      <p className="contradiction__reasoning">
        <strong>Why the agent disagrees:</strong> {contradiction.reasoning}
      </p>
    </section>
  );
}
