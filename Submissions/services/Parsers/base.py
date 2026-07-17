"""
Base parser interface for the modular parser system.
All parsers must inherit from BaseParser and implement the parse method.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any


class BaseParser(ABC):
    """
    Abstract base class for all parsers.
    
    Each parser is responsible for extracting structured data from a specific
    file type. Parsers are deterministic and must not perform evaluation or grading.
    """

    @abstractmethod
    def parse(self, file_path: str) -> Dict[str, Any]:
        """
        Parse a file and return structured data.

        Args:
            file_path: Absolute path to the file to parse.

        Returns:
            A dictionary with the following structure:
            {
                "parser_type": str,  # Name of the parser used
                "content": Any,       # Extracted content (text, dict, list, etc.)
                "meta": {
                    "file_name": str,
                    "file_size": int,
                    "encoding": str,  # Optional
                    ... other metadata
                }
            }

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the file format is unsupported or invalid.
        """
        pass
