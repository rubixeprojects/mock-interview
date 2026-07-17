import os
import re
import zipfile
import fnmatch
import tempfile
from typing import Optional

from django.core.files.uploadedfile import UploadedFile


class ValidationResult:
    """
    Standard response object for validation.
    `issues` holds all collected (code, message) pairs for multi-error results.
    """
    def __init__(self, is_valid: bool, message: str = "", code: str = "", issues: list = None):
        self.is_valid = is_valid
        self.message = message
        self.code = code
        self.issues = issues or []

    def to_dict(self):
        return {
            "is_valid": self.is_valid,
            "message": self.message,
            "code": self.code,
            "issues": self.issues,
        }


class SubmissionValidator:
    """
    Schema-driven submission validator
    """

    def __init__(self, submission_schema: dict):
        self.schema = submission_schema or {}

    # ---------- PUBLIC API ----------

    def validate(self, uploaded_file: Optional[UploadedFile]) -> ValidationResult:
        """
        Entry point for validation
        """
        # 1. Required check
        if self.schema.get("required", False) and not uploaded_file:
            return ValidationResult(
                False,
                "Submission file is required.",
                "FILE_REQUIRED"
            )

        if not uploaded_file:
            return ValidationResult(True, "No submission required.")

        # 2. Extension check
        ext = os.path.splitext(uploaded_file.name)[1].lower()
        allowed_exts = self.schema.get("allowed_extensions", [])

        if ext not in allowed_exts:
            return ValidationResult(
                False,
                f"Invalid file type '{ext}'. Allowed: {allowed_exts}",
                "INVALID_EXTENSION"
            )

        submission_type = self.schema.get("submission_type")

        # 3. Branch by submission type
        if submission_type == "ipynb":
            return self._validate_ipynb(uploaded_file)

        if submission_type == "zip":
            return self._validate_zip(uploaded_file)

        return ValidationResult(
            False,
            "Unsupported submission type.",
            "UNSUPPORTED_SUBMISSION_TYPE"
        )

    # ---------- IPYNB VALIDATION ----------

    def _validate_ipynb(self, uploaded_file: UploadedFile) -> ValidationResult:
        """
        Simple validation for .ipynb
        """
        if not uploaded_file.name.lower().endswith(".ipynb"):
            return ValidationResult(
                False,
                "Only .ipynb files are allowed.",
                "INVALID_IPYNB"
            )

        return ValidationResult(True, "Valid notebook submission.")

    # ---------- ZIP VALIDATION ----------

    def _validate_zip(self, uploaded_file: UploadedFile) -> ValidationResult:
        # Multi-track schema (e.g. client projects with Cloud / Docker tracks)
        tracks = self.schema.get("tracks")
        if tracks:
            return self._validate_zip_multi_track(uploaded_file, tracks)

        zip_schema = self.schema.get("zip_schema")

        if not zip_schema:
            return ValidationResult(
                False,
                "Zip schema missing for zip submission.",
                "ZIP_SCHEMA_MISSING"
            )

        if not zipfile.is_zipfile(uploaded_file):
            return ValidationResult(
                False,
                "Uploaded file is not a valid zip archive.",
                "INVALID_ZIP"
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                with zipfile.ZipFile(uploaded_file) as zip_ref:
                    zip_ref.extractall(temp_dir)
            except Exception:
                return ValidationResult(
                    False,
                    "Failed to extract zip file.",
                    "ZIP_EXTRACTION_FAILED"
                )

            extracted_files = self._list_all_files(temp_dir)
            issues = []

            forbidden_exts = zip_schema.get("forbidden_extensions", [])
            accepted_exts = self._accepted_extensions(zip_schema)

            for f in extracted_files:
                file_ext = os.path.splitext(f)[1].lower()
                if not file_ext:
                    continue
                if file_ext in forbidden_exts:
                    issues.append(("FORBIDDEN_FILE", f"Forbidden file type detected: {f} (extension: {file_ext})"))
                elif accepted_exts and file_ext not in accepted_exts:
                    issues.append(("UNRECOGNIZED_FILE", f"Unrecognized file type: {os.path.basename(f)} (extension: {file_ext})"))

            for pattern in zip_schema.get("required_files", []):
                if not self._match_pattern(extracted_files, pattern):
                    issues.append(("REQUIRED_FILE_MISSING", f"Required file missing: {pattern}"))

            if not issues:
                return ValidationResult(True, "Valid zip submission.")

            if len(issues) == 1:
                code, message = issues[0]
                return ValidationResult(False, message, code)

            first_code, first_message = issues[0]
            return ValidationResult(False, first_message, "MULTIPLE_ERRORS", issues=issues)

    def _validate_zip_multi_track(self, uploaded_file: UploadedFile, tracks: dict) -> ValidationResult:
        """
        For projects with multiple submission tracks (e.g. Cloud vs Docker),
        validate against every track and accept if any passes.
        If all fail, report issues from the best-matching track (fewest problems).
        """
        if not zipfile.is_zipfile(uploaded_file):
            return ValidationResult(False, "Uploaded file is not a valid zip archive.", "INVALID_ZIP")

        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                with zipfile.ZipFile(uploaded_file) as zip_ref:
                    zip_ref.extractall(temp_dir)
            except Exception:
                return ValidationResult(False, "Failed to extract zip file.", "ZIP_EXTRACTION_FAILED")

            extracted_files = self._list_all_files(temp_dir)

            best_track_label = None
            best_issues = None

            for track_key, track_data in tracks.items():
                zip_schema = track_data.get("zip_schema", {})
                track_label = track_data.get("label", track_key)
                issues = []

                forbidden_exts = zip_schema.get("forbidden_extensions", [])
                accepted_exts = self._accepted_extensions(zip_schema)

                for f in extracted_files:
                    file_ext = os.path.splitext(f)[1].lower()
                    if not file_ext:
                        continue
                    if file_ext in forbidden_exts:
                        issues.append(("FORBIDDEN_FILE", f"Forbidden file type detected: {f} (extension: {file_ext})"))
                    elif accepted_exts and file_ext not in accepted_exts:
                        issues.append(("UNRECOGNIZED_FILE", f"Unrecognized file type: {os.path.basename(f)} (extension: {file_ext})"))

                for pattern in zip_schema.get("required_files", []):
                    if not self._match_pattern(extracted_files, pattern):
                        issues.append(("REQUIRED_FILE_MISSING", f"Required file missing: {pattern}"))

                # If this track passes, the submission is valid
                if not issues:
                    return ValidationResult(True, f"Valid submission for track: {track_label}.")

                # Track the best match (fewest issues)
                if best_issues is None or len(issues) < len(best_issues):
                    best_issues = issues
                    best_track_label = track_label

            # All tracks failed — report issues from the closest-matching track
            prefix_issue = ("TRACK_INFO", f"Checked against track: {best_track_label}")
            all_issues = [prefix_issue] + best_issues

            if len(best_issues) == 1:
                code, message = best_issues[0]
                return ValidationResult(False, message, code, issues=all_issues)

            first_code, first_message = best_issues[0]
            return ValidationResult(False, first_message, "MULTIPLE_ERRORS", issues=all_issues)

    def _accepted_extensions(self, zip_schema: dict) -> set:
        """Derive accepted extensions from required_files + optional_files patterns."""
        patterns = zip_schema.get("required_files", []) + zip_schema.get("optional_files", [])
        accepted = set()
        for pattern in patterns:
            if '(' in pattern and ')' in pattern:
                m = re.search(r'\(([^)]+)\)', pattern)
                if m:
                    for ext in m.group(1).split('|'):
                        accepted.add('.' + ext.strip().lower().lstrip('.'))
            else:
                ext = os.path.splitext(pattern)[1].lower()
                if ext:
                    accepted.add(ext)
        return accepted

    # ---------- HELPERS ----------

    def _list_all_files(self, base_path: str) -> list[str]:
        files = []
        for root, _, filenames in os.walk(base_path):
            for name in filenames:
                full_path = os.path.join(root, name)
                relative = os.path.relpath(full_path, base_path)
                files.append(relative.replace("\\", "/"))
        return files

    def _match_pattern(self, files: list[str], pattern: str) -> bool:
        """
        Match files against a glob pattern.
        Supports bash-style patterns with | for OR:
        - Example: *.(doc|docx|pdf|txt) matches .doc, .docx, .pdf, .txt files

        Matching is tried against both the full relative path and the basename,
        so ZIPs with a wrapping top-level folder (e.g. Mac "Compress") work correctly.
        """
        # Build a deduplicated list of names to check: full paths + basenames
        names_to_check = list({name for f in files for name in (f, os.path.basename(f))})

        def expand_alternatives(p):
            match = re.search(r'\(([^)]+)\)', p)
            if not match:
                return [p]
            alternatives = match.group(1).split('|')
            prefix = p[:match.start()]
            suffix = p[match.end():]
            return [f"{prefix}{alt}{suffix}" for alt in alternatives]

        if '|' in pattern:
            expanded = expand_alternatives(pattern)
            return any(
                any(fnmatch.fnmatch(name, exp_pattern) for exp_pattern in expanded)
                for name in names_to_check
            )
        else:
            return any(fnmatch.fnmatch(name, pattern) for name in names_to_check)
