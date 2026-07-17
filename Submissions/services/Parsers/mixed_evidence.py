"""
Mixed evidence parser: delegates to other parsers based on file extension.
"""

import os
from typing import Dict, Any
from .base import BaseParser
from .image_ocr import ImageOCRParser
from .tabular_text import TabularTextParser
from .plain_text import PlainTextParser
from .ppt_parser import PPTParser


class MixedEvidenceParser(BaseParser):
    """
    Router parser that delegates to appropriate specialized parsers
    based on file extension.
    
    Supports routing to:
    - ImageOCRParser for images (.png, .jpg, .jpeg)
    - TabularTextParser for CSVs (.csv)
    - PlainTextParser for text files (.txt, .sql, Dockerfile)
    - PPTParser for PowerPoint files (.pptx)
    """

    def __init__(self):
        self.image_parser = ImageOCRParser()
        self.csv_parser = TabularTextParser()
        self.text_parser = PlainTextParser()
        self.ppt_parser = PPTParser()

    def parse(self, file_path: str) -> Dict[str, Any]:
        """
        Route the file to the appropriate parser based on extension.

        Args:
            file_path: Path to the file.

        Returns:
            Dictionary with parser_type, content, and metadata from the appropriate parser.

        Raises:
            FileNotFoundError: If file does not exist.
            ValueError: If file format is not supported by any parser.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        file_ext = os.path.splitext(file_path)[1].lower()
        file_name = os.path.basename(file_path).lower()

        # Route based on extension
        if file_ext in ImageOCRParser.SUPPORTED_EXTENSIONS:
            return self.image_parser.parse(file_path)

        elif file_ext in TabularTextParser.SUPPORTED_EXTENSIONS:
            return self.csv_parser.parse(file_path)

        elif file_ext in PPTParser.SUPPORTED_EXTENSIONS:
            return self.ppt_parser.parse(file_path)

        elif (file_ext in PlainTextParser.SUPPORTED_EXTENSIONS or
              file_name in PlainTextParser.SUPPORTED_NAMES):
            return self.text_parser.parse(file_path)

        else:
            raise ValueError(
                f"Unsupported file format: {file_ext}. "
                f"Supported formats: .png, .jpg, .jpeg, .csv, .pptx, .txt, .sql, Dockerfile"
            )
