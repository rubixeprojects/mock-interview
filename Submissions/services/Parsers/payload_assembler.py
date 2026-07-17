"""
Payload Assembler: Coordinates parsing and LLM evaluation of student submissions.

Workflow:
1. Take Project ID and submission file path
2. Query MySQL to get question, prompt, and parser schema
3. Call ParserOrchestrator to parse submission
4. Assemble payload (question + prompt + parsed content)
5. Send to LLM for evaluation
6. Return evaluation result
"""

import json
from typing import Dict, Any, Optional, Tuple
from .parser_orchestrator import parse_submission
from .llm_service import get_llm_service, BaseLLMService


# Standard formatting instruction to be appended to all evaluation prompts
STANDARD_FORMATTING_INSTRUCTION = """
FORMATTING REQUIREMENTS FOR YOUR RESPONSE:
- Write in plain text without markdown symbols (no *, #, +, -, or other formatting characters)
- Focus on evaluating the SUBMISSION itself, not the student
- Use neutral language addressing the submission directly:
  * Instead of "The student failed to...", use "The submission lacks..."
  * Instead of "The student demonstrated...", use "The submission demonstrates..."
  * Instead of "You missed...", use "Missing elements include..."
- Structure EACH evaluation section using EXACTLY this format:

SECTION HEADING
Present Implementation Comments:
[Detailed feedback on what the submission currently implements or demonstrates]

Scope for Improvement:
[Specific, actionable recommendations for improvement]

________________________________________

- The section heading MUST be on its own line in UPPERCASE
- Each section MUST contain both "Present Implementation Comments:" and "Scope for Improvement:" subsections, in that order
- Use exactly "________________________________________" (40 underscores) to separate every section
- Always end your entire response with:

________________________________________
Overall Grade: [A or B or C or D - single letter only, no plus/minus or modifiers]

Do not deviate from this format. The grade MUST be a single letter (A, B, C, or D only).
"""


