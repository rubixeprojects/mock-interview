import re
import os
from django.utils.safestring import mark_safe


def format_validation_error(validation_result, schema, uploaded_filename):
    """
    Convert raw validation errors into user-friendly messages.
    
    Args:
        validation_result: ValidationResult object with (is_valid, message, code)
        schema: The submission schema dict
        uploaded_filename: Name of the uploaded file
    
    Returns:
        User-friendly error message string (HTML safe with <br> tags)
    """
    if validation_result.is_valid:
        return None
    
    code = validation_result.code
    message = validation_result.message
    
    # Extract file extension
    file_ext = os.path.splitext(uploaded_filename)[1].lower()
    
    # ========== ERROR CODE HANDLERS ==========
    
    if code == "MULTIPLE_ERRORS":
        msg = _format_multiple_errors(uploaded_filename, validation_result.issues)
    elif code == "INVALID_EXTENSION":
        msg = _format_invalid_extension(schema, uploaded_filename, file_ext)
    elif code == "REQUIRED_FILE_MISSING":
        msg = _format_required_file_missing(schema, uploaded_filename, message)
    elif code == "FORBIDDEN_FILE":
        msg = _format_forbidden_file(schema, uploaded_filename, message)
    elif code == "UNRECOGNIZED_FILE":
        msg = _format_unrecognized_file(uploaded_filename, message)
    elif code == "INVALID_ZIP":
        msg = _format_invalid_zip(uploaded_filename)
    elif code == "FILE_REQUIRED":
        msg = "Submission Failed<br><br>This submission requires a file to be uploaded."
    elif code == "ZIP_EXTRACTION_FAILED":
        msg = "Submission Failed<br><br>Unable to extract the ZIP file. Please ensure it's not corrupted."
    elif code == "ZIP_SCHEMA_MISSING":
        msg = "Submission Failed<br><br>The submission format for this project is not configured. Please contact support."
    elif code == "UNSUPPORTED_SUBMISSION_TYPE":
        msg = "Submission Failed<br><br>This project has an unsupported submission format configured. Please contact support."
    else:
        # Fallback for unknown errors
        msg = f"Submission Failed<br><br>{message}"
    
    # Convert newlines to HTML line breaks
    msg = msg.replace('\n', '<br>')
    return mark_safe(msg)


# ========== FORMATTING HELPERS ==========

def _format_invalid_extension(schema, uploaded_filename, file_ext):
    """Format error for wrong file extension."""
    allowed = schema.get("allowed_extensions", [])
    submission_type = schema.get("submission_type", "")
    
    # Format allowed extensions nicely
    allowed_str = _format_extensions(allowed)
    
    # Add submission type context
    type_context = ""
    if submission_type == "zip":
        type_context = " (ZIP archive)"
    elif submission_type == "ipynb":
        type_context = " (Jupyter Notebook)"
    
    return (
        f"Submission Failed\n"
        f"Uploaded: {uploaded_filename}\n\n"
        f"This project requires: {allowed_str}{type_context}"
    )


def _format_required_file_missing(schema, uploaded_filename, message=""):
    """Format error for missing required files in ZIP."""
    missing_pattern = None
    prefix = "Required file missing: "
    if message and message.startswith(prefix):
        missing_pattern = message[len(prefix):]

    if missing_pattern:
        missing_str = _describe_required_pattern(missing_pattern)
    else:
        zip_schema = schema.get("zip_schema", {})
        required_patterns = zip_schema.get("required_files", [])
        readable_formats = [r for p in required_patterns if (r := _parse_file_pattern(p))]
        missing_str = " and ".join(readable_formats) if readable_formats else "required files"

    return (
        f"Submission Failed\n"
        f"Uploaded: {uploaded_filename}\n\n"
        f"Your ZIP is missing a required file:\n"
        f"• {missing_str}"
    )


def _format_forbidden_file(schema, uploaded_filename, message):
    """Format error for forbidden file type detected in ZIP."""
    bad_file, bad_ext = _parse_forbidden_message(message)
    if bad_file:
        return (
            f"Submission Failed\n"
            f"Uploaded: {uploaded_filename}\n\n"
            f"Your ZIP contains a file that is not allowed:\n"
            f"• {bad_file} — {bad_ext} files are not permitted in this submission"
        )
    # Fallback if message couldn't be parsed
    zip_schema = schema.get("zip_schema", {})
    forbidden = zip_schema.get("forbidden_extensions", [])
    forbidden_str = _format_extensions(forbidden) if forbidden else "forbidden file types"
    return (
        f"Submission Failed\n"
        f"Uploaded: {uploaded_filename}\n\n"
        f"Contains prohibited file type.\n"
        f"Forbidden: {forbidden_str}"
    )


def _format_invalid_zip(uploaded_filename):
    """Format error for corrupted or invalid ZIP."""
    return (
        f"Submission Failed\n"
        f"Uploaded: {uploaded_filename}\n\n"
        f"This file is not a valid ZIP archive. Please check and try again."
    )


def _format_unrecognized_file(uploaded_filename, message):
    """Format error for a single unrecognized file type inside the ZIP."""
    bad_file, bad_ext = _parse_unrecognized_message(message)
    if bad_file:
        return (
            f"Submission Failed\n"
            f"Uploaded: {uploaded_filename}\n\n"
            f"Your ZIP contains a file that is not accepted for this submission:\n"
            f"• {bad_file} — {bad_ext} files are not accepted"
        )
    return f"Submission Failed\n\n{message}"


