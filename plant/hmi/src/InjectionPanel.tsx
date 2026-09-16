import { useEffect, useState } from "react";

import type { FaultKindView } from "./plantApi";
import { fetchFaultKinds, injectFault } from "./plantApi";

/** §3.7's fault-injection panel. This is the demo console, and the screen the plant is
 * driven from.
 *
 * Undesigned, like the rest of this screen (§15): a select, a number per parameter, a
 * duration and a button.
 *
 * **The kinds and their parameters are fetched, never written down here.**
 * `simulator.faults` is where §3.5's vocabulary lives, and a copy on this side would
 * offer a parameter the plant refuses — the operator would find out by pressing the
 * button and reading a 400. Fetched once on mount rather than carried on the snapshot:
 * a frame goes out twice a second and this list never changes within a run.
 *
 * Every injection made here goes through the simulator's `ground_truth.Injector`, which
 * writes it to the ground-truth log before the plant is running it. That is not a detail
 * of the backend: §3.5's rule is that *every* injection is written down, and a fault
 * injected from a screen that skipped it would be one no evaluation could ever account
 * for.
 */
export function InjectionPanel() {
  const [kinds, setKinds] = useState<FaultKindView[] | null>(null);
  const [selected, setSelected] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [duration, setDuration] = useState("");
  const [token, setToken] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    let cancelled = false;
    fetchFaultKinds()
      .then((fetched) => {
        if (cancelled) return;
        setKinds(fetched);
        setSelected(fetched[0]?.kind ?? "");
      })
      .catch((error: unknown) => {
        if (!cancelled) setMessage(String(error));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (kinds === null) {
    return (
      <p className="inject inject--empty">
        {message === "" ? "loading the fault kinds…" : message}
      </p>
    );
  }

  const kind = kinds.find((candidate) => candidate.kind === selected);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const params: Record<string, number> = {};
    for (const name of kind?.parameters ?? []) {
      const raw = values[name];
      // An empty box is a parameter the operator did not give, which is different from
      // one they gave as zero: `ramp_seconds` omitted is a step, and a scope omitted is
      // refused by the plant for the kinds that require one. Sending 0 for both would
      // turn the second into a fault aimed at carrier 0.
      if (raw !== undefined && raw !== "") params[name] = Number(raw);
    }
    setMessage("");
    injectFault(
      selected,
      params,
      duration === "" ? null : Number(duration),
      token,
    )
      .then((injected) => {
        setMessage(`injected ${injected.kind} at ${injected.at}`);
      })
      .catch((error: unknown) => {
        setMessage(String(error));
      });
  };

  return (
    <form className="inject" onSubmit={submit}>
      <label className="inject__field">
        fault
        <select
          value={selected}
          onChange={(event) => {
            setSelected(event.target.value);
            // The parameters are per kind, so what was typed for the last one is not an
            // answer about this one — carried over, a `newtons` left in the box would be
            // sent to a kind that does not read it and refused.
            setValues({});
          }}
        >
          {kinds.map((candidate) => (
            <option key={candidate.kind} value={candidate.kind}>
              {candidate.kind}
            </option>
          ))}
        </select>
      </label>

      {kind?.parameters.map((name) => (
        <label className="inject__field" key={name}>
          {name}
          {name === kind.scope && kind.scope_required ? " (required)" : ""}
          <input
            type="number"
            step="any"
            value={values[name] ?? ""}
            onChange={(event) => {
              setValues({ ...values, [name]: event.target.value });
            }}
          />
        </label>
      ))}

      <label className="inject__field">
        duration (s)
        <input
          type="number"
          step="any"
          value={duration}
          onChange={(event) => {
            setDuration(event.target.value);
          }}
        />
      </label>

      {/* §10.5's gate. Not pre-filled and not remembered anywhere on this page: the
          plant is what decides whether it is right, and a page that could answer that
          question itself would need its own copy of the secret. */}
      <label className="inject__field">
        token
        <input
          type="password"
          value={token}
          onChange={(event) => {
            setToken(event.target.value);
          }}
        />
      </label>

      <button type="submit">inject</button>
      {/* Empty until something has been injected or refused. The plant answers a refusal
          with the reason it refused, and showing it is the whole point of the status. */}
      {message !== "" && <p className="inject__message">{message}</p>}
    </form>
  );
}
