"""Synthetic UNO document model for tool integration tests.

LibreOffice's UNO bridge can't be exercised in CI for fork PRs (no
secrets, no headless soffice) and in some sandboxes can't be started
at all (see ``docs/investigations.md`` #N — soffice's URP Acceptor
hangs in some container environments). To still cover the tool layer
end-to-end — not just helper functions — this module hand-rolls UNO
look-alikes for the surface area the 20 tools touch:

  - :class:`FakeTextDocument` (``com.sun.star.text.TextDocument``)
  - :class:`FakeText` + :class:`FakeParagraph` + ``createEnumeration``
  - :class:`FakeTextCursor` (with property setters mimicking UNO style)
  - :class:`FakeTextTable` + :class:`FakeCell`
  - :class:`FakeAnnotation` (comments)
  - :class:`FakeFrame` / :class:`FakeDesktop` so tools that call
    ``ctx.ServiceManager.createInstance("com.sun.star.frame.Desktop")``
    receive a real-looking handle.

The synthetic model is **not** a complete UNO emulator. It mimics:

  * ``supportsService`` answers for the service names the tools query.
  * the property-bag pattern (``getPropertyValue`` / ``setPropertyValue``)
    common to UNO controls and cursors.
  * the document-level ``getText() → XText`` and
    ``getTextTables() → XEnumerable`` getters.
  * a bare ``XUndoManager`` so ``undo_redo`` can verify call sequencing.

Tests construct a fresh document per case (see ``synthetic_doc``
fixture in ``conftest.py``). Mutations apply directly to the in-memory
model so assertions can read the final state with plain Python.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Property bag — mimics UNO's getPropertyValue / setPropertyValue
# ---------------------------------------------------------------------------


class _PropBag:
    """Mutable property dict with UNO-compatible getter/setter pair.

    UNO objects expose ``getPropertyValue(name)`` and
    ``setPropertyValue(name, value)``. Some objects (e.g. paragraph
    properties) also support direct attribute access; we expose both
    by deferring to the same dict.
    """

    def __init__(self, **initial: Any) -> None:
        self._props: dict[str, Any] = dict(initial)

    def getPropertyValue(self, name: str) -> Any:  # noqa: N802
        return self._props.get(name)

    def setPropertyValue(self, name: str, value: Any) -> None:  # noqa: N802
        self._props[name] = value

    def setPropertyValues(  # noqa: N802
        self, names: tuple[str, ...], values: tuple[Any, ...]
    ) -> None:
        for n, v in zip(names, values, strict=True):
            self._props[n] = v

    def __getattr__(self, name: str) -> Any:
        # Direct-attribute access (e.g. ``cursor.CharWeight = 150``)
        # falls through to the dict so a test can grep the cursor's
        # final state without needing UNO's exact accessor.
        if name.startswith("_"):
            raise AttributeError(name)
        return self._props.get(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_props":
            object.__setattr__(self, name, value)
            return
        # Allow direct attribute assignment too.
        self._props[name] = value


# ---------------------------------------------------------------------------
# Service-name registry — what `supportsService` answers
# ---------------------------------------------------------------------------


class _Service:
    """Helper for objects that expose ``supportsService(name) -> bool``."""

    SERVICES: tuple[str, ...] = ()

    def supportsService(self, name: str) -> bool:  # noqa: N802
        return name in self.SERVICES


# ---------------------------------------------------------------------------
# Paragraphs, cursors, text
# ---------------------------------------------------------------------------


class FakeParagraph(_Service):
    """One top-level paragraph in a Writer document.

    Mimics ``com.sun.star.text.Paragraph``. Holds ``ParaStyleName``,
    ``NumberingIsNumber``, plus a free-form property bag used by the
    formatting tools. Exposes ``getText()`` / ``getStart()`` so font
    inspection (which builds a cursor over the paragraph) works.
    """

    SERVICES = ("com.sun.star.text.Paragraph",)

    def __init__(
        self,
        text: str = "",
        *,
        style: str = "Standard",
        list_id: str | None = None,
    ) -> None:
        self._text = text
        self._props = _PropBag(
            ParaStyleName=style,
            CharColor=None,
            CharWeight=100,  # NORMAL
            CharPosture=0,
            CharUnderline=0,
            CharHeight=12,
            CharFontName="Liberation Serif",
            CharHighlight=-1,
            CharBackColor=-1,
            ParaAdjust=0,
            ParaTopMargin=0,
            ParaBottomMargin=0,
            ParaLeftMargin=0,
            ParaRightMargin=0,
            NumberingIsNumber=list_id is not None,
            NumberingRules=list_id,
        )
        # Back-link to owning XText injected by FakeText after init.
        self._owner_text: FakeText | None = None

    # Standard UNO XTextContent accessors
    def getString(self) -> str:  # noqa: N802
        return self._text

    def setString(self, value: str) -> None:  # noqa: N802
        self._text = value

    def getText(self) -> FakeText:  # noqa: N802
        if self._owner_text is None:
            # Detached paragraph (no owning XText). Synthesize a
            # single-paragraph FakeText so cursor construction still
            # works in isolated tests.
            self._owner_text = FakeText([self])
        return self._owner_text

    def getStart(self) -> _ParagraphAnchor:  # noqa: N802
        """Return a cursor anchored to the start of this paragraph.

        ``_ParagraphAnchor`` remembers the owning paragraph so a
        ``setString("")`` after ``gotoEndOfParagraph(True)`` +
        ``goRight(1, True)`` removes the paragraph from the document —
        the production path that ``delete_content`` uses.
        """
        return _ParagraphAnchor(self, at_end=False)

    def getEnd(self) -> _ParagraphAnchor:  # noqa: N802
        return _ParagraphAnchor(self, at_end=True)

    # Property bag passthroughs
    def getPropertyValue(self, name: str) -> Any:  # noqa: N802
        return self._props.getPropertyValue(name)

    def setPropertyValue(self, name: str, value: Any) -> None:  # noqa: N802
        self._props.setPropertyValue(name, value)

    def setPropertyValues(  # noqa: N802
        self, names: tuple[str, ...], values: tuple[Any, ...]
    ) -> None:
        self._props.setPropertyValues(names, values)

    # Some Word-mirroring tools enumerate paragraph text via createEnumeration
    def createEnumeration(self) -> FakeEnumeration:  # noqa: N802
        # Treat the paragraph's text as a single portion for now.
        return FakeEnumeration(
            [FakeTextPortion(self._text, _PropBag(**self._props._props))]
        )

    # UNO attributes addressable via plain ``getattr`` (e.g. ``para.ParaStyleName``).
    # Production code uses ``getattr(para, "ParaStyleName", "")`` rather than
    # ``getPropertyValue`` for hot-path reads. Mirror writes through to
    # ``_props`` so subsequent ``getPropertyValue`` reflects the change.
    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        props = self.__dict__.get("_props")
        if props is None:
            raise AttributeError(name)
        return props._props.get(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        props = self.__dict__.get("_props")
        if props is None:
            # Pre-init phase; store directly.
            object.__setattr__(self, name, value)
            return
        props._props[name] = value


@dataclass
class FakeTextPortion(_Service):
    SERVICES = ("com.sun.star.text.TextPortion",)
    _text: str = ""
    _props: _PropBag = field(default_factory=_PropBag)

    def getString(self) -> str:  # noqa: N802
        return self._text

    def getPropertyValue(self, name: str) -> Any:  # noqa: N802
        return self._props.getPropertyValue(name)


class FakeEnumeration:
    """Implements ``XEnumeration`` over a Python list."""

    def __init__(self, items: list[Any]) -> None:
        self._items = list(items)
        self._idx = 0

    def hasMoreElements(self) -> bool:  # noqa: N802
        return self._idx < len(self._items)

    def nextElement(self) -> Any:  # noqa: N802
        item = self._items[self._idx]
        self._idx += 1
        return item


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


class FakeCell(_Service):
    SERVICES = ("com.sun.star.text.Cell", "com.sun.star.text.CellProperties")

    def __init__(self, name: str = "A1") -> None:
        self._name = name
        self._text = ""
        self._props = _PropBag(CellName=name)

    def getString(self) -> str:  # noqa: N802
        return self._text

    def setString(self, value: str) -> None:  # noqa: N802
        self._text = value

    def getName(self) -> str:  # noqa: N802
        return self._name


class _Sized:
    def __init__(self, count: int) -> None:
        self._count = count

    def getCount(self) -> int:  # noqa: N802
        return self._count

    def setCount(self, n: int) -> None:  # noqa: N802 — tests inject sizes
        self._count = n


class FakeTextTable(_Service):
    SERVICES = ("com.sun.star.text.TextTable",)

    def __init__(self, rows: int = 2, cols: int = 2, name: str = "Table1") -> None:
        self._rows = rows
        self._cols = cols
        self._name = name
        # Row-major cell grid; A1, A2, … syntax mirrors UNO.
        self._cells: dict[str, FakeCell] = {}
        for r in range(rows):
            for c in range(cols):
                col_letter = chr(ord("A") + c)
                cell_name = f"{col_letter}{r + 1}"
                self._cells[cell_name] = FakeCell(cell_name)
        self._props = _PropBag(Name=name)

    # UNO accessors
    def getName(self) -> str:  # noqa: N802
        return self._name

    def getRows(self) -> _Sized:  # noqa: N802
        return _Sized(self._rows)

    def getColumns(self) -> _Sized:  # noqa: N802
        return _Sized(self._cols)

    def getCellByName(self, name: str) -> FakeCell:  # noqa: N802
        return self._cells[name]

    def getCellNames(self) -> list[str]:  # noqa: N802
        return list(self._cells.keys())

    def getPropertyValue(self, name: str) -> Any:  # noqa: N802
        return self._props.getPropertyValue(name)

    def setPropertyValue(self, name: str, value: Any) -> None:  # noqa: N802
        self._props.setPropertyValue(name, value)


class FakeTablesContainer:
    """``XTextTables`` — name-indexed container of tables."""

    def __init__(self) -> None:
        self._tables: dict[str, FakeTextTable] = {}

    def add(self, table: FakeTextTable) -> None:
        self._tables[table.getName()] = table

    def getCount(self) -> int:  # noqa: N802
        return len(self._tables)

    def getByName(self, name: str) -> FakeTextTable:  # noqa: N802
        return self._tables[name]

    def getByIndex(self, idx: int) -> FakeTextTable:  # noqa: N802
        return list(self._tables.values())[idx]

    def hasByName(self, name: str) -> bool:  # noqa: N802
        return name in self._tables

    def createEnumeration(self) -> FakeEnumeration:  # noqa: N802
        return FakeEnumeration(list(self._tables.values()))


# ---------------------------------------------------------------------------
# Comments / annotations
# ---------------------------------------------------------------------------


class FakeAnnotation(_Service):
    SERVICES = ("com.sun.star.text.TextField.Annotation",)

    def __init__(
        self,
        *,
        name: str,
        content: str,
        author: str = "Tester",
        anchor_text: str = "",
        parent_name: str | None = None,
        resolved: bool = False,
    ) -> None:
        self._props = _PropBag(
            Name=name,
            Content=content,
            Author=author,
            ParentName=parent_name or "",
            Resolved=resolved,
            Date=None,
        )
        self._anchor_text = anchor_text

    def getAnchor(self) -> Any:  # noqa: N802
        cursor = FakeTextCursor()
        cursor.setString(self._anchor_text)
        return cursor

    def getPropertyValue(self, name: str) -> Any:  # noqa: N802
        return self._props.getPropertyValue(name)

    def setPropertyValue(self, name: str, value: Any) -> None:  # noqa: N802
        self._props.setPropertyValue(name, value)

    # UNO-style attribute access: ``getattr(ann, "Author")`` and direct
    # writes (``ann.Resolved = True``) mirror through to the prop bag.
    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        props = self.__dict__.get("_props")
        if props is None:
            raise AttributeError(name)
        return props._props.get(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        props = self.__dict__.get("_props")
        if props is None:
            object.__setattr__(self, name, value)
            return
        props._props[name] = value


class FakeAnnotationsContainer:
    """Holds the document's annotations + supports iteration."""

    def __init__(self) -> None:
        self._items: list[FakeAnnotation] = []

    def add(self, ann: FakeAnnotation) -> None:
        self._items.append(ann)

    def createEnumeration(self) -> FakeEnumeration:  # noqa: N802
        return FakeEnumeration(self._items)


