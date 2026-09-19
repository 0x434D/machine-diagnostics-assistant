/** §7.2's serial search, which reaches the part view.
 *
 * **This plant has two kinds of serial and nothing in the string says which you are
 * holding.** An assembly serial resolves against `/parts/{serial}`; a component serial —
 * the one a supplier gives you months later (§3.5 scenario 7) — resolves against
 * `/components/{serial}/assembly` and names the assembly it went into. RULING M4-R5 settled
 * that they are different endpoints, so a box that only knew about the first would answer
 * "no such part" to the question this system exists to answer quickly.
 *
 * So the lookup is sequential and says which kind it found. The component lookup runs only
 * when the assembly lookup comes back 404 — one extra request on a miss, none on a hit, and
 * never a guess from the shape of the string.
 */
import { useState } from "react";
import { Link } from "react-router";

import { fetchPart, type Part } from "../api";
import { ComponentPanel } from "../citations/panels";
import { useResolution } from "../citations/useResolution";

export function SerialSearch() {
  const [typed, setTyped] = useState("");
  const [looking, setLooking] = useState<string | null>(null);

  return (
    <section className="search">
      <h2>Serial search</h2>
      <p className="search__lede">
        A serial from a label, a reject bin or a supplier&apos;s email. Both
        kinds are looked up: the assemblies this line built, and the components
        it built them from.
      </p>

      <form
        className="search__form"
        onSubmit={(event) => {
          event.preventDefault();
          setLooking(typed.trim());
        }}
      >
        <label htmlFor="serial">Serial</label>
        <input
          id="serial"
          name="serial"
          value={typed}
          autoComplete="off"
          spellCheck={false}
          onChange={(event) => {
            setTyped(event.target.value);
          }}
        />
        <button type="submit" disabled={typed.trim() === ""}>
          Search
        </button>
      </form>

      {looking === null || looking === "" ? null : (
        // Keyed by the serial so a second search remounts rather than showing the first
        // result while the second is still in flight -- a stale answer under a new query
        // is the quiet wrong answer this view could most easily give.
        <SerialLookup key={looking} serial={looking} />
      )}
    </section>
  );
}

function SerialLookup({ serial }: { serial: string }) {
  const resolution = useResolution(`part:${serial}`, (token) =>
    fetchPart(serial, token),
  );

  if (resolution.state === "opening") {
    return <p className="search__note">Looking for {serial}…</p>;
  }

  if (resolution.state === "open") {
    return <AssemblyFound serial={serial} part={resolution.value} />;
  }

  if (resolution.missing) {
    return <AsComponent serial={serial} />;
  }

  // Not a miss, and it matters: nobody looked. Reporting this as "no such serial" would
  // tell a reader the line never built the part, on the evidence of a network failure.
  return (
    <p className="search__failed error">
      Could not look up {serial}: {resolution.reason}
    </p>
  );
}

function AssemblyFound({ serial, part }: { serial: string; part: Part }) {
  return (
    <div className="evidence">
      <p>
        <span className="mono">{serial}</span> is an assembly this line built.
      </p>
      <dl>
        <dt>Created</dt>
        <dd className="mono">{part.created_at ?? "unknown"}</dd>
        <dt>Verdict</dt>
        <dd>{part.inspection?.result ?? "not inspected"}</dd>
        <dt>Left the line</dt>
        <dd>
          {part.disposition === null
            ? "still on the line"
            : `${part.disposition.disposition} at ${part.disposition.at}`}
        </dd>
      </dl>
      <p>
        <Link to={`/parts/${encodeURIComponent(serial)}`}>
          Everything the line recorded about it
        </Link>
      </p>
    </div>
  );
}

/** The other kind of serial, looked up only once the first kind has come back empty. */
function AsComponent({ serial }: { serial: string }) {
  return (
    <>
      <p className="search__note">
        No assembly carries the serial <span className="mono">{serial}</span>.
        It was looked up as a component serial instead — the kind a supplier
        names — and this is what that recall says.
      </p>
      <ComponentPanel serial={serial} />
    </>
  );
}
