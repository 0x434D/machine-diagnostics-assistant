"""§6.2's knowledge base, read off disk into an immutable index.

> Adding diagnostic competence means adding a file. That is the project's central claim and
> this is the mechanism that makes it literally true.

Which puts one obligation on this module above all others: **a document that does not load
is a startup failure, loudly.** A loader that skipped a malformed file would turn "I added
a file" into "I think I added a file", and the document that never arrives is indis-
tinguishable, from inside an answer, from a document that had nothing to say. So every
deviation from the front-matter vocabulary raises — an unknown key, an unknown value, a
duplicate id, an empty list — and nothing here catches anything.

**Why the front-matter is read without a YAML parser.** The front-matter grammar in use is
four scalar forms and one nested block of flow sequences, and reading it needs a *stricter*
reader than a general one: `question_type:` for `question_types:` has to fail, and
`safe_load` would return it happily for the routing layer to ignore forever. The stricter
reader is the smaller half of this module. A general parser would also have to be a runtime
dependency of this package, which it is not — `pyyaml` is in the workspace as the contract
generator's dev tool, and the container this package ships in does not carry it.

The vocabulary constants below are not a routing table. They are the closed sets §6.1 and
§3.4 fix, restated once so that a typo in a document is caught where it is written rather
than never.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

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

_FENCE = "---"

DEFAULT_ROOT: Path = Path(__file__).resolve().parents[4] / "knowledge"
"""Where the tree lives: top level, and deliberately (§10.1).

The deployment's dial for it belongs in `Settings`, the way `analysis.config` restates the
defaults its pure modules carry. Until it is lifted there, a container that does not mount
the tree at this path fails on the first read rather than answering without it.
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
            if not _ALARM_CODE.match(code):
                raise KnowledgeError(f"alarm_codes: {code!r} is not an alarm code")

    def as_mapping(self) -> Mapping[str, frozenset[str]]:
        """The five keys as data, for the one caller that walks them uniformly."""
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

    Raises `KnowledgeError` if any of them is malformed or if two share an id.
    """
    documents: list[Document] = []
    for path in sorted(root.rglob("*.md")):
        document = parse(path.read_text(), path)
        if document is not None:
            documents.append(document)
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

    identifier, title, always_load, applies_to = _front_matter(lines[1:closing], path)
    if identifier is None:
        raise KnowledgeError(f"{path}: front-matter without an id")
    if title is None:
        raise KnowledgeError(f"{path}: front-matter without a title")

    return Document(
        id=identifier,
        title=title,
        path=path,
        body="\n".join(lines[closing + 1 :]).strip("\n"),
        always_load=always_load,
        applies_to=applies_to,
    )


def search(index: KnowledgeIndex, text: str, limit: int) -> tuple[Document, ...]:
    """§6.2's other retrieval path, and the one that is allowed to miss.

    Free text, for a direct knowledge question — *"what does contamination mean?"* — and
    **never for the procedure**, which is why this is a separate function from
    `routing.route` rather than a fallback inside it. A miss here costs a definition; a
    miss on the procedure costs the method, and `always_load` is why that cannot happen.
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


# --- front-matter, read strictly -----------------------------------------------------------


def _front_matter(
    lines: list[str], path: Path
) -> tuple[str | None, str | None, bool, Facets]:
    identifier: str | None = None
    title: str | None = None
    always_load = False
    values: dict[str, frozenset[str]] = {}
    inside_applies_to = False

    for line in lines:
        if not line.strip():
            continue
        indented = line[:1].isspace()
        key, separator, value = line.strip().partition(":")
        key, value = key.strip(), value.strip()
        if not separator:
            raise KnowledgeError(f"{path}: {line.strip()!r} is not a key and a value")

        if indented:
            if not inside_applies_to:
                raise KnowledgeError(f"{path}: indented key {key!r} outside applies_to")
            if key not in FACET_KEYS:
                raise KnowledgeError(f"{path}: unknown applies_to key {key!r}")
            if key in values:
                raise KnowledgeError(f"{path}: applies_to key {key!r} appears twice")
            values[key] = _sequence(value, key, path)
            continue

        inside_applies_to = False
        if key == "id":
            identifier = _required_scalar(value, key, path)
        elif key == "title":
            title = _required_scalar(value, key, path)
        elif key == "always_load":
            always_load = _flag(value, path)
        elif key == "applies_to":
            if value:
                raise KnowledgeError(f"{path}: applies_to takes an indented block")
            inside_applies_to = True
        else:
            raise KnowledgeError(f"{path}: unknown front-matter key {key!r}")

    try:
        facets = Facets(**values)
    except KnowledgeError as error:
        raise KnowledgeError(f"{path}: {error}") from error
    return identifier, title, always_load, facets


def _required_scalar(value: str, key: str, path: Path) -> str:
    if not value:
        raise KnowledgeError(f"{path}: {key} is empty")
    return value


def _sequence(value: str, key: str, path: Path) -> frozenset[str]:
    if not (value.startswith("[") and value.endswith("]")):
        raise KnowledgeError(f"{path}: {key} must be a list in [a, b] form")
    entries = [entry.strip() for entry in value[1:-1].split(",") if entry.strip()]
    if not entries:
        # A key that is written down and carries nothing reads, to anyone editing the
        # tree, as scope the document has. It has none, and nothing downstream says so.
        raise KnowledgeError(f"{path}: {key} is an empty list")
    return frozenset(entries)


def _flag(value: str, path: Path) -> bool:
    if value not in ("true", "false"):
        raise KnowledgeError(
            f"{path}: always_load must be true or false, not {value!r}"
        )
    return value == "true"


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
