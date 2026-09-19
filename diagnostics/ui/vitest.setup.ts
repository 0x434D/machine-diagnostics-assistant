import "@testing-library/jest-dom/vitest";

// jsdom implements no canvas, and Vega measures text with one before it lays a chart out
// (§7.4). Without this every chart test prints a "Not implemented: getContext" line to
// stderr and buries whatever else the run had to say. Vega falls back to an estimate either
// way — a jsdom chart's geometry is approximate in both cases and is not what these tests
// assert on — so this buys quiet output and nothing else.
HTMLCanvasElement.prototype.getContext = (() => ({
  measureText: (text: string) => ({ width: text.length * 6 }),
  // Vega sets these before measuring; a context that refused them would throw where the
  // real one does not.
  font: "",
  fillText: () => undefined,
  save: () => undefined,
  restore: () => undefined,
})) as unknown as HTMLCanvasElement["getContext"];
