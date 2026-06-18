from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class QueryTranslation:
    generic_query: str
    source: str
    translated_query: str
    semantics_notes: list[str]


_QUOTED = re.compile(r"(['\"])(.*?)\1")
_OPERATORS = {"AND", "OR", "NOT"}


def translate_for_eg(generic_query: str) -> QueryTranslation:
    """Translate the small generic Boolean query syntax to EG/DSpace query text.

    This spike intentionally keeps the grammar conservative: Boolean operators,
    parentheses, bare terms, and quoted terms/phrases. Single-quoted terms are
    normalized because DSpace search behaves more predictably with bare terms or
    double-quoted phrases.
    """

    notes: list[str] = [
        "EG spike uses DSpace discovery query syntax.",
        "Supported generic constructs for this spike: AND, OR, NOT, parentheses, bare terms, and quoted terms/phrases.",
    ]
    query = generic_query.strip()

    if not query:
        raise ValueError("query must not be empty")

    if query.count("(") != query.count(")"):
        notes.append("Warning: parentheses are not balanced; EG request will still be attempted.")

    saw_single_quotes = False
    saw_phrase = False

    def replace_quoted(match: re.Match[str]) -> str:
        nonlocal saw_single_quotes, saw_phrase
        quote = match.group(1)
        value = " ".join(match.group(2).strip().split())
        if quote == "'":
            saw_single_quotes = True
        if " " in value:
            saw_phrase = True
            return f'"{value}"'
        return value

    translated = _QUOTED.sub(replace_quoted, query)

    tokens = re.split(r"(\W+)", translated)
    translated = "".join(
        token.upper() if token.upper() in _OPERATORS else token for token in tokens
    )
    translated = re.sub(r"\s+", " ", translated).strip()

    if saw_single_quotes:
        notes.append("Single-quoted generic terms were normalized to EG-compatible bare terms or double-quoted phrases.")
    if saw_phrase:
        notes.append("Quoted multi-word phrases were preserved with double quotes.")
    if any(op in translated.upper().split() for op in _OPERATORS):
        notes.append("Boolean operators were preserved as uppercase DSpace query operators.")

    unsupported = []
    if "~" in translated:
        unsupported.append("proximity/fuzzy marker '~'")
    if unsupported:
        notes.append("Potentially unsupported constructs detected: " + ", ".join(unsupported) + ".")

    return QueryTranslation(
        generic_query=generic_query,
        source="eg",
        translated_query=translated,
        semantics_notes=notes,
    )

