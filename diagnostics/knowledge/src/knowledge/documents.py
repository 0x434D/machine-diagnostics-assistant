"""§6.2's knowledge base, read off disk into an immutable index.

> Adding diagnostic competence means adding a file. That is the project's central claim and
> this is the mechanism that makes it literally true.

Which puts one obligation on this module above all others: **anything that does not load is
a startup failure, loudly.** A loader that skipped a malformed file would turn "I added a
file" into "I think I added a file", and a document that never arrives is indistinguishable,
from inside an answer, from a document that had nothing to say. So every deviation from the
front-matter vocabulary raises — an unknown key, an unknown value, a duplicate id, an empty
list — and nothing here catches anything.

The same obligation applies one level up, to the tree itself, and that is the easier failure
to cause: see `load`. A root that is absent or empty is refused there rather than returning
an index with nothing in it.

**Why there is a validation layer on top of `yaml.safe_load`, which looks like reinvention
and is not.** A parser's job is to tell you what the document says; it has no opinion on
whether that is a thing this system understands. `safe_load` accepts

    applies_to:
      question_type: [stop_investigation]

without a murmur — singular, where the vocabulary is plural — and hands back a mapping that
routing will look at, find nothing it recognises in, and skip. The document then never
arrives for any question, forever, and nothing anywhere says so. That is precisely §6.2's
failure mode, and it is why every key and every value below is checked against a closed set
rather than read. The vocabulary constants are not a routing table: they are the sets §6.1
and §3.4 fix, restated once so that a typo is caught where it is written.

This package is deliberately its own workspace member. The analysis service serves §5.3's
`GET /knowledge/{id}` from it, the agent routes from it, and §6.11's MCP server exposes the
same documents as resources — three consumers, none of which may depend on either of the
others.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import cast

import yaml

#: The front-matter keys under `applies_to`, in the order a document is scored across them.
#: `stations` and `alarm_codes` are this tree's extensions to the four the spec names; they
#: follow the same shape, and nothing else about the mechanism changes.
FACET_KEYS: tuple[str, ...] = (
    "question_types",
    "defect_classes",
    "dimensions",
    "stations",
    "alarm_codes",
)

DOCUMENT_KEYS: frozenset[str] = frozenset({"id", "title", "always_load", "applies_to"})

QUESTION_TYPES: frozenset[str] = frozenset(
    {
        "stop_investigation",
        "quality_investigation",
        "status",
        "trend",
        "statistics",
        "traceability",
        "knowledge",
        "out_of_scope",
    }
)
"""§6.1's fixed set. A question is exactly one of these, or it is a classification bug."""

DEFECT_CLASSES: frozenset[str] = frozenset(
    {"gap", "crack", "misalignment", "missing_part", "scratch", "contamination"}
)
"""§3.4's six. The classifier scores all six independently, so a question may name several."""

DIMENSIONS: frozenset[str] = frozenset(
    {"carrier", "lane", "lot", "defect_class", "time"}
)

STATIONS: frozenset[str] = frozenset({"S1", "S2", "S3", "S4"})

_ALARM_CODE = re.compile(r"^A-\d{3}$")
"""Alarm codes are open-ended — §4.1 adds them as the plant grows — so the guard is on the
shape rather than on a list that would have to be edited alongside every new alarm."""


def is_alarm_code(value: str) -> bool:
    """Whether `value` has the shape of an alarm code.

    Public because the front-matter is not the only place a code is read: the agent reads
    them out of a question too, and a second regex there would be the same rule written
    twice and one place for it to drift.
    """
    return bool(_ALARM_CODE.match(value))


_FENCE = "---"

DEFAULT_ROOT: Path = Path(__file__).resolve().parents[4] / "knowledge"
"""Where the tree lives: the repository's top level, and deliberately (§10.1).

The deployment's dial for it is `Settings.knowledge_root` in both services that read it;
this default is what a developer running from a checkout gets. The container lays the tree
down at `/knowledge` and says so in `compose.yml` rather than relying on this arithmetic
landing in the right place.
"""


class KnowledgeError(Exception):
    """A document, or a facet, that does not fit the vocabulary.

    Raised rather than logged. §6.2's failure mode is a document that silently never
    routes, and every constructor in this module exists to make that impossible.
    """


