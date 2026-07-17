"""
Parser for image files: .png, .jpg, .jpeg

Analyzes images using LLM (Groq with Llama-4-Scout) to extract summaries,
descriptions, and relevant information from student submission images.
"""

import os
from typing import Dict, Any
from .base import BaseParser
from .llm_service import get_llm_service, BaseLLMService


class ImageOCRParser(BaseParser):
    """
    OCR and analysis of image files using LLM.
    
    Extracts text and summaries from images using Groq API.
    Maintains image metadata for reference.
    """

    SUPPORTED_EXTENSIONS = {'.png', '.jpg', '.jpeg'}
    
    DEFAULT_ANALYSIS_PROMPT = (
        "Analyze this image from a student submission. "
        "Provide a clear, concise summary of what the image contains, "
        "including any text, diagrams, charts, or relevant content. "
        "If the image contains code, data, or technical content, extract it verbatim."
    )

    def __init__(self, llm_service: BaseLLMService = None, analysis_prompt: str = None):
        """
        Initialize ImageOCRParser with optional custom LLM service.

        Args:
            llm_service: Optional LLM service instance. If None, uses Groq default.
            analysis_prompt: Optional custom prompt for image analysis.
        """
        self._llm_service = llm_service
        self.analysis_prompt = analysis_prompt or self.DEFAULT_ANALYSIS_PROMPT

    @property
    def llm_service(self) -> BaseLLMService:
        """Get or create LLM service lazily."""
        if self._llm_service is None:
            self._llm_service = get_llm_service()
        return self._llm_service

    def parse(self, file_path: str) -> Dict[str, Any]:
        """
        Parse an image file using LLM analysis.

        Args:
            file_path: Path to the image file.

        Returns:
            Dictionary with parser_type, LLM-extracted content, and metadata.

        Raises:
            FileNotFoundError: If file does not exist.
            ValueError: If file format is not supported.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        file_ext = os.path.splitext(file_path)[1].lower()
        if file_ext not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported image format: {file_ext}. "
                f"Supported: {self.SUPPORTED_EXTENSIONS}"
            )

        file_name = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)

        # Extract image metadata
        image_metadata = self._get_image_metadata(file_path)

        # Get LLM analysis
        llm_result = self._analyze_with_llm(file_path)

        return {
            "parser_type": "image_ocr",
            "content": {
                "extracted_text": llm_result.get("content"),
                "llm_model": llm_result.get("model_used"),
                "llm_success": llm_result.get("success"),
                "llm_error": llm_result.get("error"),
                "status": "success" if llm_result.get("success") else "llm_analysis_failed"
            },
            "meta": {
                "file_name": file_name,
                "file_size": file_size,
                "file_format": file_ext,
                "image_metadata": image_metadata
            }
        }

    def _analyze_with_llm(self, file_path: str) -> Dict[str, Any]:
        """
        Send image to LLM for analysis.

        Args:
            file_path: Path to the image file.

        Returns:
            Dictionary with LLM analysis results.
        """
        try:
            # Get or initialize LLM service
            if not self.llm_service:
                self.llm_service = get_llm_service("groq")
            
            # Send to LLM
            result = self.llm_service.analyze_image(file_path, self.analysis_prompt)
            return result

        except Exception as e:
            error_msg = str(e)
            import traceback
            error_traceback = traceback.format_exc()
            
            return {
                "success": False,
                "content": None,
                "model_used": None,
                "error": f"Failed to initialize/use LLM service: {error_msg}",
                "error_traceback": error_traceback
            }

    def _get_image_metadata(self, file_path: str) -> Dict[str, Any]:
        """
        Extract basic image metadata (dimensions, etc.).
        
        Placeholder implementation - returns minimal metadata.
        """
        try:
            from PIL import Image
            
            try:
                img = Image.open(file_path)
                return {
                    "width": img.width,
                    "height": img.height,
                    "format": img.format,
                    "mode": img.mode  # Color mode (RGB, RGBA, etc.)
                }
            except Exception:
                # If PIL fails, return empty metadata
                return {"status": "unable_to_read_metadata"}
        except ImportError:
            return {"status": "PIL_not_installed"}