# ---------------------------------------------------------------------------
# Cursors + XText
# ---------------------------------------------------------------------------


class FakeTextCursor:
    """A simplified XTextCursor with property-bag style setters."""

    def __init__(self) -> None:
        self._string = ""
        self._props = _PropBag(
            CharWeight=100,
            CharPosture=0,
            CharUnderline=0,
            CharColor=None,
            CharHeight=12,
            CharFontName=None,
            CharBackColor=None,
            ParaStyleName="Standard",
            ParaAdjust=0,
        )

    def getString(self) -> str:  # noqa: N802
        return self._string

    def setString(self, value: str) -> None:  # noqa: N802
        self._string = value

    def goRight(self, count: int, expand: bool) -> bool:  # noqa: N802
        return True

    def goLeft(self, count: int, expand: bool) -> bool:  # noqa: N802
        return True

    def gotoStartOfParagraph(self, expand: bool) -> bool:  # noqa: N802
        return True

    def gotoEndOfParagraph(self, expand: bool) -> bool:  # noqa: N802
        return True

    def gotoStartOfWord(self, expand: bool) -> bool:  # noqa: N802
        return True

    def gotoEndOfWord(self, expand: bool) -> bool:  # noqa: N802
        return True

    def gotoStartOfSentence(self, expand: bool) -> bool:  # noqa: N802
        return True

    def gotoEndOfSentence(self, expand: bool) -> bool:  # noqa: N802
        return True

    def getPropertyValue(self, name: str) -> Any:  # noqa: N802
        return self._props.getPropertyValue(name)

    def setPropertyValue(self, name: str, value: Any) -> None:  # noqa: N802
        self._props.setPropertyValue(name, value)

    def getStart(self) -> FakeTextCursor:  # noqa: N802
        return self

    def getEnd(self) -> FakeTextCursor:  # noqa: N802
        return self


