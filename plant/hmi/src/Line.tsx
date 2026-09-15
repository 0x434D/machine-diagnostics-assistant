import { AlarmList } from "./AlarmList";
import { BufferBar } from "./BufferBar";
import { ClockPanel } from "./ClockPanel";
import { InjectionPanel } from "./InjectionPanel";
import { PartStrip } from "./PartStrip";
import type { LineSnapshot } from "./snapshot";
import { StationTile } from "./StationTile";

/** The line: four stations, three buffers, the active alarms, the last parts, the clock.
 *
 * Takes the snapshot as a prop rather than reading the socket itself, so everything it
 * *reads* is a function of one frame and can be tested without a server. §3.7's injection
 * panel is the exception and says so: it fetches §3.5's fault vocabulary, because the
 * vocabulary belongs to `simulator.faults` and a copy of it here would offer parameters
 * the plant refuses.
 *
 * The alarms sit under the buffers and above the strip: a viewer reads the line, then
 * what a stopped station is stopped for, then what came off the end of it. §3.7's
 * injection panel is last, because it is the one thing here that is not a reading.
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
      <AlarmList alarms={snapshot.alarms} />
      <PartStrip parts={snapshot.parts} />
      <ClockPanel snapshot={snapshot} />
      <InjectionPanel />
    </>
  );
}