@dataclass(frozen=True)
class Facets:
    """What a document declares it applies to — and, unchanged, what a question is about.

    One type for both sides on purpose. A document's `applies_to` and a question's subject
    are written in the same vocabulary, and `routing.specificity` compares them key by key;
    two types here would be the same five fields twice and one place for them to drift.
    """

    question_types: frozenset[str] = frozenset()
    defect_classes: frozenset[str] = frozenset()
    dimensions: frozenset[str] = frozenset()
    stations: frozenset[str] = frozenset()
    alarm_codes: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        _check("question_types", self.question_types, QUESTION_TYPES)
        _check("defect_classes", self.defect_classes, DEFECT_CLASSES)
        _check("dimensions", self.dimensions, DIMENSIONS)
        _check("stations", self.stations, STATIONS)
        for code in self.alarm_codes:
            if not is_alarm_code(code):
                raise KnowledgeError(f"alarm_codes: {code!r} is not an alarm code")

    def as_mapping(self) -> Mapping[str, frozenset[str]]:
        """The five keys as data, for the callers that walk them uniformly."""
        return MappingProxyType(
            {
                "question_types": self.question_types,
                "defect_classes": self.defect_classes,
                "dimensions": self.dimensions,
                "stations": self.stations,
                "alarm_codes": self.alarm_codes,
            }
        )

    @property
    def declares_nothing(self) -> bool:
        return not any(self.as_mapping().values())

    @property
    def declares_only_question_types(self) -> bool:
        """The shape §6.2 gives a procedure: *"one per investigation type"*.

        A document that names a question type and makes no claim about any workcell, defect
        class, dimension or alarm code is saying it is about the *kind* of question rather
        than about anything in particular the question mentions. In the tree as it stands
        that is exactly the six SOPs and nothing else. `routing` reserves it a place for
        that reason, and the property is here — beside the vocabulary it is phrased in —
        rather than there, so that the rule stays a reading of the front-matter.
        """
        values = self.as_mapping()
        return bool(self.question_types) and not any(
            values[key] for key in FACET_KEYS if key != "question_types"
        )


@dataclass(frozen=True)
class Document:
    """One Markdown file: its front-matter, and the prose below it."""

    id: str
    title: str
    path: Path
    body: str
    always_load: bool = False
    applies_to: Facets = field(default_factory=Facets)

    @property
    def size(self) -> int:
        """What this document costs against the retrieval budget.

        The body only. The front-matter is routing metadata and is not what reaches a
        model's context; counting it would make the budget a measure of a different thing
        from the one §6.2 calls a correctness measure.
        """
        return len(self.body)


@dataclass(frozen=True)
class KnowledgeIndex:
    """An immutable snapshot of the tree. Reload builds a new one; see `KnowledgeBase`."""

    documents: tuple[Document, ...]
    by_id: Mapping[str, Document]
    always_load: tuple[Document, ...]

    @classmethod
    def of(cls, documents: Iterable[Document]) -> KnowledgeIndex:
        ordered = tuple(sorted(documents, key=lambda document: document.id))
        by_id: dict[str, Document] = {}
        for document in ordered:
            existing = by_id.get(document.id)
            if existing is not None:
                raise KnowledgeError(
                    f"duplicate id {document.id!r}: {existing.path} and {document.path}"
                )
            by_id[document.id] = document
        return cls(
            documents=ordered,
            by_id=MappingProxyType(by_id),
            always_load=tuple(document for document in ordered if document.always_load),
        )


def load(root: Path) -> KnowledgeIndex:
    """Every Markdown file under `root` that declares front-matter, as one index.

    Raises `KnowledgeError` if any of them is malformed, if two share an id, **or if the
    tree is absent or holds no documents at all.**

    That last refusal is the one that is not obvious, and it is the one that matters most.
    `Path.rglob` on a directory that does not exist yields nothing rather than raising, so
    a mistyped root, an image that stopped carrying the documents, or a path one level too
    deep would produce an index with nothing in it — and routing would then return nothing
    at all, `always_load` included. Every question would be answered with no method, no
    procedure and no error anywhere to say why.

    That is §6.2's failed retrieval silently becoming a failed method, moved one level up
    to where the flag, the reserved slot and the budget exemption cannot reach it. An empty
    knowledge base is not a valid state for this system, so it is not a state this function
    can return.
    """
    if not root.exists():
        raise KnowledgeError(f"the knowledge base does not exist: {root}")
    if not root.is_dir():
        raise KnowledgeError(f"the knowledge base is not a directory: {root}")

    documents: list[Document] = []
    for path in sorted(root.rglob("*.md")):
        document = parse(path.read_text(), path)
        if document is not None:
            documents.append(document)

    if not documents:
        raise KnowledgeError(f"the knowledge base holds no knowledge documents: {root}")
    return KnowledgeIndex.of(documents)


