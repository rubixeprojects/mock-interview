"""
Parser for plain text files: Dockerfile, .sql, .txt
"""

import os
from typing import Dict, Any
from .base import BaseParser


class PlainTextParser(BaseParser):
    """
    Extracts raw text from plain text files including Dockerfile and .sql files.
    
    Returns the raw file contents with minimal processing.
    """

    SUPPORTED_EXTENSIONS = {'.txt', '.sql', ''}  # Empty string for Dockerfile
    SUPPORTED_NAMES = {'dockerfile'}

    def parse(self, file_path: str) -> Dict[str, Any]:
        """
        Parse a plain text file and return raw content.

        Args:
            file_path: Path to the plain text file.

        Returns:
            Dictionary with parser_type, content (raw text), and metadata.

        Raises:
            FileNotFoundError: If file does not exist.
            ValueError: If file format is not supported.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        file_name = os.path.basename(file_path)
        file_ext = os.path.splitext(file_path)[1].lower()

        # Check if file is supported
        is_supported = (
            file_ext in self.SUPPORTED_EXTENSIONS or
            file_name.lower() in self.SUPPORTED_NAMES
        )

        if not is_supported:
            raise ValueError(
                f"Unsupported file format: {file_name}. "
                f"Supported: Dockerfile, .txt, .sql"
            )

        file_size = os.path.getsize(file_path)

        # Read raw content
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        return {
            "parser_type": "plain_text",
            "content": content,
            "meta": {
                "file_name": file_name,
                "file_size": file_size,
                "line_count": len(content.splitlines())
            }
        }
