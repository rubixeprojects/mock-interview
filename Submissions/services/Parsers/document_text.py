"""
Parser for document files: .docx, .pdf, .txt
"""

import os
from typing import Dict, Any
from .base import BaseParser


class DocumentTextParser(BaseParser):
    """
    Extracts text from .docx, .pdf, and .txt files.
    
    Returns extracted text and basic metadata including file size and encoding.
    """

    SUPPORTED_EXTENSIONS = {'.docx', '.pdf', '.txt'}

    def parse(self, file_path: str) -> Dict[str, Any]:
        """
        Parse a document file and extract text content.

        Args:
            file_path: Path to the document file.

        Returns:
            Dictionary with parser_type, content (extracted text), and metadata.

        Raises:
            FileNotFoundError: If file does not exist.
            ValueError: If file format is not supported.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        file_ext = os.path.splitext(file_path)[1].lower()
        if file_ext not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported file format: {file_ext}. "
                f"Supported: {self.SUPPORTED_EXTENSIONS}"
            )

        file_name = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)

        if file_ext == '.txt':
            content = self._parse_txt(file_path)
        elif file_ext == '.docx':
            content = self._parse_docx(file_path)
        elif file_ext == '.pdf':
            content = self._parse_pdf(file_path)

        return {
            "parser_type": "document_text",
            "content": content,
            "meta": {
                "file_name": file_name,
                "file_size": file_size,
                "file_format": file_ext
            }
        }

    def _parse_txt(self, file_path: str) -> str:
        """
        Read and return contents of a .txt file.
        """
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()

    def _parse_docx(self, file_path: str) -> str:
        """
        Extract text from a .docx file.
        
        Requires python-docx library.
        """
        try:
            from docx import Document
        except ImportError:
            raise ImportError(
                "python-docx is required for .docx parsing. "
                "Install it with: pip install python-docx"
            )

        doc = Document(file_path)
        paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
        return '\n'.join(paragraphs)

    def _parse_pdf(self, file_path: str) -> str:
        """
        Extract text from a .pdf file.
        
        Requires PyPDF2 library.
        """
        try:
            from PyPDF2 import PdfReader
        except ImportError:
            raise ImportError(
                "PyPDF2 is required for .pdf parsing. "
                "Install it with: pip install PyPDF2"
            )

        text_content = []
        with open(file_path, 'rb') as f:
            reader = PdfReader(f)
            for page_num, page in enumerate(reader.pages):
                text = page.extract_text()
                if text:
                    text_content.append(text)

        return '\n'.join(text_content)
