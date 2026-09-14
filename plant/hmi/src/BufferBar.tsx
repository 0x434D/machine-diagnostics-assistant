import type { BufferView } from "./snapshot";

/** One buffer between two stations, as a fill bar.
 *
 * The level is what makes propagation delayed rather than immediate (§3.1), so the
 * bar draining is the thing a viewer watches between a station stopping and the one
 * below it starving. `capacity` comes from the payload, never from a constant here:
 * it is `Settings.buffer_capacity`, and every number is configuration (§10.3).
 */
export function BufferBar({ buffer }: { buffer: BufferView }) {
  const fraction = buffer.capacity === 0 ? 0 : buffer.level / buffer.capacity;
  return (
    <li className="buffer">
      <span className="buffer__code">{buffer.code}</span>
      <span
        className="buffer__track"
        role="meter"
        aria-label={`${buffer.code} fill`}
        aria-valuenow={buffer.level}
        aria-valuemin={0}
        aria-valuemax={buffer.capacity}
      >
        <span
          className="buffer__fill"
          style={{ inlineSize: `${String(Math.round(fraction * 100))}%` }}
        />
      </span>
      <span className="buffer__level">
        {buffer.level} / {buffer.capacity}
      </span>
    </li>
  );
}
