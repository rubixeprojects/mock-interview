"""
Parser for tabular data files: .csv
"""

import os
import csv
from typing import Dict, Any, List
from .base import BaseParser


class TabularTextParser(BaseParser):
    """
    Extracts column names and a preview of rows from CSV files.
    
    Returns structured data with headers and sample rows.
    """

    SUPPORTED_EXTENSIONS = {'.csv'}
    PREVIEW_ROWS = 10  # Number of rows to include in preview

    def parse(self, file_path: str) -> Dict[str, Any]:
        """
        Parse a CSV file and extract column names and row preview.

        Args:
            file_path: Path to the CSV file.

        Returns:
            Dictionary with parser_type, content (headers and preview), and metadata.

        Raises:
            FileNotFoundError: If file does not exist.
            ValueError: If file format is not supported or CSV is invalid.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        file_ext = os.path.splitext(file_path)[1].lower()
        if file_ext != '.csv':
            raise ValueError(f"Expected .csv file, got: {file_ext}")

        file_name = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)

        try:
            headers, preview_rows, total_rows = self._parse_csv(file_path)
        except Exception as e:
            raise ValueError(f"Failed to parse CSV file: {e}")

        return {
            "parser_type": "tabular_text",
            "content": {
                "headers": headers,
                "preview_rows": preview_rows
            },
            "meta": {
                "file_name": file_name,
                "file_size": file_size,
                "total_rows": total_rows,
                "column_count": len(headers)
            }
        }

    def _parse_csv(self, file_path: str) -> tuple:
        """
        Read CSV file and extract headers and row preview.

        Returns:
            Tuple of (headers, preview_rows, total_rows)
        """
        headers = []
        preview_rows = []
        total_rows = 0

        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.reader(f)

            # Read headers
            try:
                headers = next(reader)
            except StopIteration:
                raise ValueError("CSV file is empty")

            # Read preview rows
            for idx, row in enumerate(reader):
                if idx < self.PREVIEW_ROWS:
                    preview_rows.append(row)
                total_rows += 1

        return headers, preview_rows, total_rows
