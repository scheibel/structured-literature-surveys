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


def translate_for_dblp(generic_query: str) -> QueryTranslation:
    """Translate the generic Boolean query syntax to DBLP publication search.

    DBLP's search language is intentionally lightweight: whitespace expresses
    conjunction and `|` expresses disjunction. Phrase search and boolean NOT are
    not reliable enough for an automated equivalence claim, so the translator
    records those losses explicitly.
    """

    notes: list[str] = [
        "DBLP connector uses the official publication search API at https://dblp.org/search/publ/api.",
        "DBLP search treats whitespace-separated terms as boolean AND and pipe-separated terms as boolean OR.",
        "DBLP performs case-insensitive prefix matching by default; exact-word matching requires DBLP's `$` suffix and is not inferred by this translator.",
        "DBLP currently documents phrase search and boolean NOT as disabled or degraded, so those generic constructs are not semantically preserved.",
        "DBLP search results are capped at 1000 hits; broad queries should be partitioned manually.",
    ]
    query = generic_query.strip()

    if not query:
        raise ValueError("query must not be empty")

    if query.count("(") != query.count(")"):
        notes.append("Warning: parentheses are not balanced; DBLP request will still be prepared.")

    tokens = tokenize_generic_query(query)
    translated_parts: list[str] = []
    saw_single_quotes = False
    saw_phrase = False
    saw_not = False
    saw_operator = False
    skip_next = False

    for kind, value in tokens:
        if kind == "operator":
            operator = value.upper()
            saw_operator = True
            if operator == "AND":
                translated_parts.append(" ")
            elif operator == "OR":
                translated_parts.append("|")
            elif operator == "NOT":
                saw_not = True
                skip_next = True
        elif kind == "paren":
            translated_parts.append(value)
        elif kind == "term":
            if skip_next:
                skip_next = False
                continue
            translated_parts.append(dblp_search_term(value))
        elif kind == "quoted":
            quote, content = value[0], value[1:]
            if quote == "'":
                saw_single_quotes = True
            normalized = " ".join(content.strip().split())
            if " " in normalized:
                saw_phrase = True
            if skip_next:
                skip_next = False
                continue
            translated_parts.append(dblp_search_term(normalized))

    translated = compact_dblp_query("".join(translated_parts))

    if saw_single_quotes:
        notes.append("Single-quoted generic terms were normalized to DBLP search terms.")
    if saw_phrase:
        notes.append("Quoted phrases were split into DBLP whitespace-conjoined terms because DBLP phrase search is documented as disabled.")
    if saw_operator:
        notes.append("Generic AND was translated to whitespace and OR to `|`, matching DBLP's documented search operators.")
    if saw_not:
        notes.append("Generic NOT terms were omitted because DBLP documents boolean NOT as disabled/degraded.")
    if "~" in query:
        notes.append("Potentially unsupported construct detected: proximity/fuzzy marker '~'.")

    return QueryTranslation(
        generic_query=generic_query,
        source="dblp",
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


def dblp_search_term(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def compact_boolean_query(query: str) -> str:
    query = re.sub(r"\s+", " ", query).strip()
    query = query.replace("( ", "(").replace(" )", ")")
    return query


def compact_dblp_query(query: str) -> str:
    query = re.sub(r"\s+", " ", query).strip()
    query = query.replace("( ", "(").replace(" )", ")")
    query = query.replace(" |", "|").replace("| ", "|")
    return query
