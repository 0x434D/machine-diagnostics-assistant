/** §7.2's part detail: *"genealogy tree, station-by-station timeline, the process values
 * recorded for that part, inspection result and image"*.
 *
 * §14's end-to-end trace of one serial, on a screen. Every section below comes from
 * `GET /parts/{serial}` and nothing else, which is §3.4a's rule read as a frontend rule: the
 * per-part record is authoritative for the part and is never reconstructed from anything
 * else. Nothing here computes a number the service did not return.
 *
 * **A part with no parents, no image or no process values is an ordinary part.** An assembly
 * created before the gateway's history horizon has no genealogy and no creation instant; a
 * part between the press and the camera has no verdict; a good part has no image. Each of
 * those absences is written out as the fact it is, because a blank section reads as "this
 * screen is broken" and an omitted one reads as "there was nothing to know".
 */
import { Link, useParams } from "react-router";

import { fetchCarrierParts, fetchPart, type Part } from "../api";
import { CitationPanel } from "../citations/CitationPanel";
import { Openable } from "../citations/Openable";
import { ComponentPanel } from "../citations/panels";
import { useResolution } from "../citations/useResolution";
import { InspectionRecord } from "../parts/InspectionRecord";
import { Outcomes } from "../parts/Outcomes";

export function PartDetail() {
  const { serial } = useParams<{ serial: string }>();

  if (serial === undefined || serial === "") {
    // Unreachable through the route, which always binds `:serial` — but "no serial" and
    // "a serial nothing matches" are different facts and the router is the first place
    // they can be confused for one another (§6.5).
    return (
      <section className="part">
        <h2>Part</h2>
        <p className="part__note">
          This address carries no serial, so there is nothing to look up. A part
          is reached from a citation, from a search result, or by its own
          address.
        </p>
        <p>
          <Link to="/search">Search for a serial</Link>
        </p>
      </section>
    );
  }

  return <PartTrace serial={serial} />;
}

function PartTrace({ serial }: { serial: string }) {
  const resolution = useResolution(`part:${serial}`, (token) =>
    fetchPart(serial, token),
  );

  return (
    <section className="part">
      <h2>
        Part <span className="mono">{serial}</span>
      </h2>
      {/* The same three outcomes a citation has, rendered by the same component: a view
          that invented its own words for "the service looked and found nothing" would teach
          the reader a second vocabulary for a distinction §6.5 needs them to keep. */}
      <CitationPanel what={`part ${serial}`} resolution={resolution}>
        {(part) => <Trace serial={serial} part={part} />}
      </CitationPanel>
    </section>
  );
}

function Trace({ serial, part }: { serial: string; part: Part }) {
  return (
    <>
      <dl>
        <dt>Serial</dt>
        <dd className="mono">{part.assembly_serial}</dd>
        <dt>Created</dt>
        {/* Null is "this gateway never saw the assembly created", which is what every part
            already in the buffers when the gateway started looks like. */}
        <dd className="mono">{part.created_at ?? "unknown"}</dd>
        <dt>Carrier</dt>
        <dd>
          {part.carrier_id === null ? (
            "unknown"
          ) : (
            <CarrierSiblings carrierId={part.carrier_id} />
          )}
        </dd>
      </dl>

      <StationHistory part={part} />
      <Genealogy part={part} />

      <section className="part__section">
        <h3>Inspection</h3>
        <InspectionRecord serial={serial} part={part} />
      </section>
    </>
  );
}

/** What each station recorded against this serial.
 *
 * **Ordered by station and not by clock, and that is a statement rather than a shortcut.**
 * Three of these records carry an instant — the assembly's creation, the verdict and the
 * disposition — and the press records its two numbers against the serial with no time
 * beside them (§3.4a). Putting that record on a time axis means guessing where it goes, and
 * a guessed instant on a traceability screen is the inference §3.4a exists to refuse. So a
 * station with no instant says it has none, in the place the instant would have been.
 *
 * **And it carries no state colour, unlike the stop timeline.** `design/stateCategory` is a
 * claim about cause and consequence over a station's PackML *state* (§3.3), and
 * `/parts/{serial}` returns no state for this part at any station — it returns what each
 * station recorded against the serial. Painting a press record "producing" would assert
 * something the response does not say, in the one colour language this application means a
 * reader to trust.
 */
