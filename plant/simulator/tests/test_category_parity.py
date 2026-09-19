"""§7's "design tokens and nothing else", as an assertion rather than a good intention.

The diagnostics UI paints station states by §15's five categories, and its mapping is a
copy of this stack's: the two frontends sit on opposite sides of the boundary and may not
share a package, so the copy is the only form the shared language can take. CLAUDE.md
still says two copies is where three comes from, and the danger here is specific -- a
state added or recategorised in `simulator.hmi` leaves the UI painting the old meaning,
and the claim that a viewer learns one colour language rather than two goes quietly false.

So the copy is guarded the way `diagnostics/auth`'s twelve tokens guard the Python and C#
validators: one side is the source of truth and a test asserts the other still agrees.
This stack is the source -- §3.3's state machine is the plant's -- and reading is not
importing, so nothing here couples the two at build time.
"""

from __future__ import annotations

import re
from pathlib import Path

from simulator.hmi import _CATEGORY_BY_STATE, CATEGORIES, category_for
from simulator.packml import State

REPO = Path(__file__).resolve().parents[3]
UI_CATEGORIES = REPO / "diagnostics" / "ui" / "src" / "design" / "stateCategory.ts"


def _ui_mapping() -> dict[str, str]:
    """The UI's state-to-category mapping, parsed out of its source.

    Parsed rather than grepped for a value at a time: a mapping that lost an entry
    entirely is exactly the drift this test exists to catch, and a per-key lookup would
    not notice.
    """
    source = UI_CATEGORIES.read_text(encoding="utf-8")
    body = re.search(
        r"const CATEGORY_BY_STATE: Record<string, Category> = \{(.*?)\n\};",
        source,
        re.DOTALL,
    )
    assert body is not None, f"{UI_CATEGORIES} no longer declares CATEGORY_BY_STATE"
    return {
        state: category
        for state, category in re.findall(
            r'^\s*"?([A-Za-z]+)"?:\s*"([a-z-]+)",', body.group(1), re.MULTILINE
        )
    }


def _ui_category_names() -> list[str]:
    source = UI_CATEGORIES.read_text(encoding="utf-8")
    body = re.search(r"const CATEGORIES = \[(.*?)\] as const;", source, re.DOTALL)
    assert body is not None, f"{UI_CATEGORIES} no longer declares CATEGORIES"
    return re.findall(r'"([a-z-]+)"', body.group(1))


def test_the_five_category_names_are_the_same_five() -> None:
    assert _ui_category_names() == list(CATEGORIES)


def test_every_state_lands_in_the_same_category_on_both_screens() -> None:
    ui = _ui_mapping()
    plant = {state.value: category_for(state) for state in _CATEGORY_BY_STATE}
    assert ui == plant


def test_the_parse_finds_every_state_this_stack_defines() -> None:
    """Guards the guard: a regex that silently matched nothing would make the comparison
    above pass against an empty mapping, which is the failure mode of every test that
    parses a foreign file."""
    assert set(_ui_mapping()) == {state.value for state in State}
