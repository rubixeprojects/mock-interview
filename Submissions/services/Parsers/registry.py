"""
Parser Registry: Central mapping of parser names to parser instances.

The evaluator will use this registry to look up and call parsers dynamically:

    from parsers.registry import PARSER_REGISTRY
    
    parser = PARSER_REGISTRY["document_text"]
    result = parser.parse(file_path)
"""

from .document_text import DocumentTextParser
from .plain_text import PlainTextParser
from .ipynb_parser import NotebookParser
from .image_ocr import ImageOCRParser
from .tabular_text import TabularTextParser
from .ppt_parser import PPTParser
from .mixed_evidence import MixedEvidenceParser


# Lazy initialization of parsers to avoid errors if GROQ_API_KEY is not set
_parsers = {}

def _get_parser_instance(parser_name: str):
    """Get or create a parser instance lazily."""
    if parser_name not in _parsers:
        if parser_name == "document_text":
            _parsers[parser_name] = DocumentTextParser()
        elif parser_name == "plain_text":
            _parsers[parser_name] = PlainTextParser()
        elif parser_name == "ipynb_code_and_markdown":
            _parsers[parser_name] = NotebookParser()
        elif parser_name == "image_ocr":
            _parsers[parser_name] = ImageOCRParser()
        elif parser_name == "tabular_text":
            _parsers[parser_name] = TabularTextParser()
        elif parser_name == "pptx":
            _parsers[parser_name] = PPTParser()
        elif parser_name == "mixed_evidence":
            _parsers[parser_name] = MixedEvidenceParser()
    return _parsers.get(parser_name)


# Build PARSER_REGISTRY with lazy loading
class LazyParserRegistry(dict):
    """Dictionary that lazily initializes parsers on access."""
    
    def __init__(self):
        super().__init__()
        self._parser_names = [
            "document_text",
            "plain_text",
            "ipynb_code_and_markdown",
            "image_ocr",
            "tabular_text",
            "pptx",
            "mixed_evidence"
        ]
    
    def __getitem__(self, key):
        if key not in self._parser_names:
            raise KeyError(f"Parser '{key}' not found in registry")
        return _get_parser_instance(key)
    
    def __contains__(self, key):
        return key in self._parser_names
    
    def keys(self):
        return self._parser_names


PARSER_REGISTRY = LazyParserRegistry()


def get_parser(parser_name: str):
    """
    Retrieve a parser from the registry by name.

    Args:
        parser_name: The name of the parser (e.g., "document_text").

    Returns:
        The parser instance.

    Raises:
        KeyError: If parser_name is not in the registry.
    """
    if parser_name not in PARSER_REGISTRY:
        available = ", ".join(PARSER_REGISTRY._parser_names)
        raise KeyError(
            f"Parser '{parser_name}' not found. "
            f"Available parsers: {available}"
        )
    return PARSER_REGISTRY[parser_name]