def _format_multiple_errors(uploaded_filename, issues):
    """Format all collected ZIP issues as a numbered list."""
    lines = [
        "Submission Failed",
        f"Uploaded: {uploaded_filename}",
        "",
    ]

    # If a TRACK_INFO issue is present, show it as context before the errors
    track_info = next((msg for code, msg in issues if code == "TRACK_INFO"), None)
    real_issues = [(c, m) for c, m in issues if c != "TRACK_INFO"]

    if track_info:
        lines.append(f"Validated against: {track_info.split(': ', 1)[-1]}")
        lines.append("")

    lines.append("The following issues were found in your ZIP:")

    for issue_code, issue_message in real_issues:
        if issue_code == "REQUIRED_FILE_MISSING":
            prefix = "Required file missing: "
            pattern = issue_message[len(prefix):] if issue_message.startswith(prefix) else issue_message
            readable = _describe_required_pattern(pattern)
            lines.append(f"• Missing required file: {readable}")
        elif issue_code == "FORBIDDEN_FILE":
            bad_file, bad_ext = _parse_forbidden_message(issue_message)
            if bad_file:
                lines.append(f"• {bad_file} — {bad_ext} files are not permitted")
            else:
                lines.append(f"• {issue_message}")
        elif issue_code == "UNRECOGNIZED_FILE":
            bad_file, bad_ext = _parse_unrecognized_message(issue_message)
            if bad_file:
                lines.append(f"• {bad_file} — {bad_ext} files are not accepted for this submission")
            else:
                lines.append(f"• {issue_message}")
        else:
            lines.append(f"• {issue_message}")
    return "\n".join(lines)


# ========== PATTERN DESCRIPTION ==========

# Special-case descriptions for patterns that would otherwise produce confusing messages
_PATTERN_DESCRIPTIONS = {
    "*.zip":        "an inner ZIP archive (e.g. project code, notebooks, or pipeline exports)",
    "dockerfile":   "a file named exactly 'Dockerfile' (no extension) — the Docker build file",
    "*.(yml|yaml)": "a Docker Compose file (.yml or .yaml)",
    "docker-compose.yml":  "a docker-compose.yml file",
    "docker-compose.yaml": "a docker-compose.yaml file",
    "*.ipynb":      "a Jupyter Notebook (.ipynb)",
}


def _describe_required_pattern(pattern: str) -> str:
    """
    Return a human-readable description of a required-file pattern.
    Uses special-case descriptions for patterns that would otherwise be confusing,
    falls back to _parse_file_pattern for everything else.
    """
    key = pattern.strip().lower()
    if key in _PATTERN_DESCRIPTIONS:
        return _PATTERN_DESCRIPTIONS[key]
    return _parse_file_pattern(pattern) or pattern


# ========== UTILITY FUNCTIONS ==========

def _format_extensions(ext_list):
    """
    Convert ['.ipynb', '.pdf'] to 'IPYNB or PDF files'
    """
    if not ext_list:
        return "required file types"
    
    # Remove dots and convert to uppercase
    formatted = [ext.lstrip('.').upper() for ext in ext_list]
    
    if len(formatted) == 1:
        return f"{formatted[0]} files"
    
    return " or ".join(formatted) + " files"


def _parse_forbidden_message(message):
    """Extract (basename, EXT) from 'Forbidden file type detected: path (extension: .ext)'."""
    if message:
        m = re.search(r'Forbidden file type detected: (.+?) \(extension: (.+?)\)', message)
        if m:
            return os.path.basename(m.group(1)), m.group(2).upper().lstrip(".")
    return None, None


def _parse_unrecognized_message(message):
    """Extract (basename, EXT) from 'Unrecognized file type: name (extension: .ext)'."""
    if message:
        m = re.search(r'Unrecognized file type: (.+?) \(extension: (.+?)\)', message)
        if m:
            return m.group(1), m.group(2).upper().lstrip(".")
    return None, None


def _parse_file_pattern(pattern):
    """
    Convert glob patterns to readable format.
    Examples:
    - *.(sql|txt|pdf|doc|docx) → "SQL, TXT, PDF, DOC, or DOCX files"
    - *.ipynb → "IPYNB files"
    - *.zip → "ZIP files"
    """
    if not pattern:
        return None
    
    # Pattern with alternatives: *.(ext1|ext2|ext3)
    if '(' in pattern and ')' in pattern:
        match = re.search(r'\(([^)]+)\)', pattern)
        if match:
            alternatives = match.group(1).split('|')
            # Clean and uppercase
            clean = [alt.strip().lstrip('.').upper() for alt in alternatives]
            
            if len(clean) == 1:
                return f"{clean[0]} files"
            elif len(clean) == 2:
                return f"{clean[0]} or {clean[1]} files"
            else:
                # Last one gets "or", others get commas
                return ", ".join(clean[:-1]) + f", or {clean[-1]} files"
    
    # Simple pattern: *.ipynb
    else:
        ext = pattern.lstrip('*.').strip().upper()
        if ext:
            return f"{ext} files"
    
    return None
