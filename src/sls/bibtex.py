from __future__ import annotations

import re


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