class _ParagraphAnchor(FakeTextCursor):
    """Anchor cursor whose full-paragraph clear removes the paragraph.

    On ``setString("")`` after a full-paragraph extension, removes the
    owning paragraph from the document.

    Models the production pattern in ``_delete_paragraph``:
    ``createTextCursorByRange(para.getStart()) →
    gotoEndOfParagraph(True) → goRight(1, True) → setString("")``.
    """

    def __init__(self, paragraph: FakeParagraph, *, at_end: bool) -> None:
        super().__init__()
        self._paragraph = paragraph
        self._at_end = at_end
        self._extended_through_paragraph = False
        self._swallowed_paragraph_break = False

    def gotoEndOfParagraph(self, expand: bool) -> bool:  # noqa: N802
        if expand:
            self._extended_through_paragraph = True
        return True

    def goRight(self, count: int, expand: bool) -> bool:  # noqa: N802
        if expand and self._extended_through_paragraph:
            self._swallowed_paragraph_break = True
        return True

    def setString(self, value: str) -> None:  # noqa: N802
        # Empty-string assignment after a full paragraph extension
        # deletes the paragraph from the doc.
        if value == "" and self._extended_through_paragraph:
            owner = self._paragraph._owner_text
            if owner is not None and self._paragraph in owner._paragraphs:
                owner._paragraphs.remove(self._paragraph)
                return
        # Otherwise overwrite the paragraph's text in place.
        if self._extended_through_paragraph:
            self._paragraph.setString(value)
            return
        super().setString(value)


