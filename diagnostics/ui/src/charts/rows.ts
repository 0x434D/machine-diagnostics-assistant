/** Getting from a stored tool result to the rows a chart is drawn from.
 *
 * §7.4 makes a chart a reading of a verified tool result, so everything here is about
 * *finding* values and never about supplying them. Every failure is named and returned, and
 * none of them is an empty list — a chart drawn from no rows is an empty axis, and an empty
 * axis reads as zero, which is the confident wrong answer this system exists to refuse.
 */
import type { ToolCallRecord } from "../generated/trace";

/** One row of a tool result. Values are `unknown` because the fifteen operations answer
 * fifteen shapes and the chart names its fields by hand. */
export type Row = Record<string, unknown>;

/** What a chart needs, or why it cannot be drawn. A third state — drawn, but from nothing
 * — is the one this type exists to make unrepresentable. */
export type Found =
  { readonly rows: readonly Row[] } | { readonly refused: string };

/** The tool call a chart references, or the reason it is not one a chart may be drawn from.
 *
 * Two different failures, and a reader acts on them differently: a `source` naming a call
 * that was never made is a citation the agent should not have shipped (§6.5 should have
 * removed it), while a call that failed is one §6.8 handed back to the model as an error —
 * drawing that would put a chart of the words "connection refused" on a real set of axes.
 */
export function callNamed(
  calls: readonly ToolCallRecord[],
  source: string,
): { readonly call: ToolCallRecord } | { readonly refused: string } {
  const made = calls.find((call) => call.id === source);
  if (made === undefined) {
    return {
      refused:
        `this chart names the tool call ${source}, and the trace of this answer holds ` +
        `no such call. Nothing was measured for it to be a chart of.`,
    };
  }
  if (made.failed) {
    return {
      refused:
        `the tool call behind this chart (${made.name}) failed, so its result is an ` +
        `error and not a measurement.`,
    };
  }
  return { call: made };
}

/** The rows at a dotted path inside a tool result.
 *
 * `points`, `stops`, `dimensions.0.patterns` — the model names where the rows are and the
 * result says whether they are there. A path that resolves to nothing, to something that is
 * not a list, or to an empty list is refused by name: "the chart is empty" and "the path is
 * wrong" send a reader to two different places.
 */
export function rowsAt(result: Record<string, unknown>, path: string): Found {
  let current: unknown = result;
  for (const segment of path.split(".")) {
    if (Array.isArray(current)) {
      const index = Number(segment);
      current = Number.isInteger(index) ? current[index] : undefined;
      continue;
    }
    if (typeof current !== "object" || current === null) {
      current = undefined;
      break;
    }
    current = (current as Record<string, unknown>)[segment];
  }

  if (current === undefined || current === null) {
    return {
      refused: `the tool result holds nothing at ${path}, so there are no rows to draw.`,
    };
  }
  if (!Array.isArray(current)) {
    return {
      refused: `${path} in the tool result is not a list of rows.`,
    };
  }
  const rows = current.filter(
    (row): row is Row => typeof row === "object" && row !== null,
  );
  if (rows.length !== current.length) {
    return { refused: `${path} holds entries that are not rows.` };
  }
  if (rows.length === 0) {
    // Said rather than drawn. An empty chart is indistinguishable from a chart of zeros,
    // and the reader has no way to tell "the line made nothing" from "we looked in the
    // wrong place".
    return {
      refused: `${path} in the tool result is empty, so this chart would show nothing.`,
    };
  }
  return { rows };
}

/** Whether every row carries each of the named fields.
 *
 * A field the rows do not have renders in Vega-Lite as a blank axis with no error — real
 * axes, real gridlines, no marks — which is exactly the picture §7.4 is about. Checked here
 * so it becomes a sentence instead.
 */
export function fieldsPresent(
  rows: readonly Row[],
  fields: readonly (string | null | undefined)[],
): string | null {
  const named = fields.filter((field): field is string => Boolean(field));
  const missing = named.filter(
    (field) => !rows.some((row) => row[field] !== undefined),
  );
  if (missing.length === 0) return null;
  const available = Object.keys(rows[0] ?? {}).join(", ");
  return `this chart reads ${missing.join(", ")} out of rows that carry ${available || "nothing"}.`;
}
