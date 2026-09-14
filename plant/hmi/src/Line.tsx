import { BufferBar } from "./BufferBar";
import { ClockPanel } from "./ClockPanel";
import { PartStrip } from "./PartStrip";
import type { LineSnapshot } from "./snapshot";
import { StationTile } from "./StationTile";

/** The line: four stations, the three buffers between them, the last parts, and the clock.
 *
 * Takes the snapshot as a prop rather than reading the socket itself, so what it draws
 * is a function of one frame and can be tested without a server.
 */
export function Line({ snapshot }: { snapshot: LineSnapshot }) {
  return (
    <>
      <ol className="stations">
        {snapshot.stations.map((station) => (
          <StationTile key={station.browse_name} station={station} />
        ))}
      </ol>
      <ol className="buffers">
        {snapshot.buffers.map((buffer) => (
          <BufferBar key={buffer.code} buffer={buffer} />
        ))}
      </ol>
      <PartStrip parts={snapshot.parts} />
      <ClockPanel snapshot={snapshot} />
    </>
  );
}