class _RangeCursor(FakeTextCursor):
    """An ``XTextRange``-shaped cursor anchored to a paragraph offset.

    Mutations via ``setString`` propagate back into the paragraph's
    text — that's what makes ``search_document(replace_with=...)`` /
    other find-then-mutate tools observable in synthetic tests.

    The selection length tracks the substituted text so subsequent
    operations on the same cursor (e.g. ``insertString``) see the
    correct anchor.
    """

    def __init__(self, paragraph: FakeParagraph, start: int, end: int) -> None:
        super().__init__()
        self._paragraph = paragraph
        self._start = start
        self._end = end
        super().setString(paragraph.getString()[start:end])

    def getString(self) -> str:  # noqa: N802
        return self._paragraph.getString()[self._start : self._end]

    def setString(self, value: str) -> None:  # noqa: N802
        source = self._paragraph.getString()
        new_source = source[: self._start] + value + source[self._end :]
        self._paragraph.setString(new_source)
        self._end = self._start + len(value)


class _ParagraphList(list):  # type: ignore[type-arg]
    """List subclass that auto-binds ``_owner_text`` on every paragraph.

    Tests that mutate ``_text._paragraphs`` directly (clear / extend /
    append) used to leak paragraphs with no owner — which then dropped
    out of ``_ParagraphAnchor.setString``'s delete path. This subclass
    keeps the back-link consistent on every mutation so the synthetic
    model behaves intuitively from tests.
    """

    def __init__(self, owner: FakeText, initial: list[FakeParagraph]) -> None:
        super().__init__(initial)
        self._owner = owner
        for p in initial:
            p._owner_text = owner

    def append(self, p: FakeParagraph) -> None:  # type: ignore[override]
        p._owner_text = self._owner
        super().append(p)

    def extend(self, paragraphs: Any) -> None:  # type: ignore[override]
        new_items = list(paragraphs)
        for p in new_items:
            if isinstance(p, FakeParagraph):
                p._owner_text = self._owner
        super().extend(new_items)

    def insert(self, idx: int, p: FakeParagraph) -> None:  # type: ignore[override]
        p._owner_text = self._owner
        super().insert(idx, p)


