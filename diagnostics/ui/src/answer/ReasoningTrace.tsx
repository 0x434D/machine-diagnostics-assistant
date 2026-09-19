/** §7.2's reasoning trace, permanent and collapsible under every answer.
 *
 * > SOPs loaded, every tool call with arguments and timings, budget consumed. It is stored
 * > for the audit trail anyway, so surfacing it is nearly free, and it makes the "did it
 * > follow the method" axis verifiable by eye.
 *
 * **Rendered from what the endpoint returns, verbatim.** The arguments are the recorded
 * object serialised and the durations are the recorded numbers — not rounded, not
 * summarised, not rearranged into something that reads better than what happened. This
 * panel's only value is that it is what was stored: a trace tidied on the way to the screen
 * is a trace that can no longer contradict the answer above it, which is the one thing it
 * exists to be able to do.
 *
 * Until M6 this panel showed a summary of the answer's own `method` — the answer describing
 * itself. The trace is recorded by the loop that makes the calls (Task 2), so what is below
 * can disagree with what is above, and that is the point.
 */
import { fetchTrace, type Answer, type Trace } from "../api";
import { useResolution } from "../citations/useResolution";
import type { Exchange } from "../citations/exchange";

export function ReasoningTrace({
  answer,
  exchange,
}: {
  answer: Answer;
  /** Null when the stream never named the exchange. The trace is addressed by the session
   * and the message seq, so without them there is nothing to ask for — and saying so is not
   * the same as saying the run recorded nothing. */
  exchange: Exchange | null;
}) {
  return (
    <details className="trace">
      <summary>How this was answered</summary>
      <dl>
        {/* From the answer rather than from the trace, because it is the only one of these
            the trace does not carry — and a scripted answer and a model's answer are not the
            same claim. */}
        <dt>Provider</dt>
        <dd>{answer.method.provider ?? "unknown"}</dd>
      </dl>
      {exchange === null ? (
        <p className="trace__unaddressed">
          This answer was not addressed by the stream, so its recorded trace
          cannot be reached. Nothing here says the run recorded none.
        </p>
      ) : (
        <Recorded exchange={exchange} />
      )}
    </details>
  );
}

function Recorded({ exchange }: { exchange: Exchange }) {
  const resolution = useResolution(
    // The same key `ChartPanel` builds, so the two readers of one trace at least agree on
    // what identifies it.
    `trace:${exchange.sessionId}:${String(exchange.seq)}`,
    (token) => fetchTrace(exchange, token),
  );

  if (resolution.state === "opening") {
    return <p className="trace__pending">Reading the recorded trace…</p>;
  }
  if (resolution.state === "failed") {
    return (
      <p className="trace__failed">
        {/* The service's own words, kept whole. The agent distinguishes three 404s here —
            no such session, no such message, and a message whose run never finished and so
            has no trace — and collapsing them into "no trace" would throw away the only
            thing that tells a reader which of the three happened. */}
        The recorded trace could not be read: {resolution.reason}
      </p>
    );
  }
  return <Body trace={resolution.value} />;
}

function Body({ trace }: { trace: Trace }) {
  const sops = trace.sops_loaded ?? [];
  const calls = trace.tool_calls ?? [];
  const exhausted = trace.budget.tool_turns >= trace.budget.tool_turns_limit;

  return (
    <>
      <dl>
        <dt>SOPs loaded</dt>
        {/* §6.2 routes knowledge by the question, so "none" is an ordinary outcome and not a
            failure — but it is also the thing to check first when an answer cites a
            procedure, because §6.5 verifies citations against exactly this set. */}
        <dd>{sops.length === 0 ? "none" : sops.join(" · ")}</dd>
        <dt>Budget</dt>
        <dd>
          {trace.budget.tool_turns} of {trace.budget.tool_turns_limit} tool
          turns
          {exhausted
            ? " — spent in full, so §6.8 makes this a partial answer rather than a finished one"
            : ""}
        </dd>
        <dt>Time</dt>
        <dd className="mono">
          {trace.timings.total_ms} ms total · {trace.timings.model_ms} ms model
          · {trace.timings.tools_ms} ms tools
        </dd>
      </dl>

      <h4 className="trace__heading">
        Tool calls ({calls.length}), in the order they were made
      </h4>
      {calls.length === 0 ? (
        <p className="trace__none">
          No tool was called. An answer reached without asking the analysis
          service anything is one to read closely.
        </p>
      ) : (
        <ol className="trace__calls">
          {calls.map((call) => (
            <li
              className="trace__call"
              key={call.id}
              data-failed={String(call.failed)}
            >
              <span className="trace__call-name mono">{call.name}</span>
              {/* The word, not only the colour of the row (ISA-101) — and this is the line
                  §6.8 is about: a tool error goes back to the model as a tool result, so a
                  failed call is a normal thing to find in a successful run. */}
              {call.failed ? (
                <span className="trace__call-failed">failed</span>
              ) : null}
              <span className="trace__call-timing mono">
                {call.duration_ms} ms
              </span>
              <code className="trace__call-arguments mono">
                {JSON.stringify(call.arguments ?? {})}
              </code>
              {/* The id a §7.4 chart citation references. Shown so a chart under this answer
                  can be held against the call it claims to be drawn from. */}
              <span className="trace__call-id mono">{call.id}</span>
            </li>
          ))}
        </ol>
      )}
    </>
  );
}
