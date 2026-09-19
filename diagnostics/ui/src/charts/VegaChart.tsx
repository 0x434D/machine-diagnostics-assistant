/** The one place a chart is drawn.
 *
 * Vega-Lite compiles the specification to a Vega one and Vega renders it as SVG — SVG and
 * not canvas, because an SVG chart is text a screen reader and a printer can reach, and
 * because the marks are inspectable, which is the whole point of a chart that claims to be
 * evidence.
 *
 * **Every failure renders.** A specification Vega refuses is a failure with a sentence, not
 * an empty box: §7.4 is about charts that look authoritative and are not, and a blank
 * region under a heading reads as "nothing happened".
 */
import { useEffect, useRef, useState } from "react";
import { compile, type TopLevelSpec } from "vega-lite";
import { View, parse, type Spec } from "vega";

/** The width a chart draws at when the element reports none.
 *
 * A detached or not-yet-laid-out element measures zero, and Vega asked to draw at zero
 * pixels produces axes with no marks between them — the empty chart this whole file exists
 * to avoid. Drawing at a design width instead is wrong by a few pixels until the first
 * resize, where zero is wrong in a way the reader cannot see.
 */
const DESIGN_WIDTH = 640;

function measure(element: HTMLElement | null): number {
  const width = element?.clientWidth ?? 0;
  return width > 0 ? width : DESIGN_WIDTH;
}

/** The specification compiled and sized to the panel it is about to be drawn in.
 *
 * A single or a layered view takes the width and the request to fit at its top level, and
 * the whole drawing — axes, legend and title included — then comes to exactly that width.
 *
 * A stack of rows takes neither. Vega-Lite ignores a top-level `width` on a `vconcat` and
 * warns `fit` back down to `pad`, so a two-row chart asked for the panel's width would draw
 * at Vega-Lite's own default instead: the responsiveness lost with nothing failing. Its
 * rows carry the width, which is also what lets them line up against each other under one
 * shared x scale, and the fit is asked of the compiled **Vega** specification, where that
 * limit does not exist. `fit-x` and not `fit`, because a row's height here is a station
 * count or a fixed band and fitting those as well collapses the chart to no height at all
 * — which is what Vega-Lite's own `fit` refusal is protecting against.
 *
 * `hconcat` is left alone deliberately: nothing builds one, and handing every column the
 * whole panel's width would be worse than the default it gets today.
 */
function drawable(spec: Record<string, unknown>, width: number): Spec {
  const stacked = spec.vconcat;
  const rows: readonly unknown[] = Array.isArray(stacked) ? stacked : [];
  const sized =
    rows.length > 0
      ? {
          ...spec,
          // A copy per row rather than a width written into the caller's object: the
          // specification belongs to whoever built it and is re-read on the next render.
          vconcat: rows.map((row) => Object.assign({}, row, { width })),
        }
      : { ...spec, width, autosize: { type: "fit", contains: "padding" } };

  // A specification is data on this side of the boundary by design — `DataFree` for §7.4's
  // fallback and a plain object out of `charts/spec.ts` for the rest — while `TopLevelSpec`
  // is a union of shapes no record is comparable to. This is where the two meet, and
  // Vega-Lite's own reading of the object is what actually checks it.
  const compiled = compile(sized as unknown as TopLevelSpec).spec;
  return rows.length > 0
    ? { ...compiled, autosize: { type: "fit-x", contains: "padding" } }
    : compiled;
}

export function VegaChart({
  spec,
  description,
}: {
  spec: Record<string, unknown>;
  /** What this chart shows and what it was drawn from, for a reader who cannot see it. */
  description: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [width, setWidth] = useState(DESIGN_WIDTH);
  // The specification is an object rebuilt on every render, so its identity is useless as a
  // dependency; its JSON is what has to change for the chart to be redrawn.
  const identity = JSON.stringify(spec);

  useEffect(() => {
    const element = host.current;
    if (element === null) return;

    let view: View | null = null;
    try {
      view = new View(parse(drawable(spec, width)), {
        renderer: "svg",
        container: element,
      });
      setFailure(null);
      // Not awaited: this effect is synchronous and the rejection below is the only
      // outcome that needs handling. `finalize` in the cleanup stops the view whether or
      // not the run has finished.
      void view.runAsync().catch((reason: unknown) => {
        setFailure(String(reason));
      });
    } catch (reason: unknown) {
      // The one recovery this file performs, and it is a rendering one: a specification
      // Vega-Lite cannot compile is a fact about the chart the reader is looking at, so it
      // becomes a sentence on the screen rather than an exception that empties the panel.
      // §7.4's fallback is the case this exists for — the built-ins are ours and compile.
      setFailure(String(reason));
      return;
    }

    return () => {
      view?.finalize();
      element.replaceChildren();
    };
  }, [identity, width]);

  useEffect(() => {
    const element = host.current;
    if (element === null) return;
    setWidth(measure(element));

    const onResize = () => {
      setWidth(measure(element));
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
    };
  }, []);

  return (
    <figure className="chart">
      {failure === null ? null : (
        <p data-testid="chart-failure" className="evidence__missing">
          This chart could not be drawn: {failure}
        </p>
      )}
      <div
        ref={host}
        className="chart__canvas"
        role="img"
        aria-label={description}
        data-testid="chart-canvas"
      />
      <figcaption className="chart__caption">{description}</figcaption>
    </figure>
  );
}
