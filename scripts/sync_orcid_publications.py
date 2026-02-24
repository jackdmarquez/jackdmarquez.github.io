#!/usr/bin/env python3
"""Sync new publications from ORCID into an AcademicPages `_publications` folder.

Add-only sync:
- Reads ORCID works summaries
- Enriches with Crossref when DOI is available
- Detects duplicates in existing markdown files
- Generates new publication markdown files
"""

from __future__ import annotations

import argparse
import dataclasses
import html
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


ORCID_BASE = "https://pub.orcid.org/v3.0"
CROSSREF_BASE = "https://api.crossref.org/works"
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
YAML_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
STRIP_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
SPACE_RE = re.compile(r"\s+")
HTML_TAG_RE = re.compile(r"<[^>]+>")
BIB_START_RE = re.compile(r"(?mi)^@\w+\s*\{")
BIB_KEY_RE = re.compile(r"(?mi)^@\w+\s*\{\s*([^,\s]+)\s*,")
BIB_DOI_FIELD_RE = re.compile(r"(?mi)^\s*doi\s*=\s*[\{\"](.+?)[\}\"]\s*,?\s*$")
BIB_TITLE_FIELD_RE = re.compile(r"(?mi)^\s*title\s*=\s*[\{\"](.+?)[\}\"]\s*,?\s*$")
BIB_YEAR_FIELD_RE = re.compile(r"(?mi)^\s*year\s*=\s*[\{\"]?(\d{4})[\}\"]?\s*,?\s*$")
BIB_AUTHOR_FIELD_LINE_RE = re.compile(r"(?mi)^(\s*author\s*=\s*)([\{\"])")
NONWORD_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class PublicationRecord:
    orcid_put_code: str
    orcid_path: Optional[str]
    title: str
    doi: Optional[str]
    year: Optional[int]
    month: Optional[int]
    day: Optional[int]
    venue: Optional[str]
    authors: list[str]
    url: Optional[str]
    abstract: Optional[str]
    citation: Optional[str]
    source_quality: str  # "crossref" or "orcid"


@dataclass
class ExistingIndex:
    orcid_put_codes: set[str]
    dois: set[str]
    title_years: set[tuple[str, Optional[int]]]


@dataclass
class SyncResult:
    created_files: list[Path]
    created_records: list[PublicationRecord]
    skipped_existing: int
    warnings: list[str]
    cv_bib_entries_added: int = 0
    cv_bib_entries_skipped: int = 0
    cv_warnings: list[str] = dataclasses.field(default_factory=list)
    cv_added_titles: list[str] = dataclasses.field(default_factory=list)


@dataclass
class BibIndex:
    keys: set[str]
    dois: set[str]
    title_years: set[tuple[str, Optional[int]]]


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orcid-id", required=True, help="ORCID iD, e.g. 0000-0002-2673-3507")
    parser.add_argument(
        "--publications-dir",
        default="_publications",
        help="Path to AcademicPages publications directory (default: _publications)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not write files")
    parser.add_argument("--max-new", type=int, default=None, help="Optional cap on new files to create")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    parser.add_argument(
        "--pr-body-path",
        default="orcid-sync-pr-body.md",
        help="Path to write PR summary markdown (default: orcid-sync-pr-body.md)",
    )
    parser.add_argument(
        "--cv-bib-path",
        default=None,
        help="Optional path to CV repo publications.bib to append new entries",
    )
    parser.add_argument(
        "--cv-pr-body-path",
        default=None,
        help="Optional path to write CV repo PR summary markdown",
    )
    parser.add_argument(
        "--cv-dry-run",
        action="store_true",
        help="Do not write CV publications.bib (still compute/report additions)",
    )
    return parser.parse_args(argv)


def log(msg: str, verbose: bool = True) -> None:
    if verbose:
        print(msg)


def http_get_json(url: str, headers: Optional[dict[str, str]] = None) -> dict[str, Any]:
    req = Request(url, headers=headers or {})
    with urlopen(req, timeout=30) as resp:  # nosec - controlled URLs
        charset = resp.headers.get_content_charset() or "utf-8"
        body = resp.read().decode(charset)
    return json.loads(body)


def http_get_text(url: str, headers: Optional[dict[str, str]] = None) -> str:
    req = Request(url, headers=headers or {})
    with urlopen(req, timeout=30) as resp:  # nosec - controlled URLs
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset)


