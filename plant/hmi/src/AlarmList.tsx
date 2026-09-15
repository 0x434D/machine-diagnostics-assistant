import type { AlarmView } from "./snapshot";

/** §3.7's active alarms.
 *
 * Undesigned, like the rest of this screen (§15): the visual language is deferred, and
 * what is here is the code, the text, the station and when it was raised.
 *
 * It sits beside the station tiles rather than inside them, and the two say different
 * things: a tile coloured `held-by-own-fault` says a station is §3.3's *cause
 * candidate*, and the alarm is what it is a candidate for. Neither is a diagnosis —
 * §3.3 is explicit that "the first station to raise an alarm" is a circular root cause,
 * and two of §3.5's eight scenarios raise no alarm anywhere because their cause is
 * outside the line. What is on this list is evidence, in the operator's own words.
 */
export function AlarmList({ alarms }: { alarms: AlarmView[] }) {
  if (alarms.length === 0) {
    // Not an empty list rendered as nothing: no active alarms is the normal state of a
    // line, and a blank area reads as a panel that failed to load.
    return <p className="alarms alarms--empty">no active alarms</p>;
  }

  return (
    <ol className="alarms">
      {alarms.map((alarm) => (
        <li
          key={alarm.sequence}
          className={
            alarm.acknowledged
              ? "alarm alarm--acknowledged"
              : "alarm alarm--new"
          }
          data-testid="alarm"
        >
          <span className="alarm__code">{alarm.code}</span>
          <span className="alarm__text">{alarm.text}</span>
          <span className="alarm__station">{alarm.station_browse_name}</span>
          <span className="alarm__raised">{alarm.raised_at}</span>
          {/* The acknowledgement as text and not only as a colour, for the reason every
              station tile carries its state name (ISA-101). */}
          <span className="alarm__ack">
            {alarm.acknowledged ? "acknowledged" : "not acknowledged"}
          </span>
        </li>
      ))}
    </ol>
  );
}
