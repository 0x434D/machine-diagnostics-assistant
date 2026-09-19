"""§7.2's containment form offers time phrases, and this service owns the list.

`UNDERSTOOD_EXPRESSIONS` is the whole of what `/time/resolve` resolves, and the form in
`diagnostics/ui/src/time/ShiftWindow.tsx` offers six of them as one click each. That is a
copy of part of this list, and CLAUDE.md says two copies is where three comes from.

An enum in `contracts/` would delete the copy and was ruled against: `/time/resolve` has to
keep accepting an arbitrary string so that it can refuse one and say what would have worked,
and a closed enum deletes that behaviour along with the copy.

So the copy is guarded the way `plant/simulator/tests/test_category_parity.py` guards the
UI's copy of the state categories: this side is the source of truth, a test asserts the
other still agrees, and the parse is mutation-checked so a regex matching nothing cannot
pass. Reading a file is not importing one, so nothing here couples the two stacks.

The assertion is containment rather than equality, and deliberately: the form offers six of
nine and reaches the rest through its free-text box. What must not happen is a chip that the
service refuses -- an operator clicking a phrase and being told it is not understood learns
that the screen and the service disagree, on the screen where that matters most.
"""

from __future__ import annotations

import re
from pathlib import Path

from analysis.time_expressions import UNDERSTOOD_EXPRESSIONS

REPO = Path(__file__).resolve().parents[3]
UI_SHIFT_WINDOW = REPO / "diagnostics" / "ui" / "src" / "time" / "ShiftWindow.tsx"


def _ui_source() -> str:
    return UI_SHIFT_WINDOW.read_text(encoding="utf-8")


def _offered(source: str) -> list[str]:
    """The phrases the form offers as one click each.

    Parsed as a declaration rather than grepped for a phrase at a time: a list that lost an
    entry, or gained one nothing resolves, is the drift this file exists to catch, and a
    per-phrase lookup would not notice either.
    """
    body = re.search(r"const SHIFT_PHRASES = \[(.*?)\] as const;", source, re.DOTALL)
    assert body is not None, f"{UI_SHIFT_WINDOW} no longer declares SHIFT_PHRASES"
    return re.findall(r'"([^"]+)"', body.group(1))


def _opens_on(source: str) -> str:
    """The phrase the form resolves on mount, before anybody chooses anything."""
    declared = re.search(r'const DEFAULT_PHRASE = "([^"]+)";', source)
    assert declared is not None, f"{UI_SHIFT_WINDOW} no longer declares DEFAULT_PHRASE"
    return declared.group(1)


def test_every_phrase_the_form_offers_is_one_this_service_resolves() -> None:
    assert set(_offered(_ui_source())) <= UNDERSTOOD_EXPRESSIONS


def test_the_phrase_the_form_opens_on_is_one_this_service_resolves() -> None:
    """The default is asked for before the reader has done anything, so a drift here does
    not merely disable a chip -- the containment form opens on a refusal and no window."""
    assert _opens_on(_ui_source()) in UNDERSTOOD_EXPRESSIONS


def test_the_parse_reads_the_declarations_rather_than_matching_nothing() -> None:
    """Guards the guard: a regex that silently matched nothing would compare an empty set
    against `UNDERSTOOD_EXPRESSIONS`, pass, and go on passing for ever after the list it
    watches had drifted. That is the failure mode of every test that parses a foreign file.

    Both halves are needed. The first says the real file yields phrases at all; the second
    says a list holding something this service cannot resolve is reported as holding it,
    which a parser returning the same answer to every input would fail.
    """
    source = _ui_source()
    assert _offered(source)
    assert _opens_on(source)

    drifted = (
        'export const DEFAULT_PHRASE = "a week past tuesday";\n'
        "export const SHIFT_PHRASES = [\n"
        '  "last night",\n'
        '  "a week past tuesday",\n'
        "] as const;\n"
    )
    assert _offered(drifted) == ["last night", "a week past tuesday"]
    assert _opens_on(drifted) == "a week past tuesday"
    assert not set(_offered(drifted)) <= UNDERSTOOD_EXPRESSIONS