def fetch_orcid_works(orcid_id: str) -> dict[str, Any]:
    url = f"{ORCID_BASE}/{orcid_id}/works"
    headers = {"Accept": "application/json", "User-Agent": "orcid-publication-sync-bot/1.0"}
    return http_get_json(url, headers=headers)


def fetch_crossref_by_doi(doi: str) -> Optional[dict[str, Any]]:
    email = os.environ.get("CROSSREF_MAILTO")
    ua = "orcid-publication-sync-bot/1.0"
    if email:
        ua = f"{ua} (mailto:{email})"
    headers = {"Accept": "application/json", "User-Agent": ua}
    url = f"{CROSSREF_BASE}/{quote(doi, safe='')}"
    try:
        return http_get_json(url, headers=headers)
    except HTTPError as exc:
        if exc.code in (404, 429):
            return None
        raise
    except URLError:
        return None


def fetch_doi_bibtex(doi: str) -> Optional[str]:
    headers = {
        "Accept": "application/x-bibtex; charset=utf-8",
        "User-Agent": "orcid-publication-sync-bot/1.0",
    }
    url = f"https://doi.org/{quote(doi, safe='')}"
    try:
        text = http_get_text(url, headers=headers)
        return text.strip() or None
    except HTTPError as exc:
        if exc.code in (404, 406, 429):
            return None
        raise
    except URLError:
        return None


