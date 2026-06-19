from __future__ import annotations

import html
import re
import unicodedata

from .identity import normalize_doi


def escape_bibtex(value: str) -> str:
    return (
        value.replace("\\", "\\textbackslash{}")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("&", "\\&")
    )


def entry_type(record: dict[str, str]) -> str:
    if record.get("volume") or record.get("number"):
        return "article"
    if record.get("venue"):
        return "inproceedings"
    return "misc"


def record_to_bibtex(record: dict[str, str]) -> str:
    key = record["bibtex_key"]
    fields: list[tuple[str, str]] = [
        ("title", record.get("title", "")),
        ("author", " and ".join(a.strip() for a in record.get("authors", "").split(";") if a.strip())),
        ("year", record.get("year", "")),
    ]
    if record.get("doi"):
        fields.append(("doi", record["doi"]))
    if record.get("canonical_url"):
        fields.append(("url", record["canonical_url"]))
    if record.get("venue"):
        name = "journal" if entry_type(record) == "article" else "booktitle"
        fields.append((name, record["venue"]))
    for name in ("volume", "number", "pages", "publisher"):
        if record.get(name):
            fields.append((name, record[name]))

    lines = [f"@{entry_type(record)}{{{key},"]
    present = [(name, value) for name, value in fields if value]
    for index, (name, value) in enumerate(present):
        suffix = "," if index < len(present) - 1 else ""
        lines.append(f"  {name} = {{{escape_bibtex(value)}}}{suffix}")
    lines.append("}")
    return "\n".join(lines)


def parse_bibtex_keys(text: str) -> set[str]:
    return set(re.findall(r"@\w+\s*\{\s*([^,\s]+)", text))


def parse_bibtex_records(text: str, *, source: str = "manual") -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for entry in iter_bibtex_entries(text):
        fields = parse_bibtex_fields(entry["body"])
        record = bibtex_fields_to_record(entry["key"], fields, source=source)
        if record.get("title") or record.get("doi") or record.get("canonical_url"):
            records.append(record)
    return records


def iter_bibtex_entries(text: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    index = 0
    while True:
        match = re.search(r"@(\w+)\s*\{", text[index:])
        if not match:
            break
        entry_type_name = match.group(1)
        open_brace = index + match.end() - 1
        close_brace = find_matching_brace(text, open_brace)
        if close_brace == -1:
            break
        content = text[open_brace + 1 : close_brace]
        key, _, body = content.partition(",")
        entries.append(
            {
                "type": entry_type_name.lower(),
                "key": key.strip(),
                "body": body,
            }
        )
        index = close_brace + 1
    return entries


def find_matching_brace(text: str, open_brace: int) -> int:
    depth = 0
    escaped = False
    for index in range(open_brace, len(text)):
        char = text[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def parse_bibtex_fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    index = 0
    while index < len(body):
        while index < len(body) and body[index] in " \t\r\n,":
            index += 1
        name_match = re.match(r"[A-Za-z][A-Za-z0-9_-]*", body[index:])
        if not name_match:
            index += 1
            continue
        name = name_match.group(0).lower()
        index += len(name_match.group(0))
        while index < len(body) and body[index].isspace():
            index += 1
        if index >= len(body) or body[index] != "=":
            continue
        index += 1
        while index < len(body) and body[index].isspace():
            index += 1
        value, index = parse_bibtex_value(body, index)
        fields[name] = clean_bibtex_value(value)
    return fields


def parse_bibtex_value(body: str, index: int) -> tuple[str, int]:
    if index >= len(body):
        return "", index
    if body[index] == "{":
        close = find_matching_brace(body, index)
        if close == -1:
            return body[index + 1 :], len(body)
        return body[index + 1 : close], close + 1
    if body[index] == '"':
        index += 1
        chars: list[str] = []
        escaped = False
        while index < len(body):
            char = body[index]
            if escaped:
                chars.append(char)
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                return "".join(chars), index + 1
            else:
                chars.append(char)
            index += 1
        return "".join(chars), index
    start = index
    while index < len(body) and body[index] != ",":
        index += 1
    return body[start:index].strip(), index


def clean_bibtex_value(value: str) -> str:
    value = latex_to_text(value)
    value = value.replace(r"\&", "&")
    value = value.replace(r"\_", "_")
    value = value.replace(r"\%", "%")
    value = value.replace(r"\\", "\\")
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


ACCENT_MARKS = {
    "'": "\u0301",
    "`": "\u0300",
    '"': "\u0308",
    "^": "\u0302",
    "~": "\u0303",
    "=": "\u0304",
    ".": "\u0307",
    "u": "\u0306",
    "v": "\u030c",
    "H": "\u030b",
    "r": "\u030a",
    "c": "\u0327",
    "k": "\u0328",
    "b": "\u0331",
    "d": "\u0323",
}

LATEX_SYMBOLS = {
    r"\aa": "\u00e5",
    r"\AA": "\u00c5",
    r"\ae": "\u00e6",
    r"\AE": "\u00c6",
    r"\oe": "\u0153",
    r"\OE": "\u0152",
    r"\o": "\u00f8",
    r"\O": "\u00d8",
    r"\l": "\u0142",
    r"\L": "\u0141",
    r"\ss": "\u00df",
}


def latex_to_text(value: str) -> str:
    value = replace_latex_accents(value)
    for command, replacement in LATEX_SYMBOLS.items():
        value = value.replace(command, replacement)
    value = re.sub(r"\\([{}])", r"\1", value)
    value = re.sub(r"[{}]", "", value)
    return unicodedata.normalize("NFC", value)


def replace_latex_accents(value: str) -> str:
    accent_chars = re.escape("".join(ACCENT_MARKS))
    braced = re.compile(r"\\([" + accent_chars + r"])\s*\{\\?([A-Za-z])\}")
    compact = re.compile(r"\\([" + accent_chars + r"])\s*\\?([A-Za-z])")

    def replace(match: re.Match[str]) -> str:
        return unicodedata.normalize("NFC", match.group(2) + ACCENT_MARKS[match.group(1)])

    previous = None
    while previous != value:
        previous = value
        value = braced.sub(replace, value)
        value = compact.sub(replace, value)
    return value


def bibtex_fields_to_record(key: str, fields: dict[str, str], *, source: str) -> dict[str, str]:
    doi = normalize_doi(fields.get("doi", ""))
    url = fields.get("url", "") or fields.get("ee", "")
    canonical_url = acm_canonical_url(doi, url) if source == "acm" else url
    authors = fields.get("author", "")
    return {
        "source": source,
        "source_record_id": doi or key,
        "title": fields.get("title", ""),
        "authors": "; ".join(part.strip() for part in re.split(r"\s+and\s+", authors) if part.strip()),
        "year": year_from_fields(fields),
        "doi": doi,
        "canonical_url": canonical_url,
        "venue": fields.get("booktitle", "") or fields.get("journal", ""),
        "publisher": fields.get("publisher", ""),
        "pages": fields.get("pages", ""),
        "volume": fields.get("volume", ""),
        "number": fields.get("number", ""),
        "source_api_url": "",
    }


def acm_canonical_url(doi: str, url: str) -> str:
    if doi.startswith("10.1145/"):
        return f"https://dl.acm.org/doi/{doi}"
    return url or (f"https://doi.org/{doi}" if doi else "")


def year_from_fields(fields: dict[str, str]) -> str:
    for name in ("year", "date"):
        match = re.search(r"(\d{4})", fields.get(name, ""))
        if match:
            return match.group(1)
    return ""