class FakeText:
    """XText — sequence of paragraphs + table inserts."""

    def __init__(self, paragraphs: list[FakeParagraph]) -> None:
        self._paragraphs: _ParagraphList = _ParagraphList(self, list(paragraphs))
        self._inserted_strings: list[str] = []

    def createEnumeration(self) -> FakeEnumeration:  # noqa: N802
        return FakeEnumeration(self._paragraphs)

    def getString(self) -> str:  # noqa: N802
        return "\n".join(p.getString() for p in self._paragraphs)

    def setString(self, value: str) -> None:  # noqa: N802
        self._paragraphs = [FakeParagraph(value)]

    def createTextCursor(self) -> FakeTextCursor:  # noqa: N802
        return FakeTextCursor()

    def createTextCursorByRange(self, range_obj: Any) -> FakeTextCursor:  # noqa: N802
        # When asked to create a cursor over an anchor we returned from
        # ``para.getStart()``, propagate the paragraph reference so a
        # subsequent ``setString("")`` actually deletes the paragraph.
        if isinstance(range_obj, _ParagraphAnchor):
            return _ParagraphAnchor(
                range_obj._paragraph, at_end=range_obj._at_end
            )
        if isinstance(range_obj, _RangeCursor):
            # A cursor-by-range over a search hit keeps the same
            # paragraph + offset for follow-on formatting.
            return _RangeCursor(
                range_obj._paragraph, range_obj._start, range_obj._end
            )
        return FakeTextCursor()

    def getEnd(self) -> FakeTextCursor:  # noqa: N802
        """``XTextRange.getEnd()`` — append-to-document anchor.

        Used by ``insert_content`` for the default 'append to document'
        target. Returns a plain cursor; the inserter will call
        ``insertString`` against it, which records the inserted text
        and appends a new paragraph.
        """
        return FakeTextCursor()

    def getStart(self) -> FakeTextCursor:  # noqa: N802
        return FakeTextCursor()

    def insertString(  # noqa: N802 — UNO API
        self, cursor: FakeTextCursor, text: str, absorb: bool
    ) -> None:
        self._inserted_strings.append(text)
        cursor.setString(text)

    def insertControlCharacter(  # noqa: N802
        self, cursor: FakeTextCursor, char: Any, absorb: bool
    ) -> None:
        # For ParagraphBreak this would split — we just append a new
        # blank paragraph so iteration sees one more entry.
        self._paragraphs.append(FakeParagraph(""))

    def removeTextContent(self, content: Any) -> None:  # noqa: N802
        if isinstance(content, FakeParagraph) and content in self._paragraphs:
            self._paragraphs.remove(content)


