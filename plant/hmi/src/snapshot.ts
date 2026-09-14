/** The frame `simulator.hmi.line_snapshot` pushes, as the screen reads it.
 *
 * Hand-written, unlike the diagnostics UI's types. There is no generated alternative:
 * the simulator's snapshot is internal to the plant stack and has no entry in
 * `contracts/`, which exists for the boundary *between* the two stacks (§10.1). What
 * holds these names still is `plant/simulator/tests/test_hmi.py`, which asserts the
 * payload as a contract — so a rename there fails the Python gate rather than silently
 * blanking this screen.
 */

/** §15's five, and the only values `category` takes. The CSS class is derived from
 * this string, so a sixth category needs a rule in styles.css and nothing else. */
export type Category =
  | "producing"
  | "waiting-on-others"
  | "held-by-own-fault"
  | "stopped"
  | "transitioning";

export interface StationView {
  /** §4.1's browse name, `S1_Feeding`. Deliberately NOT called `code`: the diagnostics
   * stack's `stations.code` holds `S1`, split off by the gateway, and a field called
   * `code` here would invite a join that returns zero rows and no error. */
  browse_name: string;
  /** The PackML state name. Shown as text beside the colour, always: colour is never
   * the only channel (ISA-101). */
  state: string;
  category: Category;
  /** `direction:buffer_id` when the state carries one, empty otherwise. This is the
   * whole diagnostic value of the screen — *why* a station is waiting. */
  reason: string;
}

export interface BufferView {
  /** `B1_2`. This one IS the diagnostics stack's `buffers.code` — the gateway stores a
   * buffer's browse name whole, because it carries no function to split off. */
  code: string;
  level: number;
  capacity: number;
  upstream_browse_name: string;
  downstream_browse_name: string;
}

/** §3.7's strip: the last parts the line inspected, newest first. */
export interface PartView {
  serial: string;
  /** Simulated time, like every other instant on this screen (§4.2). */
  at: string;
  /** `good` or `reject` — the vision system's own verdict, which is also what S4 sorts
   * on and what reaches `part_dispositions` one stack over. */
  disposition: Disposition;
  /** The classifier's named reason for a reject, empty for a good part. */
  reason: string;
  /** A path on the simulator's HMI server, or null for a good part — §3.4 gives only
   * rejects an image, and null here is a fact rather than a missing value. The proxy
   * prefix is added by the browser side, the same split `useLineSnapshot` makes for the
   * socket: this path is what the plant knows, and `/api/plant` is what the page knows. */
  image_url: string | null;
}

/** The two verdicts `inspection.classifier` returns. The CSS class is derived from this
 * string, exactly as it is for `Category`. */
export type Disposition = "good" | "reject";

export interface LineSnapshot {
  phase: string;
  /** Simulated time — what every number on this screen belongs to (§4.2). */
  simulated_now: string;
  history_start: string;
  catchup_speed: number;
  /** The real clock, for telling a live frame from a frozen tab. Never analysed. */
  written_wall: string;
  stations: StationView[];
  buffers: BufferView[];
  parts: PartView[];
}
