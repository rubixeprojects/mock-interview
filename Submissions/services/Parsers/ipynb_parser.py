"""
Parser for Jupyter Notebook files: .ipynb
"""

import os
import json
from typing import Dict, Any, List
from .base import BaseParser


class NotebookParser(BaseParser):
    """
    Extracts code cells and markdown cells from Jupyter Notebook files.
    
    Preserves execution order and distinguishes between cell types.
    """

    SUPPORTED_EXTENSIONS = {'.ipynb'}

    def parse(self, file_path: str) -> Dict[str, Any]:
        """
        Parse a Jupyter Notebook and extract cells.

        Args:
            file_path: Path to the .ipynb file.

        Returns:
            Dictionary with parser_type, content (cells), and metadata.

        Raises:
            FileNotFoundError: If file does not exist.
            ValueError: If file is not a valid notebook.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        file_ext = os.path.splitext(file_path)[1].lower()
        if file_ext != '.ipynb':
            raise ValueError(f"Expected .ipynb file, got: {file_ext}")

        file_name = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)

        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                notebook = json.load(f)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid notebook JSON: {e}")

        # Extract cells preserving order
        cells = self._extract_cells(notebook)

        return {
            "parser_type": "ipynb_code_and_markdown",
            "content": cells,
            "meta": {
                "file_name": file_name,
                "file_size": file_size,
                "total_cells": len(cells),
                "code_cell_count": sum(1 for c in cells if c["cell_type"] == "code"),
                "markdown_cell_count": sum(1 for c in cells if c["cell_type"] == "markdown"),
                "notebook_version": notebook.get("nbformat", "unknown")
            }
        }

    def _extract_cells(self, notebook: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extract all cells from the notebook in order.
        """
        cells_data = []

        cells = notebook.get("cells", [])
        for idx, cell in enumerate(cells):
            cell_type = cell.get("cell_type", "unknown")

            if cell_type == "code":
                cells_data.append({
                    "order": idx,
                    "cell_type": "code",
                    "source": self._join_source(cell.get("source", [])),
                    "execution_count": cell.get("execution_count"),
                    "has_output": bool(cell.get("outputs"))
                })
            elif cell_type == "markdown":
                cells_data.append({
                    "order": idx,
                    "cell_type": "markdown",
                    "source": self._join_source(cell.get("source", []))
                })
            else:
                # Capture raw, unrecognized cell types
                cells_data.append({
                    "order": idx,
                    "cell_type": cell_type,
                    "source": self._join_source(cell.get("source", []))
                })

        return cells_data

    @staticmethod
    def _join_source(source) -> str:
        """
        Join source lines into a single string.
        
        Source can be a list of strings or a single string.
        """
        if isinstance(source, list):
            return ''.join(source)
        return str(source) if source else ""