# ---------------------------------------------------------------------------
# Selection / controller
# ---------------------------------------------------------------------------


class FakeSelectionCollection:
    """``com.sun.star.container.XIndexAccess`` over selected ranges."""

    def __init__(self, items: list[FakeTextCursor]) -> None:
        self._items = items

    def getCount(self) -> int:  # noqa: N802
        return len(self._items)

    def getByIndex(self, i: int) -> FakeTextCursor:  # noqa: N802
        return self._items[i]


class FakeController:
    def __init__(self, doc: FakeTextDocument) -> None:
        self._doc = doc
        self._view_cursor = FakeTextCursor()
        self._frame = FakeFrame(doc)

    def getSelection(self) -> FakeSelectionCollection:  # noqa: N802
        # By default, an empty selection collection (no highlight).
        return FakeSelectionCollection(self._doc._selection)

    def getViewCursor(self) -> FakeTextCursor:  # noqa: N802
        return self._view_cursor

    def getFrame(self) -> FakeFrame:  # noqa: N802
        return self._frame

    def select(self, cursor: Any) -> bool:
        self._doc._selection = [cursor]
        return True


# ---------------------------------------------------------------------------
# Undo manager
# ---------------------------------------------------------------------------


class FakeUndoManager:
    """Records undo / redo calls for ``undo_redo`` tool assertions."""

    def __init__(self) -> None:
        self.undo_calls = 0
        self.redo_calls = 0
        self._undo_titles: list[str] = ["Edit"]
        self._redo_titles: list[str] = []

    def undo(self) -> None:
        self.undo_calls += 1

    def redo(self) -> None:
        self.redo_calls += 1

    def isUndoPossible(self) -> bool:  # noqa: N802
        return True

    def isRedoPossible(self) -> bool:  # noqa: N802
        return True

    def getAllUndoActionTitles(self) -> list[str]:  # noqa: N802
        return list(self._undo_titles)

    def getAllRedoActionTitles(self) -> list[str]:  # noqa: N802
        return list(self._redo_titles)


# ---------------------------------------------------------------------------
# Document, desktop, frame
# ---------------------------------------------------------------------------


