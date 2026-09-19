/** The window a question is asked over, resolved by the service that owns the calendar.
 *
 * §6.1 step 2 splits deliberately: language is read out of a sentence, and *code* computes
 * what it means, because date arithmetic across shift boundaries and DST is what models are
 * unreliable at and code is exact at. **A browser is on the same side of that split as a
 * model.** `Date.now()` here would be worse than a guess, because it would be wrong in the
 * way this project keeps naming: §3.2 puts the shift calendar in `Europe/Berlin`, all
 * analysis reads `SourceTimestamp`, and a "last 24 hours" measured off the reader's own
 * clock returns rows, reads as an answer, and scopes somebody's containment to the wrong
 * interval.
 *
 * So nothing here computes an instant. It asks `/time/resolve` and keeps what comes back
 * whole — the window, the service's own label for it, whether it was closed and the instant
 * that was judged at — and when the service refuses a phrase it renders the refusal, which
 * carries the expressions that would have worked.
 */
import { useEffect, useState, type FormEvent } from "react";

import { describeFailure, resolveTime, type TimeResolution } from "../api";
import { useAuth } from "../AuthContext";
import { LineClock } from "./LineClock";

/** The phrase a view opens on, for the views that open on one.
 *
 * The shift that is running rather than the last completed one: a containment is asked
 * about what the line is making now, and the shift it is making it in is the interval an
 * operator already has in their head. It is an *open* window and the screen says so —
 * `closed` is in the response for exactly that.
 *
 * **The stop timeline opens on no phrase at all**, and that is not an oversight. Its window
 * is also its query: a window in the address is read straight away, so a phrase resolved on
 * arrival would be a reading of an interval nobody asked about. A containment's default
 * fills a form and fetches nothing, which is what lets it have one.
 */
export const DEFAULT_PHRASE = "this shift";

/** The phrases offered as one click each.
 *
 * A second copy of part of a list whose original is
 * `analysis.time_expressions.UNDERSTOOD_EXPRESSIONS`, and deliberately short: the free-text
 * box beside these is what reaches the rest of them. It stays a copy rather than becoming an
 * enum in `contracts/` because `/time/resolve` must keep taking an arbitrary string in order
 * to refuse one and say what would have worked, and a closed enum would delete that.
 *
 * `diagnostics/analysis/tests/test_phrase_parity.py` holds every phrase here against the
 * service's own list, so a phrase renamed there fails a test rather than becoming a chip an
 * operator clicks and is refused for.
 */
export const SHIFT_PHRASES = [
  "this shift",
  "last shift",
  "last night",
  "today",
  "yesterday",
  "last 24 hours",
] as const;

export interface ShiftWindow {
  /** The phrase the service was last asked for, or null while nothing has been asked. */
  readonly asked: string | null;
  /** The last window the service resolved. Null only before the first answer, and
   * **unchanged by a refusal**: a phrase the calendar does not understand must not empty
   * the window a reader already had. */
  readonly resolution: TimeResolution | null;
  /** The service's own sentence for a phrase it would not resolve. */
  readonly refusal: string | null;
  readonly resolving: boolean;
  readonly ask: (expression: string) => void;
}

/** One request to the calendar. `attempt` is what makes asking twice for one phrase two
 * asks rather than one.
 *
 * Without it a reader who resolved "last shift", then edited the instants by hand, then
 * pressed "last shift" again would be pressing a dead control: the expression has not
 * changed, so nothing re-resolves and nothing puts the shift's own instants back.
 */
interface Request {
  readonly expression: string | null;
  readonly attempt: number;
}

/** Resolves a phrase against the shift calendar.
 *
 * `initial` is the phrase the view opens on; `null` opens on none and asks the service
 * nothing until the reader chooses. `DEFAULT_PHRASE` says why the two views differ there.
 */
export function useShiftWindow(
  initial: string | null = DEFAULT_PHRASE,
): ShiftWindow {
  const { token } = useAuth();
  const [request, setRequest] = useState<Request>({
    expression: initial,
    attempt: 0,
  });
  const [resolution, setResolution] = useState<TimeResolution | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [resolving, setResolving] = useState(initial !== null);

  const { expression, attempt } = request;
  useEffect(() => {
    if (expression === null) return undefined;
    let current = true;
    setResolving(true);
    resolveTime(expression, token)
      .then((next) => {
        if (!current) return;
        setRefusal(null);
        setResolution(next);
      })
      .catch((reason: unknown) => {
        // Turned into a state, not swallowed: every branch that reads `refusal` renders
        // it. The service's 422 carries every expression it *does* understand, and that
        // list is the half of the refusal a reader can act on.
        if (current) setRefusal(describeFailure(reason));
      })
      .finally(() => {
        if (current) setResolving(false);
      });
    return () => {
      current = false;
    };
  }, [expression, attempt, token]);

  return {
    asked: expression,
    resolution,
    refusal,
    resolving,
    ask: (next: string) => {
      setRequest((previous) => ({
        expression: next,
        attempt: previous.attempt + 1,
      }));
    },
  };
}

