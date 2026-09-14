import type { StationView } from "./snapshot";

/** One station: its category as a colour, its PackML state as text, and the reason
 * it is in that state when it carries one.
 *
 * The category is what colours the tile — §15's producing / waiting-on-others /
 * held-by-own-fault / stopped / transitioning — because that is the distinction a
 * viewer needs at a glance: amber on the stations that are merely waiting for someone
 * else, red on the one that is actually broken. The state name is beside it rather
 * than replaced by it: colour is never the only channel (ISA-101).
 */
export function StationTile({ station }: { station: StationView }) {
  return (
    <li className={`station station--${station.category}`}>
      <span className="station__name">{station.browse_name}</span>
      <span className="station__state">{station.state}</span>
      <span className="station__category">{station.category}</span>
      {station.reason !== "" && (
        <span className="station__reason">{station.reason}</span>
      )}
    </li>
  );
}
