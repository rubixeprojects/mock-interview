#!/usr/bin/env python3
"""
Test script for CDE Project Submission Parser.

Usage:
    python test_payload_assembler.py <project_id> <submission_file_path> [--track=<track_id>]

Examples:
    python test_payload_assembler.py proj_001 /path/to/submission.ipynb
    python test_payload_assembler.py proj_001 /path/to/submission.zip
    python test_payload_assembler.py PRCL-01 /path/to/submission.zip --track=cloud
    python test_payload_assembler.py PRCL-01 /path/to/submission.zip --track=local_docker
"""

import sys
import os
import json
from pathlib import Path

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("Warning: python-dotenv not installed. Using system environment variables.")

# Import the payload assembler
from Parsers.payload_assembler import assemble_and_evaluate


def print_result(result: dict):
    """Print only the LLM evaluation result (no metadata)."""
    print("\n" + "=" * 80)
    print("EVALUATION RESULT")
    print("=" * 80 + "\n")
    
    if result.get('status') == 'success' and result.get('evaluation'):
        eval_data = result['evaluation']
        # Only print the LLM evaluation text
        llm_evaluation = eval_data.get('llm_evaluation')
        if llm_evaluation:
            print(llm_evaluation)
        else:
            print("No evaluation content available.")
    else:
        error_msg = result.get('error') or (result.get('evaluation', {}).get('error') if result.get('evaluation') else None)
        print(f"ERROR: {error_msg}")
    
    print("\n" + "=" * 80)


def main():
    """Main test function."""
    
    # Parse command line arguments
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    
    project_id = sys.argv[1]
    submission_file_path = sys.argv[2]
    track = None
    
    # Parse optional --track parameter
    for arg in sys.argv[3:]:
        if arg.startswith("--track="):
            track = arg.split("=")[1]
    
    # Validate inputs
    if not Path(submission_file_path).exists():
        print(f"Error: Submission file not found: {submission_file_path}")
        sys.exit(1)
    
    print(f"Testing Payload Assembler")
    print(f"Project ID: {project_id}")
    print(f"Submission File: {submission_file_path}")
    if track:
        print(f"Track: {track}")
    print(f"\nProcessing...\n")
    
    try:
        # Call the main function
        result = assemble_and_evaluate(
            project_id=project_id,
            submission_file_path=submission_file_path,
            track=track
        )
        
        # Print results
        print_result(result)
        
        # Exit with appropriate code
        if result.get('status') == 'success':
            sys.exit(0)
        else:
            sys.exit(1)
            
    except Exception as e:
        print(f"Error: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