/** The service's words for this interval, while the interval is still the one it resolved.
 *
 * Null the moment either instant is edited by hand. A label is a claim about *which*
 * window, and leaving "night shift 22:00 – 06:00" standing over instants somebody has since
 * changed would name an interval nothing on the screen is asking about any more.
 */
export function labelFor(
  from: string,
  to: string,
  resolution: TimeResolution | null,
): string | null {
  if (resolution === null) return null;
  const resolved = resolution.window;
  return from === resolved.from_ts && to === resolved.to_ts
    ? resolution.label
    : null;
}

/** The phrases, the box for the ones not offered, and the window currently in force. */
export function ShiftWindowForm({
  shift,
  from,
  to,
}: {
  shift: ShiftWindow;
  from: string;
  to: string;
}) {
  function submitPhrase(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const expression = String(
      new FormData(event.currentTarget).get("expression") ?? "",
    ).trim();
    if (expression !== "") shift.ask(expression);
  }

  const label = labelFor(from, to, shift.resolution);

  return (
    <div className="shift">
      <p className="shift__lede">
        The window comes from the shift calendar, not from this browser. Every
        analysis reads the line&apos;s own simulated clock, which may sit either
        side of the one on this device, so a phrase below is resolved by the
        service and the instants it resolved to are the ones in the form.
      </p>

      <ul className="shift__phrases">
        {SHIFT_PHRASES.map((phrase) => (
          <li key={phrase}>
            {/* Pressed only while the instants are still the ones this phrase resolved to.
                A chip left marked over a hand-edited window would say the form is asking
                about the night shift when it is asking about something else. */}
            <button
              type="button"
              className="citation__chip"
              aria-pressed={label !== null && shift.asked === phrase}
              disabled={shift.resolving}
              onClick={() => {
                shift.ask(phrase);
              }}
            >
              {phrase}
            </button>
          </li>
        ))}
      </ul>

      <form className="shift__phrase" onSubmit={submitPhrase}>
        <label htmlFor="expression">
          Another phrase the calendar understands
        </label>
        <input
          id="expression"
          name="expression"
          defaultValue=""
          autoComplete="off"
          placeholder="last week"
        />
        <button type="submit" disabled={shift.resolving}>
          {shift.resolving ? "Resolving…" : "Resolve"}
        </button>
      </form>

      {shift.refusal === null ? null : (
        <p className="evidence__missing" data-testid="shift-refusal">
          {shift.refusal}
        </p>
      )}

      <InForce shift={shift} from={from} to={to} label={label} />
      <LineClock />
    </div>
  );
}

/** Which interval the form is asking over, said in full where a reader will see it.
 *
 * §7.2's containment set is acted on away from the screen that produced it, so the interval
 * has to be readable without opening the form that holds it — and a window still being
 * written to has to say so, because the same question asked an hour later answers over more
 * parts. `closed` is a statement about the clock rather than about the window, which is why
 * the instant it was judged at is printed beside it.
 */
function InForce({
  shift,
  from,
  to,
  label,
}: {
  shift: ShiftWindow;
  from: string;
  to: string;
  label: string | null;
}) {
  if (from === "" || to === "") {
    // Nothing asked and no window: there is no interval in force to state. A view that
    // opens on no phrase says for itself why it is waiting, and a sentence here would put
    // that reason on the screen twice.
    if (shift.asked === null) return null;

    return (
      <p className="shift__inforce" data-testid="window-in-force">
        {shift.resolving
          ? `Asking the shift calendar for “${shift.asked}”…`
          : "No window: the calendar did not answer, so give two UTC instants below."}
      </p>
    );
  }

  return (
    <p className="shift__inforce" data-testid="window-in-force">
      Asking over <span className="mono">{from}</span> →{" "}
      <span className="mono">{to}</span>
      {label === null
        ? " — typed by hand, so the calendar has no name for it."
        : ` — ${label}.`}{" "}
      {label === null || shift.resolution === null
        ? null
        : shift.resolution.closed
          ? "That interval is complete and cannot change."
          : `That interval was still being written to at ${shift.resolution.now}, so the same question will answer over more parts later.`}
    </p>
  );
}
