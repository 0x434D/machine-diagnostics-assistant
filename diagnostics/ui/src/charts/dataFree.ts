/** §7.4's guarantee, as a type: a chart specification that could carry values does not exist.
 *
 * *"The data always comes from a verified tool result. The specification references a tool
 * call by id; it never carries values the model typed. This kills the classic failure where
 * a model draws a confident chart from invented figures — and a wrong chart reads far more
 * authoritatively than a wrong sentence."*
 *
 * `DataFree` is the type of **every specification that crosses from a model into this
 * application** — §7.4's free-form fallback, and nothing else. The six built-in types are
 * assembled here from rows that came out of a tool result, so they are not specifications a
 * model wrote and are not this type. `buildFallback` takes a `DataFree` and no other shape,
 * so an edit that handed it a raw object off the wire would not compile, and `data` and
 * `datasets` — the two keys through which every form of Vega-Lite data arrives
 * (`data.values`, `data.url`, `data.name`, and the top-level `datasets` block) — are
 * `never` on it, so nothing in this application can write one carrying figures either.
 *
 * `dataFree` below is the only door into that type, and it refuses at every depth:
 * Vega-Lite nests whole specifications inside `layer`, `concat` and `facet`, so a top-level
 * check would pass a chart whose second layer carried the invented numbers. The agent
 * refuses the same thing when the answer is built (`agent.answer.ChartOptions`); this is not
 * that check repeated out of nerves but the same rule applied where the data crosses into
 * the renderer, which is a different boundary and the last one before a reader sees a chart.
 */

/** Every key through which Vega-Lite can be handed values. */
const DATA_KEYS = ["data", "datasets"] as const;

/** A Vega-Lite specification with the data half removed at the type level.
 *
 * `data?: never` is what makes `{ mark: "bar", data: { values: [] } }` a compile error
 * rather than a review comment.
 */
export type DataFree = {
  readonly [key: string]: unknown;
} & {
  readonly data?: never;
  readonly datasets?: never;
};

/** Where a specification carries values, or `null` when it carries none.
 *
 * The path rather than a boolean, so the failure a reader is shown says which part of the
 * chart was invented instead of making them diff two objects to find out.
 */
export function carriesData(value: unknown): string | null {
  if (Array.isArray(value)) {
    for (const [index, item] of value.entries()) {
      const found = carriesData(item);
      if (found !== null) return `[${String(index)}].${found}`;
    }
    return null;
  }
  if (typeof value !== "object" || value === null) return null;
  for (const [key, nested] of Object.entries(value)) {
    if ((DATA_KEYS as readonly string[]).includes(key)) return key;
    const found = carriesData(nested);
    if (found !== null) return `${key}.${found}`;
  }
  return null;
}

/** The specification as something safe to render, or the reason it is not.
 *
 * The only door from an untyped object into `DataFree`, so the cast below is the single
 * place the guarantee is asserted rather than checked — and the line above it is the check.
 */
export function dataFree(
  spec: Record<string, unknown>,
): { readonly spec: DataFree } | { readonly refused: string } {
  const found = carriesData(spec);
  if (found !== null) {
    return {
      refused:
        `this chart carries its own values at ${found}, so nothing behind it was ` +
        `verified. A chart references a tool result and never carries figures (§7.4).`,
    };
  }
  return { spec: spec as DataFree };
}
