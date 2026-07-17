"""
Parsers package: Modular parser system for processing submission files.

This package provides a configuration-driven approach to parsing various file types.
Each parser is independent, deterministic, and project-agnostic.

Usage:
    from parsers.registry import PARSER_REGISTRY, get_parser
    
    # Option 1: Direct registry access
    parser = PARSER_REGISTRY["document_text"]
    result = parser.parse("path/to/file.docx")
    
    # Option 2: Using the getter function
    parser = get_parser("document_text")
    result = parser.parse("path/to/file.docx")

Available parsers:
    - "document_text": Extracts text from .docx, .pdf, .txt
    - "plain_text": Returns raw text from .txt, .sql, Dockerfile
    - "ipynb_code_and_markdown": Extracts cells from Jupyter Notebooks
    - "image_ocr": Processes images (.png, .jpg, .jpeg)
    - "tabular_text": Parses CSV files with headers and preview
    - "mixed_evidence": Routes files to appropriate parser by extension
"""

from .base import BaseParser
from .registry import PARSER_REGISTRY, get_parser

__all__ = [
    "BaseParser",
    "PARSER_REGISTRY",
    "get_parser"
]
