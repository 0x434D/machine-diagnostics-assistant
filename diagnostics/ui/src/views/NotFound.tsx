/** A URL this application has no view for.
 *
 * Distinct from a view that found no data, and the wording keeps them apart: §6.5 depends
 * on the difference between "no such thing" and "nothing to show", and a router is the
 * first place that distinction can be lost.
 */
import { Link } from "react-router";

export function NotFound() {
  return (
    <section className="placeholder">
      <h2>No such view</h2>
      <p className="placeholder__note">
        This application has no view at that address. It is not a part, a stop
        or a query that returned nothing — there is nothing here to return.
      </p>
      <p>
        <Link to="/">Ask the line</Link>
      </p>
    </section>
  );
}
