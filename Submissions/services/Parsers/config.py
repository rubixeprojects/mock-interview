"""
Configuration module for parser settings, including LLM service configuration.

This module centralizes configuration for all parsers, making it easy to
switch LLM providers, customize prompts, and manage API keys.
"""

import os
from typing import Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class LLMConfig:
    """Configuration for LLM services."""
    
    # Which LLM service to use ("groq", "openai", etc.)
    service_type: str = "groq"
    
    # API key (if None, will look for env variable)
    api_key: Optional[str] = None
    
    # Custom analysis prompts per parser
    image_analysis_prompt: Optional[str] = None
    text_analysis_prompt: Optional[str] = None
    code_analysis_prompt: Optional[str] = None
    
    # Groq-specific settings
    groq_model: str = "meta-llama/llama-4-scout-17b-16e-instruct"
    groq_max_tokens: int = 1024
    
    # OpenAI-specific settings (for future use)
    openai_model: str = "gpt-4-vision"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary."""
        return {
            "service_type": self.service_type,
            "api_key": self.api_key,
            "image_analysis_prompt": self.image_analysis_prompt,
            "text_analysis_prompt": self.text_analysis_prompt,
            "code_analysis_prompt": self.code_analysis_prompt,
            "groq_model": self.groq_model,
            "groq_max_tokens": self.groq_max_tokens,
            "openai_model": self.openai_model,
        }

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """
        Create config from environment variables.
        
        Expected env vars:
        - PARSER_LLM_SERVICE: LLM service type (default: groq)
        - GROQ_API_KEY or OPENAI_API_KEY: API key for the service
        - PARSER_IMAGE_PROMPT: Custom image analysis prompt
        """
        return cls(
            service_type=os.getenv("PARSER_LLM_SERVICE", "groq"),
            api_key=os.getenv("GROQ_API_KEY") or os.getenv("OPENAI_API_KEY"),
            image_analysis_prompt=os.getenv("PARSER_IMAGE_PROMPT"),
        )

    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> "LLMConfig":
        """Create config from dictionary."""
        return cls(**config_dict)


@dataclass
class ParserConfig:
    """Overall parser configuration."""
    
    # LLM settings
    llm: LLMConfig = None
    
    # Parser-specific settings
    skip_unsupported_files: bool = True
    max_file_size_mb: int = 500
    timeout_seconds: int = 300
    
    def __post_init__(self):
        if self.llm is None:
            self.llm = LLMConfig.from_env()

    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary."""
        return {
            "llm": self.llm.to_dict() if self.llm else None,
            "skip_unsupported_files": self.skip_unsupported_files,
            "max_file_size_mb": self.max_file_size_mb,
            "timeout_seconds": self.timeout_seconds,
        }


# Global parser configuration instance
_parser_config: Optional[ParserConfig] = None


def get_parser_config() -> ParserConfig:
    """Get the global parser configuration."""
    global _parser_config
    if _parser_config is None:
        _parser_config = ParserConfig()
    return _parser_config


def set_parser_config(config: ParserConfig):
    """Set the global parser configuration."""
    global _parser_config
    _parser_config = config


def load_config_from_file(config_file: str) -> ParserConfig:
    """
    Load parser configuration from a JSON file.

    Args:
        config_file: Path to JSON configuration file.

    Returns:
        ParserConfig instance loaded from file.
    """
    import json
    
    if not os.path.exists(config_file):
        raise FileNotFoundError(f"Config file not found: {config_file}")
    
    with open(config_file, "r") as f:
        config_dict = json.load(f)
    
    # Handle nested LLMConfig
    if "llm" in config_dict and isinstance(config_dict["llm"], dict):
        config_dict["llm"] = LLMConfig.from_dict(config_dict["llm"])
    
    return ParserConfig.from_dict(config_dict)


def save_config_to_file(config: ParserConfig, config_file: str):
    """
    Save parser configuration to a JSON file.

    Args:
        config: ParserConfig instance to save.
        config_file: Path to save JSON configuration file.
    """
    import json
    
    with open(config_file, "w") as f:
        json.dump(config.to_dict(), f, indent=2)