class FakeTextDocument(_Service):
    """A minimal Writer document.

    Owns paragraphs, tables, annotations, an undo manager, and a
    current controller.
    """

    SERVICES = ("com.sun.star.text.TextDocument",)

    def __init__(
        self,
        *,
        paragraphs: list[str] | None = None,
        styles: list[str] | None = None,
    ) -> None:
        paras = paragraphs if paragraphs is not None else [""]
        para_styles = styles if styles is not None else ["Standard"] * len(paras)
        self._text = FakeText(
            [FakeParagraph(p, style=s) for p, s in zip(paras, para_styles, strict=True)]
        )
        self._tables = FakeTablesContainer()
        self._annotations = FakeAnnotationsContainer()
        self._undo = FakeUndoManager()
        self._props = _PropBag(
            Title="Untitled",
            Author="",
            CharacterCount=sum(len(p) for p in paras),
            WordCount=sum(len(p.split()) for p in paras),
            ParagraphCount=len(paras),
        )
        self._selection: list[FakeTextCursor] = []
        self._controller = FakeController(self)
        # Style families — used by style-resolution helpers.
        self._style_families: dict[str, dict[str, Any]] = {
            "ParagraphStyles": {
                "Standard": _PropBag(),
                "Heading 1": _PropBag(),
                "Heading 2": _PropBag(),
                "Heading 3": _PropBag(),
                "List Bullet": _PropBag(),
                "Quotations": _PropBag(),
            },
            "PageStyles": {"Default Page Style": _PropBag()},
        }
        # Page-style URP map needed by structure tools.
        self._page_styles_in_use: list[str] = ["Default Page Style"]

    # UNO accessors -------------------------------------------------------

    def getText(self) -> FakeText:  # noqa: N802
        return self._text

    def getTextTables(self) -> FakeTablesContainer:  # noqa: N802
        return self._tables

    def getCurrentController(self) -> FakeController:  # noqa: N802
        return self._controller

    def getUndoManager(self) -> FakeUndoManager:  # noqa: N802
        return self._undo

    @property
    def UndoManager(self) -> FakeUndoManager:  # noqa: N802 — IDL attr alias
        return self._undo

    def getPropertyValue(self, name: str) -> Any:  # noqa: N802
        return self._props.getPropertyValue(name)

    def setPropertyValue(self, name: str, value: Any) -> None:  # noqa: N802
        self._props.setPropertyValue(name, value)

    def getDocumentProperties(self) -> Any:  # noqa: N802
        return self._props

    def getStyleFamilies(self) -> Any:  # noqa: N802
        """Return a name-access container of name-access style families.

        Mirrors LibreOffice's two-level XStyleFamiliesSupplier shape:
        outer container exposes each family by name (``ParagraphStyles``,
        ``PageStyles``, ...), each of which is itself an XNameAccess
        over individual styles.
        """

        class _NameAccess:
            def __init__(self, contents: dict[str, Any]) -> None:
                self._contents = contents

            def getByName(self, name: str) -> Any:  # noqa: N802
                if name not in self._contents:
                    raise KeyError(name)
                return self._contents[name]

            def hasByName(self, name: str) -> bool:  # noqa: N802
                return name in self._contents

            def getElementNames(self) -> tuple[str, ...]:  # noqa: N802
                return tuple(self._contents.keys())

        return _NameAccess(
            {
                name: _NameAccess(contents)
                for name, contents in self._style_families.items()
            }
        )

    # Additional UNO surface used by reading / search / structure tools.
    def getTextSections(self) -> _Sized:  # noqa: N802
        return _Sized(0)

    def getTextFields(self) -> FakeAnnotationsContainer:  # noqa: N802
        """``XTextFieldsSupplier.getTextFields()``.

        Production code's ``_iter_annotations`` iterates this and filters
        by ``supportsService("com.sun.star.text.TextField.Annotation")``,
        which is exactly what :class:`FakeAnnotation` claims via
        :class:`_Service`. So returning the document's annotation
        container directly gives the commenting tools a working surface.
        """
        return self._annotations

    def createInstance(self, service: str) -> Any:  # noqa: N802
        """Instantiate a stub for the document-scoped services tools use."""
        if service == "com.sun.star.text.TextTable":
            return FakeTextTable(
                rows=1,
                cols=1,
                name=f"Table{self._tables.getCount() + 1}",
            )
        if service == "com.sun.star.text.TextField.Annotation":
            return FakeAnnotation(name="__synthetic__", content="")
        if service == "com.sun.star.text.TextGraphicObject":
            return _PropBag(GraphicURL="", AnchorType=0)
        raise RuntimeError(
            f"FakeTextDocument.createInstance: no synthetic for {service!r}. "
            "Extend tests/synthetic/synthetic_uno.py."
        )

    def createSearchDescriptor(self) -> _PropBag:  # noqa: N802
        return _PropBag(
            SearchString="",
            SearchCaseSensitive=False,
            SearchRegularExpression=False,
            SearchWords=False,
        )

    def createReplaceDescriptor(self) -> _PropBag:  # noqa: N802
        return _PropBag(
            SearchString="",
            ReplaceString="",
            SearchCaseSensitive=False,
            SearchRegularExpression=False,
            SearchWords=False,
        )

    def findAll(self, descriptor: _PropBag) -> FakeSelectionCollection:  # noqa: N802
        """Linear scan of paragraphs for ``SearchString`` matches.

        Each match is returned as a :class:`_RangeCursor` that, when
        ``setString`` is called, substitutes back into the underlying
        paragraph at the original offset — letting find-and-replace
        tools mutate the document end-to-end.

        Regex / word-boundary flags are accepted but ignored — tests
        needing those semantics should mock at a higher level.
        """
        query = descriptor.getPropertyValue("SearchString") or ""
        case_sensitive = bool(descriptor.getPropertyValue("SearchCaseSensitive"))
        matches: list[_RangeCursor] = []
        if not query:
            return FakeSelectionCollection(matches)
        for para in self._text._paragraphs:
            source = para.getString()
            haystack = source if case_sensitive else source.lower()
            needle = query if case_sensitive else query.lower()
            idx = haystack.find(needle)
            while idx >= 0:
                matches.append(_RangeCursor(para, idx, idx + len(query)))
                idx = haystack.find(needle, idx + len(needle))
        return FakeSelectionCollection(matches)

    def findFirst(self, descriptor: _PropBag) -> Any:  # noqa: N802
        matches = self.findAll(descriptor)
        return matches.getByIndex(0) if matches.getCount() > 0 else None

    def replaceAll(self, descriptor: _PropBag) -> int:  # noqa: N802
        """Replace every occurrence of ``SearchString`` with ``ReplaceString``.

        Returns the total number of replacements made.
        """
        query = descriptor.getPropertyValue("SearchString") or ""
        replacement = descriptor.getPropertyValue("ReplaceString") or ""
        if not query:
            return 0
        case_sensitive = bool(descriptor.getPropertyValue("SearchCaseSensitive"))
        count = 0
        for para in self._text._paragraphs:
            text = para.getString()
            if case_sensitive:
                if query in text:
                    count += text.count(query)
                    para.setString(text.replace(query, replacement))
            else:
                lower = text.lower()
                needle = query.lower()
                if needle not in lower:
                    continue
                rebuilt: list[str] = []
                i = 0
                while i < len(text):
                    if lower[i : i + len(needle)] == needle:
                        rebuilt.append(replacement)
                        count += 1
                        i += len(needle)
                    else:
                        rebuilt.append(text[i])
                        i += 1
                para.setString("".join(rebuilt))
        return count

    # Convenience helpers (test-only) ------------------------------------

    @property
    def annotations(self) -> FakeAnnotationsContainer:
        return self._annotations

    @property
    def tables(self) -> FakeTablesContainer:
        return self._tables

    def add_annotation(self, **kwargs: Any) -> FakeAnnotation:
        ann = FakeAnnotation(**kwargs)
        self._annotations.add(ann)
        return ann


