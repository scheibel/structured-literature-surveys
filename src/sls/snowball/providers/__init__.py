from .openalex import OpenAlex
from .semantic_scholar import SemanticScholar
from .opencitations import OpenCitations

PROVIDERS = {p.name: p for p in (OpenAlex, SemanticScholar, OpenCitations)}