def normalize_doi(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    s = html.unescape(value).strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^doi:\s*", "", s, flags=re.IGNORECASE)
    m = DOI_RE.search(s)
    if not m:
        return None
    return m.group(0).rstrip(" .;,").lower()


def extract_dois(text: str) -> set[str]:
    return {m.group(0).rstrip(" .;,").lower() for m in DOI_RE.finditer(text or "")}


def normalize_text_key(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.casefold()
    text = STRIP_PUNCT_RE.sub(" ", text)
    text = SPACE_RE.sub(" ", text).strip()
    return text


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "publication"


def clean_html_text(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = html.unescape(value)
    value = HTML_TAG_RE.sub(" ", value)
    value = SPACE_RE.sub(" ", value).strip()
    return value or None


def _nested_get(data: dict[str, Any], *keys: str) -> Any:
    cur: Any = data
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _orcid_title(summary: dict[str, Any]) -> str:
    title = _nested_get(summary, "title", "title", "value")
    if not title:
        raise ValueError(f"ORCID work-summary missing title for put-code={summary.get('put-code')}")
    return str(title).strip()


def _orcid_date(summary: dict[str, Any]) -> tuple[Optional[int], Optional[int], Optional[int]]:
    pd = summary.get("publication-date") or {}
    def _to_int(k: str) -> Optional[int]:
        v = _nested_get(pd, k, "value")
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None
    return _to_int("year"), _to_int("month"), _to_int("day")


def _orcid_external_url(summary: dict[str, Any]) -> Optional[str]:
    url = _nested_get(summary, "url", "value")
    return str(url).strip() if url else None


def _orcid_venue(summary: dict[str, Any]) -> Optional[str]:
    venue = _nested_get(summary, "journal-title", "value")
    return str(venue).strip() if venue else None


def _orcid_doi(summary: dict[str, Any]) -> Optional[str]:
    ids = _nested_get(summary, "external-ids", "external-id")
    if not isinstance(ids, list):
        return None
    for item in ids:
        if not isinstance(item, dict):
            continue
        id_type = str(item.get("external-id-type") or "").casefold()
        if id_type == "doi":
            val = item.get("external-id-value")
            doi = normalize_doi(str(val) if val is not None else None)
            if doi:
                return doi
    # fallback: scan all external id values
    for item in ids:
        if isinstance(item, dict):
            doi = normalize_doi(str(item.get("external-id-value") or ""))
            if doi:
                return doi
    return None


def parse_orcid_works(payload: dict[str, Any]) -> list[PublicationRecord]:
    records: list[PublicationRecord] = []
    for group in payload.get("group", []) or []:
        summaries = group.get("work-summary") or []
        if not summaries:
            continue
        # Prefer newest summary if display-index is present, else first.
        def sort_key(s: dict[str, Any]) -> tuple[int, int]:
            di = s.get("display-index")
            try:
                display_idx = int(di)
            except (TypeError, ValueError):
                display_idx = -1
            put_code = s.get("put-code")
            try:
                put = int(put_code)
            except (TypeError, ValueError):
                put = -1
            return (display_idx, put)

        summary = sorted(summaries, key=sort_key, reverse=True)[0]
        title = _orcid_title(summary)
        year, month, day = _orcid_date(summary)
        rec = PublicationRecord(
            orcid_put_code=str(summary.get("put-code") or ""),
            orcid_path=str(summary.get("path") or "") or None,
            title=title,
            doi=_orcid_doi(summary),
            year=year,
            month=month,
            day=day,
            venue=_orcid_venue(summary),
            authors=[],
            url=_orcid_external_url(summary),
            abstract=None,
            citation=None,
            source_quality="orcid",
        )
        if rec.orcid_put_code:
            records.append(rec)
    return records


def parse_crossref_message(payload: dict[str, Any], base: PublicationRecord) -> PublicationRecord:
    message = payload.get("message") or {}

    title_list = message.get("title") or []
    title = (title_list[0] if title_list else None) or base.title

    authors: list[str] = []
    for author in message.get("author", []) or []:
        if not isinstance(author, dict):
            continue
        given = str(author.get("given") or "").strip()
        family = str(author.get("family") or "").strip()
        name = str(author.get("name") or "").strip()
        if given or family:
            authors.append(" ".join(x for x in [given, family] if x))
        elif name:
            authors.append(name)

    venue_candidates = [
        (message.get("container-title") or [None])[0],
        (message.get("short-container-title") or [None])[0],
        base.venue,
    ]
    venue = next((str(v).strip() for v in venue_candidates if v), None)

    year, month, day = base.year, base.month, base.day
    for key in ("published-print", "published-online", "issued", "created"):
        parts = _nested_get(message, key, "date-parts")
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            vals = parts[0]
            year = int(vals[0]) if len(vals) > 0 and vals[0] else year
            month = int(vals[1]) if len(vals) > 1 and vals[1] else month
            day = int(vals[2]) if len(vals) > 2 and vals[2] else day
            break

    doi = normalize_doi(message.get("DOI")) or base.doi
    url = str(message.get("URL") or "").strip() or base.url
    abstract = clean_html_text(message.get("abstract")) or base.abstract
    citation = build_citation(
        authors=authors,
        year=year,
        title=title,
        venue=venue,
        doi=doi,
    )
    return dataclasses.replace(
        base,
        title=title,
        doi=doi,
        year=year,
        month=month,
        day=day,
        venue=venue,
        authors=authors,
        url=url,
        abstract=abstract,
        citation=citation,
        source_quality="crossref",
    )


def build_citation(
    *, authors: list[str], year: Optional[int], title: str, venue: Optional[str], doi: Optional[str]
) -> str:
    author_text = ", ".join(authors[:10]) if authors else ""
    if len(authors) > 10:
        author_text += ", et al."
    parts = []
    if author_text:
        parts.append(f"{author_text}.")
    if year:
        parts.append(f"({year}).")
    parts.append(f"{title}.")
    if venue:
        parts.append(f"{venue}.")
    if doi:
        parts.append(f"doi:{doi}")
    return " ".join(parts).strip()


def _author_lastname_for_key(rec: PublicationRecord) -> str:
    if rec.authors:
        first = rec.authors[0].strip()
        if "," in first:
            last = first.split(",", 1)[0].strip()
        else:
            bits = first.split()
            last = bits[-1] if bits else "pub"
        last = NONWORD_RE.sub("", unicodedata.normalize("NFKD", last).encode("ascii", "ignore").decode("ascii").lower())
        if last:
            return last
    title_bits = [w for w in NONWORD_RE.sub(" ", normalize_text_key(rec.title)).split() if w and w not in {"the", "a", "an"}]
    return title_bits[0] if title_bits else "pub"


def _title_word_for_key(rec: PublicationRecord) -> str:
    words = [w for w in NONWORD_RE.sub(" ", normalize_text_key(rec.title)).split() if w not in {"the", "a", "an", "of", "and"}]
    return (words[0] if words else "work")[:24]


def generate_bibtex_key(rec: PublicationRecord, existing_keys: set[str]) -> str:
    year_part = str(rec.year or 1900)
    base = f"{_author_lastname_for_key(rec)}{year_part}{_title_word_for_key(rec)}"
    base = NONWORD_RE.sub("", base) or f"pub{year_part}"
    key = base
    idx = 2
    while key in existing_keys:
        key = f"{base}-{idx}"
        idx += 1
    return key


def _split_bibtex_entries(text: str) -> list[str]:
    starts = [m.start() for m in BIB_START_RE.finditer(text)]
    entries: list[str] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        chunk = text[start:end].strip()
        if chunk:
            entries.append(chunk)
    return entries


def _extract_bib_field(entry: str, field_name: str) -> Optional[str]:
    pattern = re.compile(rf"(?mi)^\s*{re.escape(field_name)}\s*=\s*")
    m = pattern.search(entry)
    if not m:
        return None
    i = m.end()
    while i < len(entry) and entry[i].isspace():
        i += 1
    if i >= len(entry):
        return None
    opener = entry[i]
    if opener not in ('{', '"'):
        j = i
        while j < len(entry) and entry[j] not in ",\n":
            j += 1
        return entry[i:j].strip()
    closer = '}' if opener == '{' else '"'
    i += 1
    depth = 1 if opener == '{' else 0
    out: list[str] = []
    while i < len(entry):
        ch = entry[i]
        if opener == '{':
            if ch == '{':
                depth += 1
                out.append(ch)
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    break
                out.append(ch)
            else:
                out.append(ch)
        else:
            if ch == '"' and (i == 0 or entry[i - 1] != "\\"):
                break
            out.append(ch)
        i += 1
    return "".join(out).strip() or None


def parse_bibtex_index(text: str) -> BibIndex:
    keys: set[str] = set()
    dois: set[str] = set()
    title_years: set[tuple[str, Optional[int]]] = set()
    for entry in _split_bibtex_entries(text):
        m = BIB_KEY_RE.search(entry)
        if m:
            keys.add(m.group(1).strip())
        doi = normalize_doi(_extract_bib_field(entry, "doi"))
        if doi:
            dois.add(doi)
        title = _extract_bib_field(entry, "title")
        year_s = _extract_bib_field(entry, "year")
        year = None
        if year_s:
            ym = re.search(r"\b(19|20)\d{2}\b", year_s)
            if ym:
                year = int(ym.group(0))
        if title:
            title_years.add((normalize_text_key(title), year))
    return BibIndex(keys=keys, dois=dois, title_years=title_years)


def is_bib_duplicate(rec: PublicationRecord, idx: BibIndex) -> bool:
    if rec.doi and rec.doi in idx.dois:
        return True
    title_key = normalize_text_key(rec.title)
    if title_key and (title_key, rec.year) in idx.title_years:
        return True
    return False


def _replace_author_variants(author_value: str) -> str:
    replacements = [
        (r"(?<!\\textbf\{)\bJ\.\s*Marquez\b", r"\\textbf{J. Marquez}"),
        (r"(?<!\\textbf\{)\bJ\s+Marquez\b", r"\\textbf{J. Marquez}"),
        (r"(?<!\\textbf\{)\bMarquez,\s*J\.\b", r"\\textbf{Marquez, J.}"),
        (r"(?<!\\textbf\{)\bMarquez,\s*J\b", r"\\textbf{Marquez, J.}"),
    ]
    out = author_value
    for pat, repl in replacements:
        out = re.sub(pat, repl, out)
    return out


def bold_author_name_in_bibtex(bibtex_text: str) -> str:
    m = BIB_AUTHOR_FIELD_LINE_RE.search(bibtex_text)
    if not m:
        return bibtex_text
    start = m.end(2)  # after opening brace/quote
    opener = m.group(2)
    closer = '}' if opener == '{' else '"'
    i = start
    depth = 1 if opener == '{' else 0
    while i < len(bibtex_text):
        ch = bibtex_text[i]
        if opener == '{':
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    break
        else:
            if ch == '"' and bibtex_text[i - 1] != "\\":
                break
        i += 1
    if i >= len(bibtex_text):
        return bibtex_text
    author_val = bibtex_text[start:i]
    patched = _replace_author_variants(author_val)
    return bibtex_text[:start] + patched + bibtex_text[i:]


def _guess_bib_entry_type(rec: PublicationRecord) -> str:
    venue = (rec.venue or "").casefold()
    if any(x in venue for x in ["conference", "symposium", "workshop", "proceedings"]):
        return "inproceedings"
    if venue:
        return "article"
    return "misc"


def _bibtex_field_lines_from_record(rec: PublicationRecord) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    if rec.title:
        fields.append(("title", rec.title))
    if rec.authors:
        fields.append(("author", " and ".join(rec.authors)))
    entry_type = _guess_bib_entry_type(rec)
    if entry_type == "article" and rec.venue:
        fields.append(("journal", rec.venue))
    elif entry_type == "inproceedings" and rec.venue:
        fields.append(("booktitle", rec.venue))
    elif rec.venue:
        fields.append(("howpublished", rec.venue))
    if rec.year:
        fields.append(("year", str(rec.year)))
    if rec.doi:
        fields.append(("doi", rec.doi))
    if rec.url:
        fields.append(("url", rec.url))
    return fields


def generate_bibtex_from_record(rec: PublicationRecord, existing_keys: set[str]) -> str:
    entry_type = _guess_bib_entry_type(rec)
    key = generate_bibtex_key(rec, existing_keys)
    lines = [f"@{entry_type}{{{key},"]
    for field, value in _bibtex_field_lines_from_record(rec):
        lines.append(f"  {field} = {{{value}}},")
    if len(lines) > 1:
        lines[-1] = lines[-1].rstrip(",")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _parse_bibtex_key(entry: str) -> Optional[str]:
    m = BIB_KEY_RE.search(entry)
    return m.group(1).strip() if m else None


def _replace_bibtex_key(entry: str, new_key: str) -> str:
    return re.sub(r"(?mi)^(@\w+\s*\{)\s*([^,\s]+)\s*,", rf"\1{new_key},", entry, count=1)


def _normalize_bibtex_entry_key(entry: str, rec: PublicationRecord, existing_keys: set[str]) -> str:
    key = _parse_bibtex_key(entry)
    if not key or key in existing_keys:
        new_key = generate_bibtex_key(rec, existing_keys)
        existing_keys.add(new_key)
        if key:
            return _replace_bibtex_key(entry, new_key)
        entry_type = _guess_bib_entry_type(rec)
        body = entry.strip()
        if body.startswith("@"):
            return body + "\n"
        return f"@{entry_type}{{{new_key},\n{body}\n}}\n"
    existing_keys.add(key)
    return entry if entry.endswith("\n") else entry + "\n"


def _strip_wrapping_braces_quotes(value: str) -> str:
    val = value.strip()
    if (val.startswith("{") and val.endswith("}")) or (val.startswith('"') and val.endswith('"')):
        return val[1:-1].strip()
    return val


def _normalize_bibtex_entry(rec: PublicationRecord, raw_entry: str, existing_keys: set[str]) -> str:
    entry = raw_entry.strip() + "\n"
    entry = _normalize_bibtex_entry_key(entry, rec, existing_keys)
    entry = bold_author_name_in_bibtex(entry)
    return entry if entry.endswith("\n") else entry + "\n"


def generate_or_fetch_bibtex_entry(rec: PublicationRecord, existing_keys: set[str], warnings: list[str]) -> str:
    if rec.doi:
        doi_bib = fetch_doi_bibtex(rec.doi)
        if doi_bib:
            return _normalize_bibtex_entry(rec, doi_bib, existing_keys)
        warnings.append(f"DOI BibTeX fetch failed for {rec.doi}; generating BibTeX from metadata")
    return _normalize_bibtex_entry(rec, generate_bibtex_from_record(rec, existing_keys), existing_keys)


def append_bibtex_entries(
    bib_path: Path,
    records: list[PublicationRecord],
    *,
    dry_run: bool = False,
) -> tuple[int, int, list[str], list[str]]:
    text = bib_path.read_text(encoding="utf-8") if bib_path.exists() else ""
    idx = parse_bibtex_index(text)
    warnings: list[str] = []
    added_entries: list[str] = []
    added_titles: list[str] = []
    skipped = 0
    for rec in records:
        if is_bib_duplicate(rec, idx):
            skipped += 1
            warnings.append(f"CV bib duplicate skipped: '{rec.title}'")
            continue
        entry = generate_or_fetch_bibtex_entry(rec, idx.keys, warnings)
        added_entries.append(entry.strip() + "\n")
        added_titles.append(rec.title)
        if rec.doi:
            idx.dois.add(rec.doi)
        idx.title_years.add((normalize_text_key(rec.title), rec.year))

    if added_entries and not dry_run:
        out = text
        if out and not out.endswith("\n"):
            out += "\n"
        if out and not out.endswith("\n\n"):
            out += "\n"
        out += "\n\n".join(e.rstrip() for e in added_entries) + "\n"
        bib_path.write_text(out, encoding="utf-8")

    return len(added_entries), skipped, warnings, added_titles


def parse_existing_publication_file(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    m = YAML_FRONT_MATTER_RE.match(text)
    front: dict[str, str] = {}
    body = text
    if m:
        body = text[m.end() :]
        front = parse_simple_yaml_mapping(m.group(1))
    return front, body


def parse_simple_yaml_mapping(raw: str) -> dict[str, str]:
    # Minimal parser for flat key: value front matter; enough for duplicate detection fields.
    result: dict[str, str] = {}
    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        if val in ("|", ">"):
            block_lines: list[str] = []
            while i < len(lines):
                nxt = lines[i]
                if nxt.startswith("  ") or not nxt.strip():
                    block_lines.append(nxt[2:] if nxt.startswith("  ") else "")
                    i += 1
                else:
                    break
            result[key] = "\n".join(block_lines).strip()
            continue
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        result[key] = val
    return result


def build_existing_index(publications_dir: Path) -> ExistingIndex:
    orcid_put_codes: set[str] = set()
    dois: set[str] = set()
    title_years: set[tuple[str, Optional[int]]] = set()

    for path in sorted(publications_dir.glob("*.md")):
        try:
            front, body = parse_existing_publication_file(path)
        except Exception:
            continue

        put_code = (front.get("orcid_put_code") or "").strip()
        if put_code:
            orcid_put_codes.add(put_code)

        for source in [
            front.get("doi", ""),
            front.get("paperurl", ""),
            front.get("citation", ""),
            body,
        ]:
            for doi in extract_dois(source or ""):
                dois.add(doi)

        title = (front.get("title") or "").strip()
        year = extract_year(front.get("date", "")) or extract_year(front.get("citation", ""))
        if title:
            title_years.add((normalize_text_key(title), year))

    return ExistingIndex(orcid_put_codes=orcid_put_codes, dois=dois, title_years=title_years)


def extract_year(text: str) -> Optional[int]:
    m = re.search(r"\b(19|20)\d{2}\b", text or "")
    return int(m.group(0)) if m else None


def is_duplicate(rec: PublicationRecord, idx: ExistingIndex) -> bool:
    if rec.orcid_put_code and rec.orcid_put_code in idx.orcid_put_codes:
        return True
    if rec.doi and rec.doi in idx.dois:
        return True
    title_key = normalize_text_key(rec.title)
    if title_key and (title_key, rec.year) in idx.title_years:
        return True
    return False


def ensure_valid_date_parts(year: Optional[int], month: Optional[int], day: Optional[int]) -> tuple[int, int, int]:
    y = year or 1900
    m = month or 1
    d = day or 1
    try:
        date(y, m, d)
    except ValueError:
        m = 1
        d = 1
        date(y, m, d)
    return y, m, d


def format_publication_markdown(rec: PublicationRecord, slug: str) -> str:
    y, m, d = ensure_valid_date_parts(rec.year, rec.month, rec.day)
    date_str = f"{y:04d}-{m:02d}-{d:02d}"
    permalink = f"/publication/{date_str}-{slug}"
    venue = rec.venue or "Unknown venue"
    paperurl = rec.url or (f"https://doi.org/{rec.doi}" if rec.doi else "")
    citation = rec.citation or build_citation(
        authors=rec.authors, year=y if rec.year else None, title=rec.title, venue=rec.venue, doi=rec.doi
    )
    excerpt = rec.abstract or ""

    fm_lines = [
        "---",
        f'title: "{yaml_quote(rec.title)}"',
        "collection: publications",
        f'permalink: "{permalink}"',
        f"date: {date_str}",
        f'venue: "{yaml_quote(venue)}"',
    ]
    if paperurl:
        fm_lines.append(f'paperurl: "{yaml_quote(paperurl)}"')
    if citation:
        fm_lines.append(f'citation: "{yaml_quote(citation)}"')
    if excerpt:
        fm_lines.append(f'excerpt: "{yaml_quote(excerpt)}"')
    fm_lines.append(f'orcid_put_code: "{yaml_quote(rec.orcid_put_code)}"')
    if rec.orcid_path:
        fm_lines.append(f'orcid_path: "{yaml_quote(rec.orcid_path)}"')
    if rec.doi:
        fm_lines.append(f'doi: "{yaml_quote(rec.doi)}"')
    fm_lines.append('source: "orcid-bot"')
    fm_lines.append("---")

    body_parts: list[str] = []
    if rec.abstract:
        body_parts.append("Abstract")
        body_parts.append("")
        body_parts.append(rec.abstract)
    links: list[str] = []
    if rec.doi:
        links.append(f"[DOI](https://doi.org/{rec.doi})")
    if rec.url and (not rec.doi or rec.url.rstrip("/") != f"https://doi.org/{rec.doi}".rstrip("/")):
        links.append(f"[Publisher link]({rec.url})")
    if links:
        if body_parts:
            body_parts.append("")
        body_parts.append("Links")
        body_parts.append("")
        for item in links:
            body_parts.append(f"- {item}")
    if body_parts:
        body_parts.append("")
    body_parts.append("_Generated from ORCID/Crossref metadata by orcid-bot._")

    return "\n".join(fm_lines) + "\n\n" + "\n".join(body_parts).strip() + "\n"


def yaml_quote(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def choose_unique_output_path(publications_dir: Path, rec: PublicationRecord) -> Path:
    y, m, d = ensure_valid_date_parts(rec.year, rec.month, rec.day)
    base = f"{y:04d}-{m:02d}-{d:02d}-{slugify(rec.title)}"
    candidate = publications_dir / f"{base}.md"
    idx = 2
    while candidate.exists():
        candidate = publications_dir / f"{base}-{idx}.md"
        idx += 1
    return candidate


def enrich_record(rec: PublicationRecord, warnings: list[str]) -> PublicationRecord:
    if not rec.doi:
        rec.citation = rec.citation or build_citation(
            authors=rec.authors, year=rec.year, title=rec.title, venue=rec.venue, doi=rec.doi
        )
        return rec

    payload = fetch_crossref_by_doi(rec.doi)
    if not payload:
        warnings.append(f"Crossref unavailable or not found for DOI {rec.doi}; using ORCID metadata for '{rec.title}'")
        rec.citation = rec.citation or build_citation(
            authors=rec.authors, year=rec.year, title=rec.title, venue=rec.venue, doi=rec.doi
        )
        return rec
    try:
        return parse_crossref_message(payload, rec)
    except Exception as exc:
        warnings.append(f"Crossref parse failed for DOI {rec.doi}: {exc}; using ORCID metadata")
        rec.citation = rec.citation or build_citation(
            authors=rec.authors, year=rec.year, title=rec.title, venue=rec.venue, doi=rec.doi
        )
        return rec


def sync_publications(
    *,
    orcid_id: str,
    publications_dir: Path,
    dry_run: bool = False,
    max_new: Optional[int] = None,
    verbose: bool = False,
    cv_bib_path: Optional[Path] = None,
    cv_dry_run: bool = False,
) -> SyncResult:
    if not publications_dir.exists():
        raise FileNotFoundError(f"Publications directory does not exist: {publications_dir}")

    payload = fetch_orcid_works(orcid_id)
    orcid_records = parse_orcid_works(payload)
    idx = build_existing_index(publications_dir)
    warnings: list[str] = []
    created_files: list[Path] = []
    created_records: list[PublicationRecord] = []
    skipped = 0

    for rec in orcid_records:
        if is_duplicate(rec, idx):
            skipped += 1
            continue

        enriched = enrich_record(rec, warnings)
        if is_duplicate(enriched, idx):
            skipped += 1
            continue

        out_path = choose_unique_output_path(publications_dir, enriched)
        slug = out_path.stem.split("-", 3)[-1] if len(out_path.stem.split("-", 3)) == 4 else slugify(enriched.title)
        content = format_publication_markdown(enriched, slug)
        if verbose:
            log(f"New publication: {enriched.title} -> {out_path}", verbose=True)
        if not dry_run:
            out_path.write_text(content, encoding="utf-8")
        created_files.append(out_path)
        created_records.append(enriched)

        # Update in-memory index to prevent duplicates within the same run.
        idx.orcid_put_codes.add(enriched.orcid_put_code)
        if enriched.doi:
            idx.dois.add(enriched.doi)
        idx.title_years.add((normalize_text_key(enriched.title), enriched.year))

        if max_new is not None and len(created_files) >= max_new:
            warnings.append(f"Stopped after reaching --max-new={max_new}")
            break

    result = SyncResult(
        created_files=created_files,
        created_records=created_records,
        skipped_existing=skipped,
        warnings=warnings,
    )

    if cv_bib_path is not None:
        try:
            if not cv_bib_path.exists():
                result.cv_warnings.append(f"CV bib file not found: {cv_bib_path}")
            else:
                added, cv_skipped, cv_warnings, cv_titles = append_bibtex_entries(
                    cv_bib_path,
                    created_records,
                    dry_run=cv_dry_run,
                )
                result.cv_bib_entries_added = added
                result.cv_bib_entries_skipped = cv_skipped
                result.cv_warnings.extend(cv_warnings)
                result.cv_added_titles.extend(cv_titles)
        except Exception as exc:
            result.cv_warnings.append(f"CV bib sync failed: {exc}")

    return result


def write_pr_body(result: SyncResult, path: Path) -> None:
    lines = ["## ORCID Sync Summary", ""]
    if not result.created_records:
        lines.extend(["No new publications were detected.", ""])
    else:
        lines.append(f"New publications added: **{len(result.created_records)}**")
        lines.append("")
        for rec in result.created_records:
            doi_note = f"DOI: `{rec.doi}`" if rec.doi else "DOI: _none_"
            date_bits = [str(rec.year or "unknown"), str(rec.month or 1), str(rec.day or 1)]
            lines.append(f"- **{rec.title}** ({'-'.join(date_bits)})")
            lines.append(f"  - {doi_note}")
            lines.append(f"  - Source: `{rec.source_quality}`")
    lines.extend(["", "## CV BibTeX Sync", ""])
    if result.cv_bib_entries_added or result.cv_bib_entries_skipped or result.cv_warnings:
        lines.append(f"Entries added to `publications.bib`: **{result.cv_bib_entries_added}**")
        lines.append(f"Entries skipped (duplicates): **{result.cv_bib_entries_skipped}**")
        if result.cv_added_titles:
            lines.append("")
            for title in result.cv_added_titles:
                lines.append(f"- Added to CV bib: **{title}**")
    else:
        lines.append("CV bib sync was not requested in this run.")
    if result.warnings:
        lines.extend(["", "### Warnings", ""])
        for w in result.warnings:
            lines.append(f"- {w}")
    if result.cv_warnings:
        lines.extend(["", "### CV Warnings", ""])
        for w in result.cv_warnings:
            lines.append(f"- {w}")
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_cv_pr_body(result: SyncResult, path: Path) -> None:
    lines = ["## ORCID Sync BibTeX Update", ""]
    lines.append(f"BibTeX entries added: **{result.cv_bib_entries_added}**")
    lines.append(f"BibTeX entries skipped (duplicates): **{result.cv_bib_entries_skipped}**")
    if result.cv_added_titles:
        lines.extend(["", "### Added Entries", ""])
        for rec in result.created_records:
            if rec.title in result.cv_added_titles:
                doi_note = f"DOI: `{rec.doi}`" if rec.doi else "DOI: _none_"
                lines.append(f"- **{rec.title}**")
                lines.append(f"  - {doi_note}")
    if result.cv_warnings:
        lines.extend(["", "### Warnings", ""])
        for w in result.cv_warnings:
            lines.append(f"- {w}")
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    publications_dir = Path(args.publications_dir)
    pr_body_path = Path(args.pr_body_path)
    cv_bib_path = Path(args.cv_bib_path) if args.cv_bib_path else None
    cv_pr_body_path = Path(args.cv_pr_body_path) if args.cv_pr_body_path else None

    try:
        result = sync_publications(
            orcid_id=args.orcid_id,
            publications_dir=publications_dir,
            dry_run=args.dry_run,
            max_new=args.max_new,
            verbose=args.verbose,
            cv_bib_path=cv_bib_path,
            cv_dry_run=args.cv_dry_run,
        )
    except HTTPError as exc:
        print(f"HTTP error while syncing ORCID publications: {exc}", file=sys.stderr)
        return 2
    except URLError as exc:
        print(f"Network error while syncing ORCID publications: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Failed to sync ORCID publications: {exc}", file=sys.stderr)
        return 1

    write_pr_body(result, pr_body_path)
    if cv_pr_body_path is not None:
        write_cv_pr_body(result, cv_pr_body_path)

    print(f"Skipped existing: {result.skipped_existing}")
    print(f"Created files: {len(result.created_files)}")
    for p in result.created_files:
        print(f" - {p}")
    if result.warnings:
        print("Warnings:")
        for w in result.warnings:
            print(f" - {w}")
    print(f"CV bib entries added: {result.cv_bib_entries_added}")
    print(f"CV bib entries skipped: {result.cv_bib_entries_skipped}")
    if result.cv_warnings:
        print("CV Warnings:")
        for w in result.cv_warnings:
            print(f" - {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
