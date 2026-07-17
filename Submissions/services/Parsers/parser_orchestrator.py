"""
Parser Orchestrator: Routes submissions through appropriate parsers based on schema.

This module acts as the central hub for parsing student submissions.
It analyzes the schema and coordinates the extraction of data from various file types.

Used by: Payload Assembler (for LLM evaluation)
"""

import os
import json
import zipfile
import tempfile
import fnmatch
from typing import Dict, Any, List, Optional
from pathlib import Path

from .registry import PARSER_REGISTRY, get_parser


class ParserOrchestrator:
    """
    Orchestrates parsing of student submissions based on schema requirements.
    
    Handles:
    - Single file submissions (ipynb, pdf, etc.)
    - ZIP file submissions with multiple files
    - Automatic routing to appropriate parsers
    - Extraction of all matching files from ZIPs
    """

    def parse_submission(
        self,
        submission_file_path: str,
        schema: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Parse a submission file according to the provided schema.

        Args:
            submission_file_path: Absolute path to the submission file.
            schema: JSON schema defining submission requirements and structure.

        Returns:
            Dictionary with structure:
            {
                "submission_type": str,  # "ipynb" or "zip"
                "status": "success" | "error",
                "parsed_files": [
                    {
                        "file_name": str,
                        "file_path": str,
                        "parser_used": str,
                        "content": Any,
                        "meta": Dict[str, Any]
                    },
                    ...
                ],
                "error": str | None
            }

        Raises:
            FileNotFoundError: If submission file does not exist.
            ValueError: If schema is invalid or submission type is unsupported.
        """
        if not os.path.exists(submission_file_path):
            raise FileNotFoundError(f"Submission file not found: {submission_file_path}")

        submission_type = schema.get("submission_type")

        try:
            if submission_type == "ipynb":
                return self._parse_ipynb_submission(submission_file_path, schema)
            elif submission_type == "zip":
                return self._parse_zip_submission(submission_file_path, schema)
            else:
                return {
                    "submission_type": submission_type,
                    "status": "error",
                    "parsed_files": [],
                    "error": f"Unsupported submission_type: {submission_type}"
                }
        except Exception as e:
            return {
                "submission_type": submission_type,
                "status": "error",
                "parsed_files": [],
                "error": str(e)
            }

    def _parse_ipynb_submission(
        self,
        file_path: str,
        schema: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Parse a single Jupyter Notebook submission.
        """
        parser = get_parser("ipynb_code_and_markdown")
        
        try:
            parsed_result = parser.parse(file_path)
            return {
                "submission_type": "ipynb",
                "status": "success",
                "parsed_files": [
                    {
                        "file_name": os.path.basename(file_path),
                        "file_path": file_path,
                        "parser_used": parsed_result["parser_type"],
                        "content": parsed_result["content"],
                        "meta": parsed_result["meta"]
                    }
                ],
                "error": None
            }
        except Exception as e:
            return {
                "submission_type": "ipynb",
                "status": "error",
                "parsed_files": [],
                "error": f"Failed to parse ipynb: {str(e)}"
            }

    def _parse_zip_submission(
        self,
        zip_file_path: str,
        schema: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Extract and parse all matching files from a ZIP submission.
        """
        zip_schema = schema.get("zip_schema", {})
        required_patterns = zip_schema.get("required_files", [])
        optional_patterns = zip_schema.get("optional_files", [])
        forbidden_extensions = set(zip_schema.get("forbidden_extensions", []))
        
        parsed_files = []
        errors = []

        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                with zipfile.ZipFile(zip_file_path, 'r') as zip_ref:
                    zip_ref.extractall(temp_dir)

                # Get all extracted files
                all_files = self._get_all_files_recursive(temp_dir)

                # Filter and parse matching files
                for file_path in all_files:
                    file_name = os.path.basename(file_path)
                    file_ext = os.path.splitext(file_name)[1].lower()

                    # Skip forbidden extensions
                    if file_ext in forbidden_extensions:
                        continue

                    # Skip hidden/system files
                    if file_name.startswith('.'):
                        continue

                    # Match against patterns
                    if self._matches_patterns(file_path, temp_dir, required_patterns + optional_patterns):
                        try:
                            parsed_result = self._parse_file(file_path)
                            if parsed_result:
                                parsed_files.append({
                                    "file_name": file_name,
                                    "file_path": file_path.replace(temp_dir, ""),  # Relative path
                                    "parser_used": parsed_result.get("parser_type"),
                                    "content": parsed_result.get("content"),
                                    "meta": parsed_result.get("meta")
                                })
                        except Exception as e:
                            errors.append(f"{file_name}: {str(e)}")

            except zipfile.BadZipFile as e:
                return {
                    "submission_type": "zip",
                    "status": "error",
                    "parsed_files": [],
                    "error": f"Invalid ZIP file: {str(e)}"
                }
            except Exception as e:
                return {
                    "submission_type": "zip",
                    "status": "error",
                    "parsed_files": [],
                    "error": f"Failed to process ZIP: {str(e)}"
                }

        return {
            "submission_type": "zip",
            "status": "success" if not errors else "partial",
            "parsed_files": parsed_files,
            "error": "; ".join(errors) if errors else None
        }

    def _parse_file(self, file_path: str) -> Optional[Dict[str, Any]]:
        """
        Determine the appropriate parser and parse a single file.
        """
        file_ext = os.path.splitext(file_path)[1].lower()
        file_name = os.path.basename(file_path).lower()

        # Route to appropriate parser
        if file_ext == '.ipynb':
            parser = get_parser("ipynb_code_and_markdown")
        elif file_ext in {'.docx', '.pdf', '.txt'}:
            parser = get_parser("document_text")
        elif file_ext == '.csv':
            parser = get_parser("tabular_text")
        elif file_ext in {'.png', '.jpg', '.jpeg'}:
            parser = get_parser("image_ocr")
        elif file_ext in {'.sql', ''} or file_name == 'dockerfile':
            parser = get_parser("plain_text")
        else:
            return None  # Unsupported file type

        return parser.parse(file_path)

    def _matches_patterns(
        self,
        file_path: str,
        base_dir: str,
        patterns: List[str]
    ) -> bool:
        """
        Check if a file path matches any of the provided glob patterns.
        """
        if not patterns:
            return True  # No patterns means match all

        relative_path = os.path.relpath(file_path, base_dir)

        for pattern in patterns:
            if fnmatch.fnmatch(relative_path, pattern):
                return True
            if fnmatch.fnmatch(os.path.basename(file_path), pattern):
                return True

        return False

    def _get_all_files_recursive(self, directory: str) -> List[str]:
        """
        Recursively get all file paths in a directory.
        """
        all_files = []
        for root, dirs, files in os.walk(directory):
            for file in files:
                all_files.append(os.path.join(root, file))
        return all_files


# Singleton instance for easy module-level access
_orchestrator = ParserOrchestrator()


def parse_submission(
    submission_file_path: str,
    schema: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Parse a submission according to its schema.
    
    Convenience function that uses the singleton orchestrator.

    Args:
        submission_file_path: Path to the submission file.
        schema: JSON schema defining submission requirements.

    Returns:
        Parsed submission data with status and parsed files.
    """
    return _orchestrator.parse_submission(submission_file_path, schema)
