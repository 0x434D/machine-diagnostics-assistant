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
  code: string;
  /** The PackML state name. Shown as text beside the colour, always: colour is never
   * the only channel (ISA-101). */
  state: string;
  category: Category;
  /** `direction:buffer_id` when the state carries one, empty otherwise. This is the
   * whole diagnostic value of the screen — *why* a station is waiting. */
  reason: string;
}

export interface BufferView {
  code: string;
  level: number;
  capacity: number;
  upstream: string;
  downstream: string;
}

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
}