function StationHistory({ part }: { part: Part }) {
  const stations = stationRecords(part);
  const disposition = part.disposition;

  return (
    <section className="part__section">
      <h3>Station by station</h3>
      <p className="part__note">
        In line order, not in clock order. Only the creation, the verdict and
        the disposition carry an instant; the press records its numbers against
        the serial and no time beside them (§3.4a), and a station with no
        instant says so rather than borrowing a neighbour&apos;s. The creation
        and disposition records name no station of their own.
      </p>

      <ol className="stations">
        <li className="stations__step">
          <h4 className="stations__name">Assembly created</h4>
          <p className="stations__at mono">{part.created_at ?? "unknown"}</p>
          <p className="stations__what">
            {part.carrier_id === null
              ? "no carrier recorded"
              : `on carrier ${String(part.carrier_id)}`}
          </p>
        </li>

        {stations.map((record) => (
          <li className="stations__step" key={record.station}>
            <h4 className="stations__name mono">{record.station}</h4>
            <p className="stations__at mono">
              {record.at ?? "no instant recorded"}
            </p>
            {record.values.length === 0 ? null : (
              <p className="stations__what">
                {record.values
                  .map((value) => `${value.signal} ${value.value}`)
                  .join(" · ")}
              </p>
            )}
            {record.curves.map((curve) => (
              <p className="stations__what" key={curve.signal}>
                {curve.signal}: {curve.samples.length} samples — the route to
                the peak, which the two scalars cannot reconstruct
              </p>
            ))}
            {record.inspection === null ? null : (
              <p className="stations__what">
                {record.inspection.result}
                {record.inspection.confidence === null
                  ? ""
                  : ` — confidence ${record.inspection.confidence.toFixed(3)}`}
              </p>
            )}
            {record.values.length === 0 &&
            record.curves.length === 0 &&
            record.inspection === null ? (
              <p className="stations__what">nothing recorded at this station</p>
            ) : null}
          </li>
        ))}

        <li className="stations__step">
          <h4 className="stations__name">Left the line</h4>
          <p className="stations__at mono">
            {disposition === null ? "still on the line" : disposition.at}
          </p>
          <p className="stations__what">
            {disposition === null
              ? "no disposition recorded, which is where every part between stations is"
              : `${disposition.disposition}${disposition.reason === null ? "" : ` — ${disposition.reason}`}`}
          </p>
        </li>
      </ol>
    </section>
  );
}

/** One station's whole record for this part, however many rows it came from. */
interface StationRecord {
  station: string;
  /** The instant the station's record carries, or null where it carries none. */
  at: string | null;
  values: Part["process_values"];
  curves: Part["process_curves"];
  inspection: Part["inspection"];
}

function stationRecords(part: Part): StationRecord[] {
  const byStation = new Map<string, StationRecord>();

  function record(station: string): StationRecord {
    const found = byStation.get(station);
    if (found !== undefined) return found;
    const fresh: StationRecord = {
      station,
      at: null,
      values: [],
      curves: [],
      inspection: null,
    };
    byStation.set(station, fresh);
    return fresh;
  }

  for (const value of part.process_values)
    record(value.station).values.push(value);
  for (const curve of part.process_curves)
    record(curve.station).curves.push(curve);

  const inspection = part.inspection;
  if (inspection !== null) {
    const entry = record(inspection.station);
    entry.inspection = inspection;
    entry.at = inspection.source_ts;
  }

  // By station code, which for this line is its order along the line. Sorted rather than
  // left in insertion order: insertion order here is "process values before curves before
  // the verdict", which is an artefact of this function and not a fact about the plant.
  return [...byStation.values()].toSorted((left, right) =>
    left.station.localeCompare(right.station),
  );
}

/** §3.4a's as-built structure: which components went into this assembly, and where from. */
function Genealogy({ part }: { part: Part }) {
  return (
    <section className="part__section">
      <h3>Genealogy</h3>
      {part.genealogy.length === 0 ? (
        <p className="part__note">
          No components are recorded against this serial. Ordinary rather than
          exceptional: an assembly created before the gateway&apos;s history
          horizon has no genealogy, because the one event carrying it arrived
          before the gateway did.
        </p>
      ) : (
        <>
          <p className="part__note">
            One level deep, which is all there is: §3.4a records components
            against the assembly and nothing below them. Every field but the
            serial and the position may be unknown — a component read before the
            history horizon is one this system knows exists and little else —
            and each component opens onto its own recall record.
          </p>
          <ul className="genealogy">
            {part.genealogy.map((component) => (
              <li className="genealogy__part" key={component.component_serial}>
                <span className="genealogy__position">
                  position {component.position}
                </span>
                <Openable label={component.component_serial}>
                  <ComponentPanel serial={component.component_serial} />
                </Openable>
                <span className="genealogy__origin">
                  lane {component.lane ?? "unknown"} · lot{" "}
                  {component.lot_code ?? "unknown"} ·{" "}
                  {component.supplier ?? "supplier unknown"} · read{" "}
                  {component.read_at ?? "unknown"}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

/** Everything else this carrier has carried.
 *
 * `/carriers/{id}/parts` rather than `/parts/affected?carrier=`: §3.1 keeps the carriers in
 * a closed loop, so the question worth asking about a carrier is what it has *ever* carried,
 * and that is the query with no window on it. Closed by default — it is a second request,
 * and a part view should not fetch a carrier's whole history to show one number.
 */
function CarrierSiblings({ carrierId }: { carrierId: number }) {
  return (
    <Openable label={`carrier ${String(carrierId)}`}>
      <CarrierPanel carrierId={carrierId} />
    </Openable>
  );
}

function CarrierPanel({ carrierId }: { carrierId: number }) {
  const resolution = useResolution(`carrier:${String(carrierId)}`, (token) =>
    fetchCarrierParts(carrierId, token),
  );

  return (
    <CitationPanel
      what={`carrier ${String(carrierId)}`}
      resolution={resolution}
    >
      {(carrier) => (
        <>
          <p className="evidence__window">
            Everything carrier {carrier.carrier_id} has carried
            {carrier.window === null
              ? " across all recorded history"
              : `, ${carrier.window.from_ts} → ${carrier.window.to_ts}`}
            . A carrier comes round again, so this is a list over history and
            never a list of one pass.
          </p>
          <Outcomes parts={carrier.parts} />
        </>
      )}
    </CitationPanel>
  );
}
