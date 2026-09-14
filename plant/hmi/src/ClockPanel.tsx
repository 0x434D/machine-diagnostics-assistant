import type { LineSnapshot } from "./snapshot";

/** The clock this screen's numbers belong to.
 *
 * Simulated time first and labelled as such, because it is what every timestamp in the
 * plant carries (§4.2's SourceTimestamp) and what the diagnostics stack analyses. The
 * wall clock is shown too, and is labelled as diagnostics-only — it is here so a viewer
 * can tell a live screen from a frozen tab, and for nothing else.
 *
 * The phase is on the screen because a plant in `catchup` is generating history at
 * `catchup_speed`, and a line whose buffers move faster than any real line can is not
 * a fault to go and look at.
 */
export function ClockPanel({ snapshot }: { snapshot: LineSnapshot }) {
  return (
    <dl className="clock">
      <dt>phase</dt>
      <dd>
        {snapshot.phase}
        {snapshot.phase === "catchup" &&
          ` (x${String(snapshot.catchup_speed)})`}
      </dd>
      <dt>simulated time</dt>
      <dd>{snapshot.simulated_now}</dd>
      <dt>history starts</dt>
      <dd>{snapshot.history_start}</dd>
      <dt>wall clock (diagnostics only)</dt>
      <dd>{snapshot.written_wall}</dd>
    </dl>
  );
}
