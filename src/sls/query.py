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


def translate_for_acm(generic_query: str) -> QueryTranslation:
    """Translate the generic Boolean query syntax for ACM DL manual search.

    ACM is used as a human-executed source in this spike. The generated query is
    therefore a conservative browser-search query for the ACM Digital Library's
    all-field search box rather than an automated API request.
    """

    notes: list[str] = [
        "ACM spike uses the ACM Digital Library browser search and manual export workflow.",
        "Supported generic constructs for this spike: AND, OR, NOT, parentheses, bare terms, and quoted terms/phrases.",
        "The researcher must verify the final ACM query and exported result count in the browser.",
    ]
    query = generic_query.strip()

    if not query:
        raise ValueError("query must not be empty")

    if query.count("(") != query.count(")"):
        notes.append("Warning: parentheses are not balanced; the ACM browser query should be checked manually.")

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
        notes.append("Single-quoted generic terms were normalized to ACM-compatible bare terms or double-quoted phrases.")
    if saw_phrase:
        notes.append("Quoted multi-word phrases were preserved with double quotes.")
    if any(op in translated.upper().split() for op in _OPERATORS):
        notes.append("Boolean operators were preserved as uppercase query operators.")
    if "~" in translated:
        notes.append("Potentially unsupported construct detected: proximity/fuzzy marker '~'. Verify manually in ACM.")

    return QueryTranslation(
        generic_query=generic_query,
        source="acm",
        translated_query=translated,
        semantics_notes=notes,
    )


def translate_for_springer(generic_query: str) -> QueryTranslation:
    """Translate the generic Boolean query syntax to Springer Nature Meta API.

    The first Springer connector targets the basic, key-required metadata API.
    Basic keys support the `keyword` constraint, so unscoped generic terms are
    translated to `keyword:"term"` clauses. Richer field mappings can be added
    later for premium keys.
    """

    notes: list[str] = [
        "Springer spike uses the key-required Springer Nature metadata API.",
        "Supported generic constructs for this spike: AND, OR, NOT, parentheses, bare terms, and quoted terms/phrases.",
        "Unscoped generic terms are translated to Springer `keyword` constraints for compatibility with basic metadata keys.",
        "Springer API keys are read from the environment and must not be persisted in run artifacts.",
    ]
    query = generic_query.strip()

    if not query:
        raise ValueError("query must not be empty")

    if query.count("(") != query.count(")"):
        notes.append("Warning: parentheses are not balanced; the Springer request will still be prepared.")

    tokens = tokenize_generic_query(query)
    translated_parts: list[str] = []
    saw_single_quotes = False
    saw_phrase = False
    saw_operator = False

    for kind, value in tokens:
        if kind == "operator":
            translated_parts.append(value.upper())
            saw_operator = True
        elif kind == "paren":
            translated_parts.append(value)
        elif kind == "term":
            translated_parts.append(springer_keyword_clause(value))
        elif kind == "quoted":
            quote, content = value[0], value[1:]
            if quote == "'":
                saw_single_quotes = True
            normalized = " ".join(content.strip().split())
            if " " in normalized:
                saw_phrase = True
            translated_parts.append(springer_keyword_clause(normalized))

    translated = compact_boolean_query(" ".join(translated_parts))

    if saw_single_quotes:
        notes.append("Single-quoted generic terms were normalized to double-quoted Springer keyword values.")
    if saw_phrase:
        notes.append("Quoted multi-word phrases were preserved as single keyword phrases.")
    if saw_operator:
        notes.append("Boolean operators were preserved as uppercase Springer query operators.")
    if "~" in query:
        notes.append("Potentially unsupported construct detected: proximity/fuzzy marker '~'.")

    return QueryTranslation(
        generic_query=generic_query,
        source="springer",
        translated_query=translated,
        semantics_notes=notes,
    )


def tokenize_generic_query(query: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    index = 0
    while index < len(query):
        char = query[index]
        if char.isspace():
            index += 1
            continue
        if char in "()":
            tokens.append(("paren", char))
            index += 1
            continue
        if char in "'\"":
            quote = char
            index += 1
            start = index
            while index < len(query) and query[index] != quote:
                index += 1
            tokens.append(("quoted", quote + query[start:index]))
            if index < len(query):
                index += 1
            continue
        start = index
        while index < len(query) and not query[index].isspace() and query[index] not in "()":
            index += 1
        value = query[start:index]
        if value.upper() in _OPERATORS:
            tokens.append(("operator", value.upper()))
        else:
            tokens.append(("term", value))
    return tokens


def springer_keyword_clause(value: str) -> str:
    value = value.replace('"', r"\"")
    return f'keyword:"{value}"'


def compact_boolean_query(query: str) -> str:
    query = re.sub(r"\s+", " ", query).strip()
    query = query.replace("( ", "(").replace(" )", ")")
    return query
