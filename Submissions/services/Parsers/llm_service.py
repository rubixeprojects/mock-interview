"""
LLM Service Interface and Implementations for analyzing submission content.

This module provides a configurable interface for sending content to various LLMs
for analysis, summarization, and extraction tasks. Supports multi-key management
with automatic fallback on rate limits.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
import base64
import os
import logging
import time

logger = logging.getLogger(__name__)


class BaseLLMService(ABC):
    """
    Abstract base class for LLM services.
    """

    @abstractmethod
    def analyze_image(self, image_path: str, prompt: str) -> Dict[str, Any]:
        """
        Send an image to the LLM for analysis.

        Args:
            image_path: Path to the image file.
            prompt: The prompt/instruction for the LLM.

        Returns:
            Dictionary with:
            {
                "success": bool,
                "content": str,  # LLM response
                "model_used": str,
                "error": str | None
            }
        """
        pass

    @abstractmethod
    def analyze_text(self, text: str, prompt: str) -> Dict[str, Any]:
        """
        Send text to the LLM for analysis.

        Args:
            text: The text content to analyze.
            prompt: The prompt/instruction for the LLM.

        Returns:
            Dictionary with success status, content, and model info.
        """
        pass


class GroqLLMService(BaseLLMService):
    """
    LLM service using Groq API with multi-key support and automatic fallback.
    Uses meta-llama/llama-4-scout-17b-16e-instruct model.
    """

    def __init__(self, api_keys: Optional[List[str]] = None):
        """
        Initialize Groq LLM service with multiple API keys.

        Args:
            api_keys: List of Groq API keys. If None, reads from GROQ_API_KEY1, GROQ_API_KEY2 env variables.
        """
        if api_keys:
            self.api_keys = api_keys
        else:
            # Load from environment
            self.api_keys = []
            for i in range(1, 3):  # GROQ_API_KEY1, GROQ_API_KEY2
                key = os.getenv(f"GROQ_API_KEY{i}")
                if key:
                    self.api_keys.append(key)
        
        if not self.api_keys:
            raise ValueError(
                "No Groq API keys provided. Set GROQ_API_KEY1 and/or GROQ_API_KEY2 "
                "environment variables or pass api_keys parameter."
            )
        
        self.model = "meta-llama/llama-4-scout-17b-16e-instruct"
        self.current_key_index = 0
        self.clients = {}  # Cache clients by key index
        self._initialize_clients()

    def _initialize_clients(self):
        """Initialize Groq clients for all keys."""
        try:
            from groq import Groq
            for i, key in enumerate(self.api_keys):
                self.clients[i] = Groq(api_key=key)
        except ImportError:
            raise ImportError(
                "groq package not installed. Install with: pip install groq"
            )

    def _get_client(self, key_index: int):
        """Get client for a specific key index."""
        if key_index >= len(self.api_keys):
            return None
        return self.clients.get(key_index)

    def _is_rate_limit_error(self, error: Exception) -> bool:
        """Check if error is a rate limit error (429)."""
        error_str = str(error)
        return "429" in error_str or "rate" in error_str.lower()

    def analyze_image(self, image_path: str, prompt: str) -> Dict[str, Any]:
        """
        Send an image to Groq/Llama for analysis using vision capabilities.
        Automatically rotates keys on rate limit.

        Args:
            image_path: Path to the image file.
            prompt: The prompt/instruction for the LLM.

        Returns:
            Dictionary with analysis results.
        """
        if not os.path.exists(image_path):
            return {
                "success": False,
                "content": None,
                "model_used": self.model,
                "error": f"Image file not found: {image_path}"
            }

        for attempt in range(len(self.api_keys)):
            try:
                # Encode image to base64
                with open(image_path, "rb") as image_file:
                    base64_image = base64.b64encode(image_file.read()).decode('utf-8')

                # Determine media type from file extension
                file_ext = os.path.splitext(image_path)[1].lower()
                media_type_map = {
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".png": "image/png",
                    ".gif": "image/gif",
                    ".webp": "image/webp"
                }
                media_type = media_type_map.get(file_ext, "image/jpeg")

                # Get current client
                client = self._get_client(self.current_key_index)
                if not client:
                    return {
                        "success": False,
                        "content": None,
                        "model_used": self.model,
                        "error": "No valid Groq API keys available"
                    }

                # Call Groq API with vision capability
                message = client.chat.completions.create(
                    model=self.model,
                    max_tokens=1024,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": prompt
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{media_type};base64,{base64_image}"
                                    }
                                }
                            ]
                        }
                    ]
                )

                return {
                    "success": True,
                    "content": message.choices[0].message.content,
                    "model_used": self.model,
                    "error": None
                }

            except Exception as e:
                error_str = str(e)
                is_rate_limit = self._is_rate_limit_error(e)
                
                logger.warning(
                    f"Groq image analysis failed (attempt {attempt+1}/{len(self.api_keys)}): {error_str}"
                )
                
                # Move to next key on any error (including rate limit)
                if attempt < len(self.api_keys) - 1:
                    self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
                    logger.info(f"Rotating to Groq key index {self.current_key_index}")
                    continue
                else:
                    # All keys exhausted
                    import traceback
                    return {
                        "success": False,
                        "content": None,
                        "model_used": self.model,
                        "error": f"Groq image analysis failed after {len(self.api_keys)} attempts: {error_str}"
                    }

    def analyze_text(self, text: str, prompt: str) -> Dict[str, Any]:
        """
        Send text to Groq/Llama for analysis.
        Automatically rotates keys on rate limit.

        Args:
            text: The text content to analyze.
            prompt: The prompt/instruction for the LLM.

        Returns:
            Dictionary with analysis results.
        """
        for attempt in range(len(self.api_keys)):
            try:
                # Get current client
                client = self._get_client(self.current_key_index)
                if not client:
                    return {
                        "success": False,
                        "content": None,
                        "model_used": self.model,
                        "error": "No valid Groq API keys available"
                    }

                message = client.chat.completions.create(
                    model=self.model,
                    max_tokens=1024,
                    messages=[
                        {
                            "role": "user",
                            "content": f"{prompt}\n\nContent:\n{text}"
                        }
                    ]
                )

                return {
                    "success": True,
                    "content": message.choices[0].message.content,
                    "model_used": self.model,
                    "error": None
                }

            except Exception as e:
                error_str = str(e)
                is_rate_limit = self._is_rate_limit_error(e)
                
                logger.warning(
                    f"Groq text analysis failed (attempt {attempt+1}/{len(self.api_keys)}): {error_str}"
                )
                
                # Move to next key on any error
                if attempt < len(self.api_keys) - 1:
                    self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
                    logger.info(f"Rotating to Groq key index {self.current_key_index}")
                    continue
                else:
                    # All keys exhausted
                    import traceback
                    return {
                        "success": False,
                        "content": None,
                        "model_used": self.model,
                        "error": f"Groq text analysis failed after {len(self.api_keys)} attempts: {error_str}"
                    }


# Global LLM service instances
_groq_service: Optional[GroqLLMService] = None
_gemini_service: Optional['GeminiLLMService'] = None


class GeminiLLMService(BaseLLMService):
    """
    LLM service using Google Gemini API with multi-key and multi-model support.
    Falls back to Groq if all Gemini attempts fail.
    
    Models: gemini-2.5-flash-preview-09-2025, gemini-2.5-flash-lite
    Keys: GEMINI_API_KEY1, GEMINI_API_KEY2
    """

    def __init__(self, api_keys: Optional[List[str]] = None, groq_service: Optional[GroqLLMService] = None):
        """
        Initialize Gemini LLM service with multi-key support and Groq fallback.

        Args:
            api_keys: List of Gemini API keys. If None, reads from GEMINI_API_KEY1, GEMINI_API_KEY2.
            groq_service: Groq service for fallback. If None, creates one.
        """
        if api_keys:
            self.api_keys = api_keys
        else:
            # Load from environment
            self.api_keys = []
            for i in range(1, 3):  # GEMINI_API_KEY1, GEMINI_API_KEY2
                key = os.getenv(f"GEMINI_API_KEY{i}")
                if key:
                    self.api_keys.append(key)
        
        if not self.api_keys:
            raise ValueError(
                "No Gemini API keys provided. Set GEMINI_API_KEY1 and/or GEMINI_API_KEY2 "
                "environment variables or pass api_keys parameter."
            )
        
        self.models = [
            "gemini-2.5-flash-preview-09-2025",
            "gemini-2.5-flash-lite"
        ]
        self.current_key_index = 0
        self.current_model_index = 0
        self.groq_fallback = groq_service
        self.clients = {}  # Cache clients by key index
        self._initialize_clients()

    def _initialize_clients(self):
        """Initialize Gemini clients for all keys."""
        try:
            import google.generativeai as genai
            for i, key in enumerate(self.api_keys):
                genai.configure(api_key=key)
                self.clients[i] = genai  # Store the module with the key configured
        except ImportError:
            raise ImportError(
                "google-generativeai package not installed. Install with: pip install google-generativeai"
            )

    def _get_client(self, key_index: int):
        """Get configured client for a specific key index."""
        if key_index >= len(self.api_keys):
            return None
        
        try:
            import google.generativeai as genai
            genai.configure(api_key=self.api_keys[key_index])
            return genai
        except:
            return None

    def _is_rate_limit_error(self, error: Exception) -> bool:
        """Check if error is a rate limit error (429)."""
        error_str = str(error)
        return "429" in error_str or "rate" in error_str.lower() or "RESOURCE_EXHAUSTED" in error_str

    def analyze_image(self, image_path: str, prompt: str) -> Dict[str, Any]:
        """
        Send an image to Gemini for analysis with fallback to Groq.

        Args:
            image_path: Path to the image file.
            prompt: The prompt/instruction for the LLM.

        Returns:
            Dictionary with analysis results.
        """
        if not os.path.exists(image_path):
            return {
                "success": False,
                "content": None,
                "model_used": None,
                "error": f"Image file not found: {image_path}"
            }

        # Try Gemini first with all key/model combinations
        for key_attempt in range(len(self.api_keys)):
            for model_attempt in range(len(self.models)):
                try:
                    genai = self._get_client(self.current_key_index)
                    if not genai:
                        continue

                    model = genai.GenerativeModel(self.models[self.current_model_index])
                    
                    with open(image_path, "rb") as image_file:
                        image_data = image_file.read()
                    
                    response = model.generate_content([prompt, {"mime_type": "image/jpeg", "data": image_data}])
                    
                    return {
                        "success": True,
                        "content": response.text,
                        "model_used": f"gemini:{self.models[self.current_model_index]}",
                        "error": None
                    }

                except Exception as e:
                    error_str = str(e)
                    logger.warning(
                        f"Gemini image analysis failed (key:{self.current_key_index}, "
                        f"model:{self.current_model_index}): {error_str}"
                    )
                    
                    # Move to next model
                    if model_attempt < len(self.models) - 1:
                        self.current_model_index = (self.current_model_index + 1) % len(self.models)
                        continue
                    else:
                        # Move to next key
                        if key_attempt < len(self.api_keys) - 1:
                            self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
                            self.current_model_index = 0
                            logger.info(f"Rotating to Gemini key {self.current_key_index}, model 0")
                        break

        # Fall back to Groq if all Gemini attempts fail
        logger.info("All Gemini attempts failed, falling back to Groq")
        if self.groq_fallback:
            return self.groq_fallback.analyze_image(image_path, prompt)
        else:
            return {
                "success": False,
                "content": None,
                "model_used": None,
                "error": "All Gemini attempts failed and no Groq fallback available"
            }

    def analyze_text(self, text: str, prompt: str) -> Dict[str, Any]:
        """
        Send text to Gemini for analysis with fallback to Groq.
        Cascade: Gemini Key1 Model1 → Model2 → Key2 Model1 → Model2 → Groq

        Args:
            text: The text content to analyze.
            prompt: The prompt/instruction for the LLM.

        Returns:
            Dictionary with analysis results.
        """
        # Try Gemini first with all key/model combinations
        for key_attempt in range(len(self.api_keys)):
            for model_attempt in range(len(self.models)):
                try:
                    genai = self._get_client(self.current_key_index)
                    if not genai:
                        continue

                    model = genai.GenerativeModel(self.models[self.current_model_index])
                    response = model.generate_content(f"{prompt}\n\nContent:\n{text}")
                    
                    return {
                        "success": True,
                        "content": response.text,
                        "model_used": f"gemini:{self.models[self.current_model_index]}",
                        "error": None
                    }

                except Exception as e:
                    error_str = str(e)
                    logger.warning(
                        f"Gemini text analysis failed (key:{self.current_key_index}, "
                        f"model:{self.current_model_index}): {error_str}"
                    )
                    
                    # Move to next model
                    if model_attempt < len(self.models) - 1:
                        self.current_model_index = (self.current_model_index + 1) % len(self.models)
                        continue
                    else:
                        # Move to next key
                        if key_attempt < len(self.api_keys) - 1:
                            self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
                            self.current_model_index = 0
                            logger.info(f"Rotating to Gemini key {self.current_key_index}, model 0")
                        break

        # Fall back to Groq if all Gemini attempts fail
        logger.info("All Gemini attempts failed, falling back to Groq")
        if self.groq_fallback:
            return self.groq_fallback.analyze_text(text, prompt)
        else:
            return {
                "success": False,
                "content": None,
                "model_used": None,
                "error": "All Gemini attempts failed and no Groq fallback available"
            }


def get_llm_service(service_type: str = "groq", **kwargs) -> BaseLLMService:
    """
    Get or create an LLM service instance.

    Args:
        service_type: Type of LLM service ("groq" or "gemini")
        **kwargs: Additional arguments for the service (e.g., api_keys)

    Returns:
        LLM service instance.
    """
    global _groq_service, _gemini_service

    if service_type == "groq":
        if not _groq_service:
            _groq_service = GroqLLMService(**kwargs)
        return _groq_service
    
    elif service_type == "gemini":
        if not _gemini_service:
            # Initialize Groq as fallback for Gemini
            groq_fallback = get_llm_service("groq")
            _gemini_service = GeminiLLMService(groq_service=groq_fallback, **kwargs)
        return _gemini_service
    
    else:
        raise ValueError(f"Unsupported LLM service type: {service_type}")


def set_llm_service(service: BaseLLMService, service_type: str = "groq"):
    """
    Manually set a global LLM service instance.

    Args:
        service: LLM service instance to use globally.
        service_type: Type of service ("groq" or "gemini")
    """
    global _groq_service, _gemini_service
    
    if service_type == "groq":
        _groq_service = service
    elif service_type == "gemini":
        _gemini_service = service
    else:
        raise ValueError(f"Unsupported service type: {service_type}")
