"""Component-aware datasheet retrieval.

``RetrievalBackend`` is the extension point (an embeddings / Bedrock Knowledge Bases
backend can implement the same ``search`` signature without changing the API).
``KeywordRetrievalBackend`` is a dependency-free BM25 implementation over chunks
extracted from local Markdown/text files and PDFs (via PyMuPDF, with page numbers).

Every passage keeps its source file, page (PDFs), section heading and whether the
document is illustrative teaching data. Nothing here fabricates a source: a passage
can only be cited if it was actually returned by ``search``.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import pymupdf

logger = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = {".md", ".txt", ".pdf"}
MAX_CHUNK_CHARS = 900
MAX_DOC_BYTES = 20 * 1024 * 1024

_STOPWORDS = set(
    "a an and are as at be by for from has have how i in is it its of on or that the this to was what when "
    "where which who why will with my me do does can should next about into than then there these those your".split()
)
_TOKEN = re.compile(r"[a-z0-9]+(?:[.\-][a-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    tokens = []
    for tok in _TOKEN.findall(text.lower()):
        if tok in _STOPWORDS:
            continue
        tokens.append(tok)
        if "-" in tok or "." in tok:
            tokens.extend(t for t in re.split(r"[.\-]", tok) if t and t not in _STOPWORDS)
    return tokens


@dataclass
class DocumentMeta:
    source: str
    title: str
    component_types: list[str] = field(default_factory=list)
    part_numbers: list[str] = field(default_factory=list)
    illustrative: bool = True


@dataclass
class Chunk:
    id: str
    meta: DocumentMeta
    text: str
    section: str | None
    page: int | None
    tokens: Counter = field(default_factory=Counter)
    length: int = 0


@dataclass
class RetrievedPassage:
    id: str
    source: str
    title: str
    page: int | None
    section: str | None
    excerpt: str
    illustrative: bool
    score: float


class RetrievalBackend(Protocol):
    def search(
        self, query: str, *, component_type: str | None = None, part_number: str | None = None, k: int = 4
    ) -> list[RetrievedPassage]: ...

    @property
    def document_count(self) -> int: ...


# --------------------------------------------------------------------------- loading


def _parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    header = text[3:end].strip()
    body = text[end + 4 :].lstrip("\n")
    meta: dict[str, str] = {}
    for line in header.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip().lower()] = value.strip()
    return meta, body


def _split_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip().lower() for v in value.strip("[]").split(",") if v.strip()]


def _window(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out: list[str] = []
    buf = ""
    for p in paragraphs:
        if len(p) > limit:
            if buf:
                out.append(buf)
                buf = ""
            for i in range(0, len(p), limit):
                out.append(p[i : i + limit])
            continue
        if buf and len(buf) + len(p) + 2 > limit:
            out.append(buf)
            buf = p
        else:
            buf = f"{buf}\n\n{p}" if buf else p
    if buf:
        out.append(buf)
    return out


def load_markdown(path: Path, root: Path) -> list[Chunk]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    fm, body = _parse_front_matter(raw)
    source = path.relative_to(root).as_posix()
    meta = DocumentMeta(
        source=source,
        title=fm.get("title") or path.stem.replace("_", " "),
        component_types=_split_list(fm.get("component_types")),
        part_numbers=_split_list(fm.get("part_numbers")),
        illustrative=fm.get("illustrative", "true").lower() != "false",
    )
    chunks: list[Chunk] = []
    section: str | None = None
    current: list[str] = []

    def flush() -> None:
        text = "\n".join(current).strip()
        if text:
            for part in _window(text):
                chunks.append(_make_chunk(meta, part, section, None, len(chunks)))

    for line in body.splitlines():
        heading = re.match(r"^(#{1,4})\s+(.*)$", line)
        if heading:
            flush()
            current = []
            section = heading.group(2).strip()
        else:
            current.append(line)
    flush()
    return chunks


def load_pdf(path: Path, root: Path) -> list[Chunk]:
    if path.stat().st_size > MAX_DOC_BYTES:
        logger.warning("skipping %s: larger than %d bytes", path, MAX_DOC_BYTES)
        return []
    source = path.relative_to(root).as_posix()
    chunks: list[Chunk] = []
    with pymupdf.open(path) as doc:
        title = (doc.metadata or {}).get("title") or path.stem.replace("_", " ")
        lowered = path.stem.lower()
        meta = DocumentMeta(
            source=source,
            title=title,
            component_types=[t for t in ("diode", "led", "resistor", "opamp", "capacitor", "transistor") if t in lowered],
            illustrative="illustrative" in lowered or "illustrative" in title.lower(),
        )
        for page_index, page in enumerate(doc):
            text = page.get_text("text")
            if not text.strip():
                continue
            section = next((ln.strip() for ln in text.splitlines() if ln.strip()), None)
            for part in _window(text):
                chunks.append(_make_chunk(meta, part, section, page_index + 1, len(chunks)))
    return chunks


def _make_chunk(meta: DocumentMeta, text: str, section: str | None, page: int | None, n: int) -> Chunk:
    digest = hashlib.sha1(f"{meta.source}|{page}|{n}".encode()).hexdigest()[:10]
    tokens = Counter(tokenize(f"{meta.title} {section or ''} {text}"))
    return Chunk(
        id=f"ds-{digest}",
        meta=meta,
        text=text,
        section=section,
        page=page,
        tokens=tokens,
        length=sum(tokens.values()),
    )


# --------------------------------------------------------------------------- BM25


class KeywordRetrievalBackend:
    def __init__(self, directory: Path, k1: float = 1.5, b: float = 0.75):
        self.directory = directory
        self.k1, self.b = k1, b
        self.chunks: list[Chunk] = []
        self.sources: set[str] = set()
        if directory.exists():
            for path in sorted(directory.rglob("*")):
                if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
                    continue
                try:
                    loaded = load_pdf(path, directory) if path.suffix.lower() == ".pdf" else load_markdown(path, directory)
                except Exception as exc:  # a broken document must not take the service down
                    logger.warning("could not index %s: %s", path, exc)
                    continue
                if loaded:
                    self.sources.add(loaded[0].meta.source)
                    self.chunks.extend(loaded)
        self.df: Counter = Counter()
        for c in self.chunks:
            self.df.update(set(c.tokens))
        self.avg_len = (sum(c.length for c in self.chunks) / len(self.chunks)) if self.chunks else 1.0

    @property
    def document_count(self) -> int:
        return len(self.sources)

    def _idf(self, term: str) -> float:
        n = len(self.chunks)
        df = self.df.get(term, 0)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def search(
        self, query: str, *, component_type: str | None = None, part_number: str | None = None, k: int = 4
    ) -> list[RetrievedPassage]:
        k = max(1, min(k, 10))
        q_terms = tokenize(query)
        if component_type:
            q_terms += tokenize(component_type.replace("_", " "))
        if not q_terms or not self.chunks:
            return []
        ctype = (component_type or "").lower()
        pn = (part_number or "").lower()
        scored: list[tuple[float, Chunk]] = []
        for c in self.chunks:
            score = 0.0
            for term in set(q_terms):
                tf = c.tokens.get(term, 0)
                if not tf:
                    continue
                denom = tf + self.k1 * (1 - self.b + self.b * c.length / self.avg_len)
                score += self._idf(term) * tf * (self.k1 + 1) / denom
            if score <= 0:
                continue
            if ctype and ctype in c.meta.component_types:
                score *= 1.6
            elif ctype and c.meta.component_types and "general" not in c.meta.component_types:
                score *= 0.6
            if pn and pn in c.meta.part_numbers:
                score *= 1.5
            scored.append((score, c))
        scored.sort(key=lambda t: (-t[0], t[1].id))
        return [
            RetrievedPassage(
                id=c.id,
                source=c.meta.source,
                title=c.meta.title,
                page=c.page,
                section=c.section,
                excerpt=c.text.strip(),
                illustrative=c.meta.illustrative,
                score=round(s, 3),
            )
            for s, c in scored[:k]
        ]


def build_retrieval_backend(kind: str, directory: Path) -> RetrievalBackend:
    if kind == "keyword":
        return KeywordRetrievalBackend(directory)
    raise ValueError(f"unknown retrieval backend {kind!r}")
