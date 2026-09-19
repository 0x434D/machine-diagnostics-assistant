/** §5.3's split of a set of parts, and the reason `/parts/affected` exists.
 *
 * *"340 serials, 62 rejected, 278 shipped and need checking."* Collapsing the three into a
 * total is what makes a traceability answer useless, so they are never summed here.
 *
 * Three endpoints answer in this shape — a lot's parts, a carrier's parts, a containment
 * scope — and the rendering is shared rather than repeated. `count` is exact while `serials`
 * is capped, and `truncated` is the only thing that stops a short list being read as the
 * whole of it at three in the morning; a second copy of this is a second place for that
 * sentence to be left out.
 */
import { Fragment } from "react";

import type { PartsByOutcome } from "../api";
import { PartLinks } from "../citations/PartLinks";

export function Outcomes({ parts }: { parts: PartsByOutcome }) {
  const groups = [
    ["Rejected — already contained", parts.rejected],
    ["Shipped — the ones someone has to act on", parts.shipped],
    ["Still on the line — no disposition yet", parts.on_the_line],
  ] as const;

  return (
    <>
      <p className="evidence__note">{parts.total} assemblies in all.</p>
      <dl>
        {groups.map(([label, group]) => (
          <Fragment key={label}>
            <dt>{label}</dt>
            <dd>
              {group.count === 0 ? (
                "none"
              ) : (
                <>
                  {group.count}: <PartLinks serials={group.serials} />
                  {group.truncated
                    ? " …the service capped this list, so it is shorter than the count"
                    : ""}
                </>
              )}
            </dd>
          </Fragment>
        ))}
      </dl>
    </>
  );
}
