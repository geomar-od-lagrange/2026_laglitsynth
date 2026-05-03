"""Lazy typed views over a GROBID TEI XML file on disk.

``ExtractedDocument`` stores only ``work_id`` + ``tei_path`` +
``content_sha256`` + ``extracted_at``. The TEI bytes on disk are the
canonical extraction artefact. ``TeiDocument`` parses that file on
first accessor call and exposes four typed accessor methods
(``sections()``, ``figures()``, ``citations()``, ``bibliography()``).
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

from lxml import etree
from pydantic import BaseModel, ConfigDict

TEI_NS = "{http://www.tei-c.org/ns/1.0}"

# Hardened parser: no external-entity resolution, no network access.
_TEI_PARSER = etree.XMLParser(resolve_entities=False, no_network=True)


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None
    title: str | None
    paragraphs: list[str]
    children: list["Section"]


Section.model_rebuild()


class Figure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None
    label: str | None
    caption: str | None


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: str | None
    text: str


class BibReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None
    authors: list[str]
    title: str | None
    year: str | None
    doi: str | None
    raw: str


def _element_text(el: etree._Element) -> str:
    # ``itertext`` collects descendant text only, excluding the element's tail.
    return "".join(el.itertext()).strip()


def _normalise_ws(text: str) -> str:
    return " ".join(text.split())


def _xmlid(el: etree._Element) -> str | None:
    value = el.get("{http://www.w3.org/XML/1998/namespace}id")
    return value if value else None


def _format_author(pers_name: etree._Element) -> str | None:
    """Format a ``<persName>`` element as ``"Surname, F."``.

    Forenames collapse to first-letter initials joined by spaces. Missing
    surname returns ``None`` — the caller drops such entries so callers
    downstream see only well-formed author strings.
    """
    surname_el = pers_name.find(f"{TEI_NS}surname")
    if surname_el is None:
        return None
    surname = _element_text(surname_el)
    if not surname:
        return None
    initials: list[str] = []
    for fn in pers_name.findall(f"{TEI_NS}forename"):
        fn_text = _element_text(fn)
        if fn_text:
            initials.append(f"{fn_text[0]}.")
    if initials:
        return f"{surname}, {' '.join(initials)}"
    return surname


def _build_section(div: etree._Element) -> Section:
    head = div.find(f"{TEI_NS}head")
    title: str | None = None
    if head is not None:
        head_text = _element_text(head)
        title = head_text if head_text else None

    paragraphs: list[str] = []
    for p in div.findall(f"{TEI_NS}p"):
        p_text = _element_text(p)
        if p_text:
            paragraphs.append(p_text)

    children = [_build_section(child) for child in div.findall(f"{TEI_NS}div")]

    return Section(
        id=_xmlid(div),
        title=title,
        paragraphs=paragraphs,
        children=children,
    )


class TeiDocument:
    """Lazy typed views over a TEI XML file on disk."""

    def __init__(self, path: Path) -> None:
        if not path.exists():
            raise FileNotFoundError(path)
        self._path = path
        self._bytes: bytes | None = None
        self._root: etree._Element | None = None

    @classmethod
    def from_bytes(cls, data: bytes) -> "TeiDocument":
        # Bypass __init__'s existence check; keep the same lazy-parse shape.
        inst = cls.__new__(cls)
        inst._path = Path()  # unused for in-memory documents
        inst._bytes = data
        inst._root = None
        return inst

    def _load_bytes(self) -> bytes:
        if self._bytes is None:
            self._bytes = self._path.read_bytes()
        return self._bytes

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self._load_bytes()).hexdigest()

    def _parse(self) -> etree._Element:
        if self._root is None:
            self._root = etree.fromstring(self._load_bytes(), parser=_TEI_PARSER)
        return self._root

    def _body(self) -> etree._Element | None:
        return self._parse().find(f".//{TEI_NS}body")

    def sections(self) -> list[Section]:
        body = self._body()
        if body is None:
            return []
        # Strip figures so their captions don't leak into section paragraphs.
        # Operate on a deep copy so figures() still works on the original tree.
        body = deepcopy(body)
        for fig in body.findall(f".//{TEI_NS}figure"):
            parent = fig.getparent()
            if parent is not None:
                parent.remove(fig)

        return [_build_section(div) for div in body.findall(f"{TEI_NS}div")]

    def figures(self) -> list[Figure]:
        body = self._body()
        if body is None:
            return []
        figures: list[Figure] = []
        for fig in body.findall(f".//{TEI_NS}figure"):
            label_el = fig.find(f"{TEI_NS}label")
            label = _element_text(label_el) if label_el is not None else None
            desc_el = fig.find(f"{TEI_NS}figDesc")
            caption = _element_text(desc_el) if desc_el is not None else None
            figures.append(
                Figure(
                    id=_xmlid(fig),
                    label=label if label else None,
                    caption=caption if caption else None,
                )
            )
        return figures

    def citations(self) -> list[Citation]:
        body = self._body()
        if body is None:
            return []
        citations: list[Citation] = []
        for ref in body.findall(f".//{TEI_NS}ref[@type='bibr']"):
            target = ref.get("target")
            target_id: str | None = None
            if target:
                target_id = target[1:] if target.startswith("#") else target
            citations.append(
                Citation(target_id=target_id, text=_element_text(ref))
            )
        return citations

    def bibliography(self) -> list[BibReference]:
        root = self._parse()
        refs: list[BibReference] = []
        for bibl_list in root.findall(f".//{TEI_NS}listBibl"):
            for bibl in bibl_list.findall(f"{TEI_NS}biblStruct"):
                refs.append(_build_bib_reference(bibl))
        return refs


def flatten_sections(tei: TeiDocument) -> list[str]:
    """Depth-first flatten of all top-level sections into title+paragraph blocks.

    Each section contributes one block: the title (if any) on its first
    line, followed by its paragraphs (one per line). Nested children
    contribute their own blocks in document order. Returns a flat list of
    block strings, one per section node encountered depth-first.
    """

    def _flatten_one(section: Section) -> list[str]:
        blocks: list[str] = []
        lines: list[str] = []
        if section.title:
            lines.append(section.title)
        lines.extend(section.paragraphs)
        if lines:
            blocks.append("\n".join(lines))
        for child in section.children:
            blocks.extend(_flatten_one(child))
        return blocks

    result: list[str] = []
    for top in tei.sections():
        result.extend(_flatten_one(top))
    return result


def _build_bib_reference(bibl: etree._Element) -> BibReference:
    authors: list[str] = []
    for author in bibl.findall(f".//{TEI_NS}author"):
        pers = author.find(f"{TEI_NS}persName")
        if pers is None:
            continue
        formatted = _format_author(pers)
        if formatted is not None:
            authors.append(formatted)

    # Prefer the analytic (article) title; fall back to monograph title.
    title: str | None = None
    analytic_title = bibl.find(f"{TEI_NS}analytic/{TEI_NS}title")
    if analytic_title is not None:
        text = _element_text(analytic_title)
        if text:
            title = text
    if title is None:
        mono_title = bibl.find(f"{TEI_NS}monogr/{TEI_NS}title[@type='main']")
        if mono_title is not None:
            text = _element_text(mono_title)
            if text:
                title = text

    year: str | None = None
    date_el = bibl.find(f".//{TEI_NS}date")
    if date_el is not None:
        when = date_el.get("when")
        if when:
            year = when.split("-")[0]
        else:
            date_text = _element_text(date_el)
            if date_text:
                year = date_text

    doi: str | None = None
    for idno in bibl.findall(f".//{TEI_NS}idno"):
        if idno.get("type") == "DOI":
            doi_text = _element_text(idno)
            if doi_text:
                doi = doi_text
                break

    return BibReference(
        id=_xmlid(bibl),
        authors=authors,
        title=title,
        year=year,
        doi=doi,
        raw=_normalise_ws(_element_text(bibl)),
    )