class PayloadAssembler:
    """
    Assembles evaluation payloads and orchestrates LLM evaluation of submissions.
    Uses Gemini with automatic fallback to Groq for robustness.
    """

    def __init__(self, llm_service: BaseLLMService = None):
        """
        Initialize PayloadAssembler.

        Args:
            llm_service: Optional LLM service instance. If None, uses Gemini with Groq fallback.
        """
        self.llm_service = llm_service

    def assemble_and_evaluate(
        self,
        project_id: str,
        submission_file_path: str,
        question: str,
        prompt: str,
        parser_schema: Dict[str, Any],
        track: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Main orchestration: parse -> assemble -> evaluate.

        Args:
            project_id: Project/Assignment ID.
            submission_file_path: Path to the submission file (ipynb or zip).
            question: Assignment question/requirements.
            prompt: Evaluation prompt for the LLM.
            parser_schema: Schema defining submission structure.
            track: Optional track selection for multi-track projects (e.g., "cloud" or "local_docker").

        Returns:
            Dictionary with evaluation results:
            {
                "project_id": str,
                "status": "success" | "error" | "partial",
                "evaluation": {
                    "question": str,
                    "prompt": str,
                    "parsed_content": Dict,
                    "llm_evaluation": str,
                    "model_used": str
                },
                "parsing_report": {
                    "files_parsed": int,
                    "status": str,
                    "errors": str | None
                },
                "error": str | None
            }
        """
        try:
            # If track-aware validation is needed, inject track into schema
            if track:
                parser_schema = parser_schema.copy() if isinstance(parser_schema, dict) else {}
                parser_schema["track"] = track

            # Step 1: Parse submission
            parsed_result = parse_submission(submission_file_path, parser_schema)

            if parsed_result["status"] == "error":
                return {
                    "project_id": project_id,
                    "status": "error",
                    "evaluation": None,
                    "parsing_report": parsed_result,
                    "error": f"Parsing failed: {parsed_result.get('error')}"
                }

            # Step 2: Assemble payload
            assembled_payload, evaluation_prompt = self._assemble_payload(
                question=question,
                prompt=prompt,
                parsed_files=parsed_result["parsed_files"],
                submission_metadata=parsed_result
            )

            # Step 3: Send to LLM for evaluation
            llm_evaluation = self._evaluate_with_llm(assembled_payload, evaluation_prompt)

            # Step 4: Return results
            return {
                "project_id": project_id,
                "status": "success" if llm_evaluation.get("success") else "evaluation_failed",
                "evaluation": {
                    "question": question,
                    "prompt": prompt,
                    "parsed_content": self._summarize_parsed_content(parsed_result["parsed_files"]),
                    "llm_evaluation": llm_evaluation.get("evaluation"),
                    "model_used": llm_evaluation.get("model_used")
                },
                "parsing_report": {
                    "submission_type": parsed_result.get("submission_type"),
                    "files_parsed": len(parsed_result.get("parsed_files", [])),
                    "status": parsed_result.get("status"),
                    "errors": parsed_result.get("error")
                },
                "error": llm_evaluation.get("error") if not llm_evaluation.get("success") else None
            }

        except Exception as e:
            return {
                "project_id": project_id,
                "status": "error",
                "evaluation": None,
                "error": f"Unexpected error in payload assembly: {str(e)}"
            }



    def _assemble_payload(
        self,
        question: str,
        prompt: str,
        parsed_files: list,
        submission_metadata: Dict[str, Any]
    ) -> Tuple[str, str]:
        """
        Assemble the complete evaluation payload as a formatted string.

        Args:
            question: Assignment question/requirements.
            prompt: Evaluation prompt for the LLM (from database).
            parsed_files: List of parsed file contents.
            submission_metadata: Submission parsing metadata.

        Returns:
            Tuple of (payload_string, prompt_string) - payload for context, prompt for LLM instruction
        """
        payload_parts = []

        # Add question
        payload_parts.append("=" * 60)
        payload_parts.append("ASSIGNMENT QUESTION:")
        payload_parts.append("=" * 60)
        payload_parts.append(str(question) if question else "")

        # Add parsed content (without the prompt - that will be separate)
        payload_parts.append("\n" + "=" * 60)
        payload_parts.append("STUDENT SUBMISSION CONTENT:")
        payload_parts.append("=" * 60)

        for i, file_data in enumerate(parsed_files, 1):
            file_name = file_data.get("file_name", "unknown")
            parser_used = file_data.get("parser_used", "unknown")
            content = file_data.get("content")

            payload_parts.append(f"\n--- File {i}: {file_name} (parsed with {parser_used}) ---")

            # Format content based on type
            if isinstance(content, dict):
                # For dicts (like image LLM results), show key values
                payload_parts.append(json.dumps(content, indent=2))
            elif isinstance(content, list):
                # For lists, join elements
                payload_parts.append("\n".join(str(item) if not isinstance(item, dict) else json.dumps(item) for item in content))
            else:
                # For strings and others
                payload_parts.append(str(content) if content else "")

        # Convert prompt to string if it's a dict
        prompt_str = json.dumps(prompt, indent=2) if isinstance(prompt, dict) else str(prompt) if prompt else ""
        
        return "\n".join(str(part) for part in payload_parts), prompt_str

    def _summarize_parsed_content(self, parsed_files: list) -> Dict[str, Any]:
        """
        Create a summary of parsed content for the response.

        Args:
            parsed_files: List of parsed file data.

        Returns:
            Summary dictionary with file names and content snippets.
        """
        summary = {}
        for file_data in parsed_files:
            file_name = file_data.get("file_name", "unknown")
            content = file_data.get("content")

            # Store first 500 chars or summary
            if isinstance(content, str):
                summary[file_name] = content[:500] + "..." if len(content) > 500 else content
            elif isinstance(content, dict):
                summary[file_name] = content
            else:
                summary[file_name] = str(content)[:500]

        return summary

    def _evaluate_with_llm(self, payload: str, evaluation_prompt: str) -> Dict[str, Any]:
        """
        Send assembled payload to LLM for evaluation using the prompt from database.
        Uses Gemini with automatic fallback to Groq.
        Automatically appends standard formatting instructions to ensure consistent output.

        Args:
            payload: Formatted evaluation payload string (question + content).
            evaluation_prompt: Evaluation prompt/instructions from database.

        Returns:
            Dictionary with evaluation results:
            {
                "success": bool,
                "evaluation": str,
                "model_used": str,
                "error": str | None
            }
        """
        try:
            # Get or initialize LLM service (Gemini with Groq fallback)
            if not self.llm_service:
                self.llm_service = get_llm_service("gemini")

            # Combine the database prompt with standard formatting instructions
            # The database prompt contains project-specific evaluation criteria
            # The standard instruction ensures consistent formatting across all evaluations
            base_prompt = evaluation_prompt if evaluation_prompt else "Evaluate the submission fairly and provide detailed feedback."
            system_prompt = f"{base_prompt}\n\n{STANDARD_FORMATTING_INSTRUCTION}"

            # Send to LLM (Gemini with auto-fallback to Groq)
            result = self.llm_service.analyze_text(
                text=payload,
                prompt=system_prompt
            )

            return {
                "success": result.get("success", False),
                "evaluation": result.get("content"),
                "model_used": result.get("model_used"),
                "error": result.get("error")
            }

        except Exception as e:
            return {
                "success": False,
                "evaluation": None,
                "model_used": None,
                "error": f"LLM evaluation failed: {str(e)}"
            }


# Convenience function
def assemble_and_evaluate(
    project_id: str,
    submission_file_path: str,
    question: str = None,
    prompt: str = None,
    parser_schema: Dict[str, Any] = None,
    llm_service: BaseLLMService = None,
    track: Optional[str] = None
) -> Dict[str, Any]:
    """
    Quick function to assemble and evaluate a submission.

    Args:
        project_id: Project ID.
        submission_file_path: Path to submission file.
        question: Assignment question/requirements.
        prompt: Evaluation prompt for the LLM.
        parser_schema: Schema defining submission structure.
        llm_service: Optional custom LLM service.
        track: Optional track selection for multi-track projects.

    Returns:
        Evaluation results dictionary.
    """
    # If project details not provided, fetch from database (for backward compatibility)
    if not question or not prompt or not parser_schema:
        from django.core.management import call_command
        from django.db import connection
        
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT ProjectName, LLMInputPrompt, ExpectedSubmissionJSON 
                FROM submissions_project_registry 
                WHERE ProjectID = %s
            """, [project_id])
            row = cursor.fetchone()
        
        if not row:
            return {
                "project_id": project_id,
                "status": "error",
                "evaluation": None,
                "error": f"Project {project_id} not found in database"
            }
        
        question = row[0]
        prompt = row[1]
        expected_submission = row[2]
        
        if isinstance(expected_submission, str):
            expected_submission = json.loads(expected_submission)
        
        # Convert schema format
        submission_type = expected_submission.get("submission_type", "ipynb")
        parser_schema = {"submission_type": submission_type}
        
        if submission_type == "zip" and "zip_schema" in expected_submission:
            zip_schema = expected_submission["zip_schema"]
            if isinstance(zip_schema, dict):
                parser_schema["zip_schema"] = {
                    "required_files": zip_schema.get("required_files", []),
                    "optional_files": zip_schema.get("optional_files", []),
                    "forbidden_extensions": zip_schema.get("forbidden_extensions", []),
                    "max_uncompressed_size_mb": zip_schema.get("max_uncompressed_size_mb", 500)
                }
    
    assembler = PayloadAssembler(llm_service=llm_service)
    return assembler.assemble_and_evaluate(
        project_id, submission_file_path, question, prompt, parser_schema, track=track
    )