def parse(text: str, path: Path) -> Document | None:
    """One file's front-matter and body, or `None` if it declares no front-matter.

    `None` is the rule `knowledge/README.md` states and is itself the case for: a file with
    no front-matter is prose about the tree, not a document in it. A file that *opens* a
    front-matter block and then omits `id` is a different thing — it declared an intent to
    be a document — and raises.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FENCE:
        return None

    closing = next(
        (
            number
            for number, line in enumerate(lines[1:], start=1)
            if line.strip() == _FENCE
        ),
        None,
    )
    if closing is None:
        raise KnowledgeError(f"{path}: the front-matter block is never closed")

    front_matter = _mapping(
        yaml.safe_load("\n".join(lines[1:closing])), path, "front-matter"
    )
    unknown = sorted(set(front_matter) - DOCUMENT_KEYS)
    if unknown:
        raise KnowledgeError(f"{path}: unknown front-matter key(s) {unknown}")

    return Document(
        id=_required_string(front_matter, "id", path),
        title=_required_string(front_matter, "title", path),
        path=path,
        body="\n".join(lines[closing + 1 :]).strip("\n"),
        always_load=_flag(front_matter.get("always_load", False), path),
        applies_to=_applies_to(front_matter.get("applies_to"), path),
    )


def search(index: KnowledgeIndex, text: str, limit: int) -> tuple[Document, ...]:
    """§6.2's other retrieval path, and the one that is allowed to miss.

    Free text, for a direct knowledge question — *"what does contamination mean?"* — and
    **never for the procedure**, which is why this is a separate function from
    `routing.route` rather than a fallback inside it. A miss here costs a definition; a
    miss on the procedure costs the method, and `always_load` and the reserved procedure
    slot are why that cannot happen.
    """
    words = [word for word in re.split(r"\W+", text.lower()) if word]
    if not words:
        return ()

    scored: list[tuple[int, str, Document]] = []
    for document in index.documents:
        title = document.title.lower()
        body = document.body.lower()
        # A word in the title says what the document is about; a word in the body may be
        # one mention in a table. Weighted rather than counted flat for that reason.
        score = sum(10 * title.count(word) + body.count(word) for word in words)
        if score:
            scored.append((score, document.id, document))

    scored.sort(key=lambda hit: (-hit[0], hit[1]))
    return tuple(document for _, _, document in scored[:limit])


class KnowledgeBase:
    """The current index, and the ability to swap it for a newer one.

    §6.2: an SOP edit must not require a restart, because that edit is the one made dozens
    of times while tuning. Reload builds a whole new `KnowledgeIndex` and rebinds one
    attribute; a question that took `.index` before the swap keeps the index it started
    with, all of it, and never sees half of an edit. The rebind is a single attribute
    assignment, which is why no lock is needed to make that true.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._index = load(root)
        self._fingerprint = _fingerprint(root)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def index(self) -> KnowledgeIndex:
        return self._index

    def reload(self) -> KnowledgeIndex:
        """Re-read the tree unconditionally and swap in the result."""
        fingerprint = _fingerprint(self._root)
        index = load(self._root)
        self._index = index
        self._fingerprint = fingerprint
        return index

    def reload_if_changed(self) -> KnowledgeIndex:
        """Re-read only when a file was added, removed, resized or touched."""
        if _fingerprint(self._root) == self._fingerprint:
            return self._index
        return self.reload()


# --- front-matter, validated against the vocabulary ---


def _mapping(value: object, path: Path, what: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise KnowledgeError(f"{path}: {what} is not a mapping")
    for key in value:
        if not isinstance(key, str):
            raise KnowledgeError(f"{path}: {what} key {key!r} is not a name")
    return cast(Mapping[str, object], value)


def _required_string(front_matter: Mapping[str, object], key: str, path: Path) -> str:
    value = front_matter.get(key)
    if value is None:
        raise KnowledgeError(f"{path}: front-matter without {key}")
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeError(f"{path}: {key} must be a non-empty string")
    return value


def _flag(value: object, path: Path) -> bool:
    if not isinstance(value, bool):
        raise KnowledgeError(
            f"{path}: always_load must be true or false, not {value!r}"
        )
    return value


def _applies_to(value: object, path: Path) -> Facets:
    if value is None:
        return Facets()
    declared = _mapping(value, path, "applies_to")
    unknown = sorted(set(declared) - set(FACET_KEYS))
    if unknown:
        # The typo that routes nothing, forever. `safe_load` is happy with it, which is
        # the whole reason this function exists between the parser and the index.
        raise KnowledgeError(f"{path}: unknown applies_to key(s) {unknown}")

    values = {key: _values(declared[key], key, path) for key in declared}
    try:
        return Facets(**values)
    except KnowledgeError as error:
        # Re-raised, not recovered from: the only thing added is which file said it, which
        # is the one fact the constructor cannot know and the reader most needs.
        raise KnowledgeError(f"{path}: {error}") from error


def _values(value: object, key: str, path: Path) -> frozenset[str]:
    if not isinstance(value, list):
        raise KnowledgeError(f"{path}: {key} must be a list")
    entries: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            raise KnowledgeError(f"{path}: {key} holds {entry!r}, which is not a name")
        entries.append(entry)
    if not entries:
        # A key that is written down and carries nothing reads, to anyone editing the
        # tree, as scope the document has. It has none, and nothing downstream says so.
        raise KnowledgeError(f"{path}: {key} is an empty list")
    return frozenset(entries)


def _check(key: str, values: frozenset[str], vocabulary: frozenset[str]) -> None:
    unknown = sorted(values - vocabulary)
    if unknown:
        raise KnowledgeError(
            f"{key}: {', '.join(repr(value) for value in unknown)} "
            f"not in {sorted(vocabulary)}"
        )


def _fingerprint(root: Path) -> tuple[tuple[str, int, int], ...]:
    """What has to change for the tree to be worth re-reading."""
    return tuple(
        sorted(
            (str(path), path.stat().st_mtime_ns, path.stat().st_size)
            for path in root.rglob("*.md")
        )
    )