# ---------------------------------------------------------------------------
# Frame + desktop — for `get_writer_document(ctx)` to resolve back to ours
# ---------------------------------------------------------------------------


class FakeFrame:
    def __init__(self, doc: FakeTextDocument) -> None:
        self._doc = doc

    def getContainerWindow(self) -> Any:  # noqa: N802
        return None


class FakeDesktop:
    def __init__(self, doc: FakeTextDocument | None) -> None:
        self._doc = doc

    def getCurrentComponent(self) -> FakeTextDocument | None:  # noqa: N802
        return self._doc

    def getCurrentFrame(self) -> Any:  # noqa: N802
        return self._doc._controller._frame if self._doc is not None else None


# ---------------------------------------------------------------------------
# ServiceManager — minimal createInstance dispatch
# ---------------------------------------------------------------------------


class FakeServiceManager:
    def __init__(self, doc: FakeTextDocument | None) -> None:
        self._doc = doc
        self._factories: dict[str, Callable[[], Any]] = {}
        # Register the only service tools ask for: Desktop.
        self._factories["com.sun.star.frame.Desktop"] = lambda: FakeDesktop(self._doc)

    def createInstanceWithContext(  # noqa: N802
        self, name: str, _ctx: Any
    ) -> Any:
        factory = self._factories.get(name)
        if factory is None:
            raise RuntimeError(
                f"FakeServiceManager has no factory for service {name!r}. "
                "Register one if a tool depends on it."
            )
        return factory()


class FakeContext:
    def __init__(self, doc: FakeTextDocument | None) -> None:
        self.ServiceManager = FakeServiceManager(doc)
