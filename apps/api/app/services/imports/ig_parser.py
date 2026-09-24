"""Instagram data-export parser.

Meta's DYI (Download Your Information) tool ships a ZIP containing:
    saved/saved_posts.html          — reels/posts the user bookmarked
    saved/saved_collections.html    — user-organized folders of saves
    saved/saved_music.html          — audio-only saves

We parse HTML rather than JSON because the JSON format is inconsistent
across Meta rollouts — HTML has stable visible field labels ("URL",
"Caption", "Hashtags", "Owner") that survive the CSS-class churn Meta
performs monthly (TRD §8.2).

The parser is schema-tolerant: unknown labels are dropped into an
`_unknown` bucket and counted; anything > 5% of entries with unknowns
should fire the parser-schema-hash alert (TRD §8.8).
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator

from bs4 import BeautifulSoup, Tag

from app.observability.logging import get_logger

_logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Public parsed shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Owner:
    url: str | None = None
    name: str | None = None
    username: str | None = None


@dataclass(frozen=True)
class SavedPost:
    """One entry from saved_posts.html or nested inside a collection."""
    url: str
    saved_at: datetime | None
    caption: str | None = None
    hashtags: tuple[str, ...] = ()
    owner: Owner | None = None
    unknown_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class SavedCollection:
    """One entry from saved_collections.html — a folder + members."""
    name: str
    type: str | None
    privacy: str | None
    update_time: datetime | None
    media: tuple[SavedPost, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SavedAudio:
    """One entry from saved_music.html."""
    title: str | None
    artist: str | None
    saved_at: datetime | None


@dataclass(frozen=True)
class ParsedExport:
    """Aggregate over all three files. Post-parse the pipeline hands
    this off to ig_pipeline.process_import() (Phase 2 next commit)."""
    posts: tuple[SavedPost, ...]
    collections: tuple[SavedCollection, ...]
    audio: tuple[SavedAudio, ...]
    parser_schema_hash: str
    # Aggregate telemetry — helps the schema-watch alert (TRD §8.8)
    # spot when Meta ships a rename we haven't caught up to yet.
    unknown_label_count: int
    entries_with_unknowns: int


# ---------------------------------------------------------------------------
# Zip entrypoint
# ---------------------------------------------------------------------------


class ParserError(RuntimeError):
    """Malformed ZIP or missing expected files."""


def parse_zip(zip_path: Path) -> ParsedExport:
    """Read the export ZIP → ParsedExport.

    Handles the three canonical filenames but is tolerant of Meta
    reshuffling the folder layout (some exports drop the `saved/`
    directory in favor of a flat root).
    """
    if not zip_path.exists():
        raise ParserError(f"zip not found: {zip_path}")

    posts: list[SavedPost] = []
    collections: list[SavedCollection] = []
    audio: list[SavedAudio] = []

    labels_seen: set[str] = set()
    entries_with_unknowns = 0
    unknown_label_count = 0

    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            base = Path(member).name.lower()
            if base == "saved_posts.html":
                with zf.open(member) as fp:
                    html = fp.read()
                for post in _iter_saved_posts(html, labels_seen):
                    posts.append(post)
                    if post.unknown_fields:
                        entries_with_unknowns += 1
                        unknown_label_count += len(post.unknown_fields)
            elif base == "saved_collections.html":
                with zf.open(member) as fp:
                    html = fp.read()
                for coll in _iter_saved_collections(html, labels_seen):
                    collections.append(coll)
            elif base == "saved_music.html":
                with zf.open(member) as fp:
                    html = fp.read()
                for a in _iter_saved_music(html):
                    audio.append(a)

    schema_hash = hashlib.sha256(
        ",".join(sorted(labels_seen)).encode("utf-8")
    ).hexdigest()[:16]

    _logger.info(
        "ig_parser.done",
        posts=len(posts),
        collections=len(collections),
        audio=len(audio),
        schema_hash=schema_hash,
        unknown_labels=unknown_label_count,
    )
    return ParsedExport(
        posts=tuple(posts),
        collections=tuple(collections),
        audio=tuple(audio),
        parser_schema_hash=schema_hash,
        unknown_label_count=unknown_label_count,
        entries_with_unknowns=entries_with_unknowns,
    )


# ---------------------------------------------------------------------------
# Per-file iterators
# ---------------------------------------------------------------------------

# Every entry in the HTML dump is a `div` with these three CSS-obfuscated
# classes together. Selecting on the concrete class set (not just a
# substring) avoids matching nested owner sub-tables that reuse the same
# style tokens.
_ENTRY_SELECTOR = "div.pam.uiBoxWhite.noborder"

# Timestamp footer sits on a sibling with these classes.
_TIMESTAMP_CLASS = "_a6-o"


def _iter_saved_posts(html: bytes, labels_seen: set[str]) -> Iterator[SavedPost]:
    """Yield SavedPost per top-level entry in saved_posts.html."""
    soup = BeautifulSoup(html, "lxml")
    for entry in _iter_top_level_entries(soup, _ENTRY_SELECTOR):
        table = _first_child_table(entry)
        if table is None:
            continue
        parsed = _parse_row_labels(table, labels_seen)
        url = parsed.get("__url__")
        if not url:
            continue
        yield SavedPost(
            url=url,
            saved_at=_read_timestamp_sibling(entry),
            caption=parsed.get("caption"),
            hashtags=tuple(parsed.get("hashtags") or ()),
            owner=parsed.get("owner"),
            unknown_fields=tuple(parsed.get("__unknown__") or ()),
        )


def _iter_saved_collections(
    html: bytes, labels_seen: set[str]
) -> Iterator[SavedCollection]:
    """Yield SavedCollection per top-level entry in saved_collections.html."""
    soup = BeautifulSoup(html, "lxml")
    for entry in _iter_top_level_entries(soup, _ENTRY_SELECTOR):
        table = _first_child_table(entry)
        if table is None:
            continue

        name: str | None = None
        type_val: str | None = None
        privacy: str | None = None
        update_time: datetime | None = None
        media: list[SavedPost] = []

        for label, value_cell in _iter_label_value_cells(table):
            labels_seen.add(label)
            lower = label.lower()
            if lower == "name":
                name = value_cell.get_text(strip=True) or None
            elif lower == "type":
                type_val = value_cell.get_text(strip=True) or None
            elif lower == "privacy":
                privacy = value_cell.get_text(strip=True) or None
            elif lower.startswith("update"):
                update_time = _parse_meta_ts(value_cell.get_text(strip=True))
            elif lower == "media":
                # Nested media entries — each one is a saved_posts-shaped
                # sub-block. Recurse into the innermost .pam wrappers
                # (leaves) — each is a full media entry.
                for nested in _iter_nested_media_blocks(value_cell):
                    inner_table = nested.find("table")
                    if inner_table is None:
                        continue
                    parsed = _parse_row_labels(inner_table, labels_seen)
                    url = parsed.get("__url__")
                    if not url:
                        continue
                    media.append(
                        SavedPost(
                            url=url,
                            saved_at=None,
                            caption=parsed.get("caption"),
                            hashtags=tuple(parsed.get("hashtags") or ()),
                            owner=parsed.get("owner"),
                            unknown_fields=tuple(parsed.get("__unknown__") or ()),
                        )
                    )

        if name is None:
            continue
        yield SavedCollection(
            name=name,
            type=type_val,
            privacy=privacy,
            update_time=update_time,
            media=tuple(media),
        )


def _iter_saved_music(html: bytes) -> Iterator[SavedAudio]:
    """Yield SavedAudio per entry in saved_music.html.

    Music entries can contain multiple (Title, Artist) rows when the
    creator sampled several tracks in one reel. We yield the first
    (Title, Artist) pair per top-level entry — matching the reference
    HTML's UI, which treats each block as one saved audio moment.
    """
    soup = BeautifulSoup(html, "lxml")
    for entry in _iter_top_level_entries(soup, _ENTRY_SELECTOR):
        table = _first_child_table(entry)
        if table is None:
            continue
        title: str | None = None
        artist: str | None = None
        for label, value_cell in _iter_label_value_cells(table):
            text = value_cell.get_text(strip=True) or None
            if label.lower() == "title" and title is None:
                title = text
            elif label.lower() == "artist" and artist is None:
                artist = text
            if title is not None and artist is not None:
                break
        saved_at = _read_timestamp_sibling(entry)
        if title is None and artist is None:
            continue
        yield SavedAudio(title=title, artist=artist, saved_at=saved_at)


# ---------------------------------------------------------------------------
# Row-level parsers — shared between saved_posts and nested collection media
# ---------------------------------------------------------------------------


def _parse_row_labels(table: Tag, labels_seen: set[str]) -> dict[str, object]:
    """Walk the entry's outer <table>, recognize labels, build a dict.

    Returns keys: __url__, caption, hashtags, owner, __unknown__.
    """
    out: dict[str, object] = {}
    unknown: list[str] = []

    for label, value_cell in _iter_label_value_cells(table):
        labels_seen.add(label)
        lower = label.lower()

        if lower.startswith("url"):
            # URL cell has an <a href> inside a nested <div>.
            anchor = value_cell.find("a")
            if anchor and anchor.has_attr("href"):
                out["__url__"] = anchor["href"]
        elif lower == "caption":
            # Preserve line breaks — the caption may contain a multiline
            # recipe or thread.
            out["caption"] = value_cell.get_text("\n", strip=False).strip() or None
        elif lower.startswith("hashtag"):
            out["hashtags"] = _extract_hashtag_list(value_cell)
        elif lower.startswith("owner"):
            out["owner"] = _extract_owner(value_cell)
        else:
            unknown.append(label)

    if unknown:
        out["__unknown__"] = unknown
    return out


def _extract_hashtag_list(cell: Tag) -> list[str]:
    """Owner-formatted hashtag section: a nested table with one row per
    tag whose text is the tag itself. Strip empties + leading `#`."""
    tags: list[str] = []
    for leaf in cell.find_all("div", class_="_a6-p"):
        text = leaf.get_text(strip=True)
        if not text or text.lower() == "name":  # skip the "Name" header row
            continue
        tags.append(text.lstrip("#"))
    return tags


def _extract_owner(cell: Tag) -> Owner:
    """Owner subsection: buried inside 2-4 nested wrappers, so we skip
    the traversal dance and just scan every <tr> for URL/Name/Username
    label rows. Meta consistently uses 2-column rows for these — first
    <td> is the label, second is the value."""
    fields: dict[str, str] = {}
    for tr in cell.find_all("tr"):
        cells = tr.find_all("td", recursive=False)
        if len(cells) != 2:
            continue
        label = cells[0].get_text(strip=True).lower()
        if label in ("url", "name", "username"):
            value = cells[1].get_text(strip=True) or ""
            if value:
                fields[label] = value
    return Owner(
        url=fields.get("url") or None,
        name=fields.get("name") or None,
        username=fields.get("username") or None,
    )


# ---------------------------------------------------------------------------
# Traversal + parse primitives
# ---------------------------------------------------------------------------


def _iter_top_level_entries(soup: BeautifulSoup, selector: str) -> Iterator[Tag]:
    """Yield only the outermost .pam.uiBoxWhite.noborder — Meta nests
    these boxes for every subsection; we want the entry-level ones."""
    for div in soup.select(selector):
        # An entry-level box is one whose closest ancestor of the same
        # selector is either <main> or the document itself.
        parent = div.find_parent("div", class_=re.compile(r"pam\b"))
        if parent is None or parent.name == "main":
            yield div


def _first_child_table(entry: Tag) -> Tag | None:
    return entry.find("table")


def _iter_label_value_cells(table: Tag) -> Iterator[tuple[str, Tag]]:
    """Every <tr> in the entry's outer table has exactly two <td>s:
    a label cell (like `<td>Caption</td>`) and a value cell. We yield
    (label_text, value_td) for each — the value_td is the caller's to
    interpret."""
    # tbody may or may not be present depending on parser rules — walk
    # rows regardless of depth but stop at the first table's own rows
    # (i.e. exclude descendants that are inside a NESTED table).
    for row in _iter_direct_rows(table):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 2:
            # Some rows are a single wide cell containing a nested
            # subsection (Hashtags, Owner, Media). Their label lives on
            # an <h2> child heading — check for that first, then fall
            # back to the first line of separated text.
            if len(cells) == 1:
                heading = cells[0].find("h2")
                if heading is not None:
                    label = heading.get_text(strip=True)
                else:
                    text = cells[0].get_text(separator="\n", strip=True)
                    label, _, _ = text.partition("\n")
                yield label, cells[0]
            continue
        label = cells[0].get_text(strip=True)
        yield label, cells[1]


def _iter_direct_rows(table: Tag) -> Iterator[Tag]:
    """Rows belonging to `table` — either direct children (no tbody) or
    children of the first-level tbody, but NOT rows nested in a table
    inside a cell of this one."""
    # Fast path: direct <tr>.
    direct = table.find_all("tr", recursive=False)
    if direct:
        yield from direct
        return
    # Common case: implicit <tbody>.
    for tbody in table.find_all("tbody", recursive=False):
        yield from tbody.find_all("tr", recursive=False)


def _iter_nested_media_blocks(cell: Tag) -> Iterator[Tag]:
    """Iterate entry-level wrappers inside a collection's Media cell.

    Structure:
      cell
        → <div>
          → <div class="pam uiBoxWhite noborder">      (outer Media section wrapper)
            → <h2>Media</h2>
            → <div class="_a6-p">                      (inner container)
              → <div class="pam uiBoxWhite noborder"> × N  ← each is a media entry
                → <div class="_a6-p">
                  → <table>                            ← the entry's table

    We walk the direct-child chain instead of matching by inner content
    (previous approach caught Owner sub-blocks which also carry a URL
    row). Only direct children of the inner container qualify as
    entry wrappers.
    """
    outer_media = cell.select_one("div.pam.uiBoxWhite.noborder")
    if outer_media is None:
        return
    inner = outer_media.find("div", class_="_a6-p", recursive=True)
    if inner is None:
        return
    for entry in inner.find_all(
        "div", class_="pam", recursive=False
    ):
        if _has_url_row(entry):
            yield entry


def _has_url_row(node: Tag) -> bool:
    """True if `node` contains a `<tr>` whose first <td> starts with 'URL'."""
    for tr in node.find_all("tr"):
        cells = tr.find_all("td", recursive=False)
        if not cells:
            continue
        head = cells[0].get_text(strip=True).lower()
        if head.startswith("url"):
            return True
    return False


def _read_timestamp_sibling(entry: Tag) -> datetime | None:
    """The entry's saved-timestamp sits on a sibling `<div class='_a6-o'>`
    with a human-readable date string."""
    sibling = entry.find_next_sibling("div", class_=_TIMESTAMP_CLASS)
    if sibling is None:
        # Fallback: the entry's own outer container may have it as a
        # descendant when Meta ships a different layout.
        sibling = entry.find("div", class_=_TIMESTAMP_CLASS)
    if sibling is None:
        return None
    return _parse_meta_ts(sibling.get_text(strip=True))


# Meta's export dates look like: `Aug 02, 2026 3:03 am`. No timezone —
# we treat them as UTC to keep the parser deterministic; the actual
# save time in the user's local zone is off by at most one day and we
# only ever use it for chronological sort + F8.4 age gates.
_TS_FORMATS = (
    "%b %d, %Y %I:%M %p",
    "%b %d, %Y %I:%M%p",
)


def _parse_meta_ts(raw: str) -> datetime | None:
    if not raw:
        return None
    normalized = raw.strip().replace("  ", " ")
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    return None
