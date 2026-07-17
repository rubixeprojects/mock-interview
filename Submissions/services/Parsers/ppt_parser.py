"""
Parser for PowerPoint files: .pptx

Extracts slide content, images, and text while filtering out decorative images
(logos, backgrounds, etc.) using multiple heuristics.
"""

import os
import hashlib
import tempfile
import zipfile
from typing import Dict, Any, List, Tuple
from pathlib import Path
from .base import BaseParser
from .llm_service import get_llm_service, BaseLLMService
from .image_ocr import ImageOCRParser
from pptx.enum.shapes import MSO_SHAPE_TYPE


class PPTParser(BaseParser):
    """
    Parser for .pptx files with slide-wise structure and intelligent image filtering.
    
    Features:
    - Extracts text from each slide
    - Extracts images from slides with metadata
    - Filters decorative images using multiple heuristics
    - Preserves slide structure
    - Assigns confidence scores to images
    """

    SUPPORTED_EXTENSIONS = {'.pptx'}
    
    # Image filtering thresholds
    MIN_IMAGE_SIZE = 50  # pixels
    MAX_IMAGE_SIZE_RATIO = 0.9  # 90% of slide area
    MIN_COLORS_FOR_CONTENT = 5  # Decorative images often have < 5 unique colors
    MIN_ENTROPY = 1.5  # Low entropy = likely decorative
    ASPECT_RATIO_BOUNDS = (0.3, 3.0)  # Extreme ratios suggest decorative
    REPETITION_THRESHOLD = 3  # Same image on 3+ slides = likely decorative

    def __init__(self, llm_service: BaseLLMService = None):
        """
        Initialize PPT parser.

        Args:
            llm_service: Optional LLM service for image analysis.
        """
        self._llm_service = llm_service
        self._image_ocr_parser = None
        self._image_hashes = {}  # Track image hashes across slides

    @property
    def llm_service(self) -> BaseLLMService:
        """Get or create LLM service lazily."""
        if self._llm_service is None:
            self._llm_service = get_llm_service()
        return self._llm_service

    @property
    def image_ocr_parser(self) -> 'ImageOCRParser':
        """Get or create ImageOCRParser lazily."""
        if self._image_ocr_parser is None:
            self._image_ocr_parser = ImageOCRParser(llm_service=self.llm_service)
        return self._image_ocr_parser

    def parse(self, file_path: str) -> Dict[str, Any]:
        """
        Parse a .pptx file with slide-wise structure.

        Args:
            file_path: Path to the .pptx file.

        Returns:
            Dictionary with slides, images, and metadata.

        Raises:
            FileNotFoundError: If file does not exist.
            ValueError: If file format is not supported.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        file_ext = os.path.splitext(file_path)[1].lower()
        if file_ext not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported format: {file_ext}. "
                f"Supported: {self.SUPPORTED_EXTENSIONS}"
            )

        try:
            from pptx import Presentation
        except ImportError:
            raise ImportError(
                "python-pptx not installed. Install with: pip install python-pptx"
            )

        file_name = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)

        # Load presentation with error recovery for corrupted files
        try:
            prs = Presentation(file_path)
        except zipfile.BadZipFile as e:
            # File may be corrupted but still partially readable
            # Try to work with what we can extract
            return {
                "parser_type": "pptx",
                "content": {
                    "slides": [],
                    "total_slides": 0,
                    "status": "corrupted_file"
                },
                "meta": {
                    "file_name": file_name,
                    "file_size": file_size,
                    "file_format": ".pptx",
                    "error": f"File appears to be corrupted: {str(e)}"
                }
            }
        except Exception as e:
            raise ValueError(f"Cannot parse PPTX file: {str(e)}")

        slide_width = prs.slide_width
        slide_height = prs.slide_height
        slide_area = (slide_width * slide_height) / (914400 ** 2)  # Convert EMUs to square inches

        # Extract media files from PPTX ZIP
        media_files = self._extract_media_from_zip(file_path)

        # First pass: collect all images and their hashes
        self._collect_image_hashes(prs, media_files)

        # Second pass: extract slides with filtered images
        slides_data = []
        for slide_idx, slide in enumerate(prs.slides):
            slide_data = self._extract_slide(
                slide, slide_idx, slide_width, slide_height, slide_area, media_files
            )
            slides_data.append(slide_data)

        return {
            "parser_type": "pptx",
            "content": {
                "slides": slides_data,
                "total_slides": len(slides_data),
                "status": "success"
            },
            "meta": {
                "file_name": file_name,
                "file_size": file_size,
                "file_format": file_ext,
                "slide_count": len(prs.slides),
                "total_media_files": len(media_files)
            }
        }

    def _extract_media_from_zip(self, file_path: str) -> Dict[str, str]:
        """
        Extract all media files from the PPTX ZIP to temporary directory.

        Args:
            file_path: Path to the .pptx file.

        Returns:
            Dictionary mapping media filenames to their extracted paths.
        """
        media_files = {}
        temp_dir = tempfile.gettempdir()
        
        try:
            with zipfile.ZipFile(file_path, 'r') as z:
                # Extract all media files
                for file_info in z.filelist:
                    if 'ppt/media/' in file_info.filename:
                        # Extract to temp directory
                        extract_path = z.extract(file_info, temp_dir)
                        media_id = os.path.basename(file_info.filename)
                        media_files[media_id] = extract_path
        except Exception as e:
            # If extraction fails, just continue with empty media_files
            pass
        
        return media_files

    def _collect_image_hashes(self, prs, media_files: Dict[str, str]) -> None:
        """
        First pass: collect hashes of all images to detect repetition.

        Args:
            prs: Presentation object.
            media_files: Dictionary mapping media IDs to file paths.
        """
        self._image_hashes = {}  # Maps hash -> list of occurrences
        self._media_id_hashes = {}  # Maps media_id -> hash (for quick lookup)
        
        # Hash all extracted media files
        for media_id, media_path in media_files.items():
            try:
                with open(media_path, 'rb') as f:
                    image_hash = hashlib.md5(f.read()).hexdigest()
                
                self._media_id_hashes[media_id] = image_hash
                
                if image_hash not in self._image_hashes:
                    self._image_hashes[image_hash] = []
                self._image_hashes[image_hash].append(media_id)
            except Exception:
                pass

    def _extract_slide(self, slide, slide_idx: int, slide_width: int, slide_height: int, slide_area: float, media_files: Dict[str, str]) -> Dict[str, Any]:
        """
        Extract content from a single slide.

        Args:
            slide: Slide object from python-pptx.
            slide_idx: Index of the slide (0-based).
            slide_width: Width of slide in EMUs.
            slide_height: Height of slide in EMUs.
            slide_area: Area of slide in square inches.
            media_files: Dictionary mapping media IDs to file paths.

        Returns:
            Dictionary with slide content and filtered images.
        """
        slide_data = {
            "slide_number": slide_idx + 1,
            "text_content": "",
            "shapes": [],
            "images": []
        }

        # Extract text
        text_parts = []
        images_data = []

        for shape in slide.shapes:
            if hasattr(shape, "text"):
                text = shape.text.strip()
                if text:
                    text_parts.append(text)

            # Extract images from shape objects
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                image_info = self._extract_image_info(
                    shape, slide_idx, slide_width, slide_height, slide_area
                )
                if image_info:
                    images_data.append(image_info)

        # Also add media files from ZIP (handles embedded images not exposed by python-pptx)
        # For simplicity, we'll add all media files to each slide as they appear to be
        # embedded throughout the presentation
        for media_id, media_path in media_files.items():
            # Skip non-image formats
            if media_id.lower().endswith(('.svg', '.wdp')):
                continue
            
            try:
                image_info = self._extract_media_info(
                    media_id, media_path, slide_idx, slide_width, slide_height, slide_area
                )
                if image_info:
                    images_data.append(image_info)
            except Exception:
                pass

        slide_data["text_content"] = "\n".join(text_parts)
        
        # Filter images
        filtered_images = self._filter_images(images_data, slide_idx)
        slide_data["images"] = filtered_images

        return slide_data

    def _extract_image_info(self, shape, slide_idx: int, slide_width: int, slide_height: int, slide_area: float) -> Dict[str, Any]:
        """
        Extract image information and metadata.

        Args:
            shape: Picture shape from python-pptx.
            slide_idx: Index of the slide.
            slide_width: Width of slide in EMUs.
            slide_height: Height of slide in EMUs.
            slide_area: Area of slide in square inches.

        Returns:
            Dictionary with image info or None if extraction fails.
        """
        try:
            # Get image bytes
            image_bytes = shape.image.blob
            image_hash = hashlib.md5(image_bytes).hexdigest()

            # Save temporarily
            temp_dir = tempfile.gettempdir()
            image_path = os.path.join(temp_dir, f"ppt_img_{slide_idx}_{image_hash[:8]}.png")
            
            # Only save if not already saved
            if not os.path.exists(image_path):
                with open(image_path, "wb") as f:
                    f.write(image_bytes)

            # Get image dimensions
            img_width = shape.width / 914400  # Convert EMUs to inches
            img_height = shape.height / 914400
            img_area_ratio = (img_width * img_height) / slide_area if slide_area > 0 else 0
            aspect_ratio = img_width / img_height if img_height > 0 else 0

            # Get position (normalized to 0-1)
            left_norm = (shape.left / slide_width) if slide_width > 0 else 0
            top_norm = (shape.top / slide_height) if slide_height > 0 else 0

            return {
                "image_id": f"slide_{slide_idx + 1}_img_{image_hash[:8]}",
                "image_path": image_path,
                "image_hash": image_hash,
                "position": {
                    "left_ratio": float(left_norm),
                    "top_ratio": float(top_norm)
                },
                "size": {
                    "width_inches": float(img_width),
                    "height_inches": float(img_height),
                    "area_ratio": float(img_area_ratio),
                    "aspect_ratio": float(aspect_ratio)
                }
            }

        except Exception as e:
            # Skip images that can't be processed
            return None

    def _extract_media_info(self, media_id: str, media_path: str, slide_idx: int, slide_width: int, slide_height: int, slide_area: float) -> Dict[str, Any]:
        """
        Extract info from media file (from ZIP).

        Args:
            media_id: Media filename (e.g., "image2.png").
            media_path: Full path to the extracted media file.
            slide_idx: Index of the slide.
            slide_width: Width of slide in EMUs.
            slide_height: Height of slide in EMUs.
            slide_area: Area of slide in square inches.

        Returns:
            Dictionary with media info or None if extraction fails.
        """
        try:
            # Read media file and compute hash
            with open(media_path, 'rb') as f:
                media_bytes = f.read()
            
            media_hash = hashlib.md5(media_bytes).hexdigest()
            
            # Get media dimensions using PIL
            from PIL import Image
            pil_img = Image.open(media_path)
            img_width = pil_img.width / 96.0  # Convert pixels to inches (96 DPI standard)
            img_height = pil_img.height / 96.0
            
            img_area_ratio = (img_width * img_height) / slide_area if slide_area > 0 else 0
            aspect_ratio = img_width / img_height if img_height > 0 else 1.0
            
            # Assume media files are centered or full-width (conservative estimate)
            left_norm = 0.0  # Unknown position from ZIP extraction
            top_norm = 0.0
            
            return {
                "image_id": f"slide_{slide_idx + 1}_{media_id.split('.')[0]}",
                "image_path": media_path,
                "image_hash": media_hash,
                "position": {
                    "left_ratio": float(left_norm),
                    "top_ratio": float(top_norm)
                },
                "size": {
                    "width_inches": float(img_width),
                    "height_inches": float(img_height),
                    "area_ratio": float(img_area_ratio),
                    "aspect_ratio": float(aspect_ratio)
                }
            }
        
        except Exception as e:
            return None

    def _hash_image(self, shape) -> str:
        """Get MD5 hash of image for deduplication."""
        try:
            image_bytes = shape.image.blob
            return hashlib.md5(image_bytes).hexdigest()
        except:
            return None

    def _filter_images(self, images: List[Dict[str, Any]], slide_idx: int) -> List[Dict[str, Any]]:
        """
        Filter images using multiple heuristics and assign confidence scores.
        Uses ImageOCRParser (Groq LLM) for advanced content analysis.

        Args:
            images: List of image info dictionaries.
            slide_idx: Current slide index.

        Returns:
            List of images that passed filters with confidence scores and LLM analysis.
        """
        filtered = []

        for img in images:
            confidence = 1.0
            reasons = []
            llm_analysis = None

            # 1. Size filtering
            size_info = img["size"]
            if (size_info["width_inches"] < self.MIN_IMAGE_SIZE / 96 or
                size_info["height_inches"] < self.MIN_IMAGE_SIZE / 96):
                confidence *= 0.3
                reasons.append("too_small")

            if size_info["area_ratio"] > self.MAX_IMAGE_SIZE_RATIO:
                confidence *= 0.4
                reasons.append("too_large_area")

            # 2. Aspect ratio check
            aspect = size_info["aspect_ratio"]
            if aspect < self.ASPECT_RATIO_BOUNDS[0] or aspect > self.ASPECT_RATIO_BOUNDS[1]:
                confidence *= 0.5
                reasons.append("extreme_aspect_ratio")

            # 3. Position check (full-width top/bottom = decorative)
            pos = img["position"]
            is_full_width = pos["left_ratio"] < 0.1 and (
                img["size"]["width_inches"] > 8  # ~90% of typical slide
            )
            is_top_edge = pos["top_ratio"] < 0.05
            is_bottom_edge = pos["top_ratio"] > 0.85

            if (is_full_width and (is_top_edge or is_bottom_edge)):
                confidence *= 0.3
                reasons.append("header_footer_position")

            # 4. Repetition check (appears on 3+ slides = logo/watermark)
            image_hash = img["image_hash"]
            if image_hash in self._image_hashes:
                occurrence_count = len(self._image_hashes[image_hash])
                if occurrence_count >= self.REPETITION_THRESHOLD:
                    confidence *= 0.2
                    reasons.append(f"repeated_on_{occurrence_count}_slides")

            # 5. Color palette analysis
            try:
                from PIL import Image
                pil_img = Image.open(img["image_path"])
                unique_colors = len(set(pil_img.getdata()))
                
                if unique_colors < self.MIN_COLORS_FOR_CONTENT:
                    confidence *= 0.4
                    reasons.append("few_colors")

                # 6. Entropy check
                entropy = self._calculate_entropy(pil_img)
                if entropy < self.MIN_ENTROPY:
                    confidence *= 0.3
                    reasons.append("low_entropy")

            except Exception:
                # If PIL analysis fails, still include image but lower confidence
                confidence *= 0.7
                reasons.append("could_not_analyze_visually")

            # 7. LLM-based content analysis (if confidence is reasonable enough to analyze)
            if confidence > 0.3:
                try:
                    llm_result = self.image_ocr_parser.parse(img["image_path"])
                    if llm_result.get("content"):
                        img_content = llm_result["content"].get("extracted_text", "")
                        
                        # Analyze LLM response to determine if it's decorative
                        is_decorative = self._is_decorative_content(img_content)
                        
                        if is_decorative:
                            confidence *= 0.25
                            reasons.append("llm_identified_as_decorative")
                        
                        llm_analysis = {
                            "extracted_text": img_content,
                            "model": llm_result["content"].get("llm_model"),
                            "is_decorative": is_decorative
                        }
                except Exception as e:
                    # LLM analysis failed, continue with heuristic filtering
                    pass

            # Add analysis results
            img["confidence_score"] = float(max(0, min(1, confidence)))  # Clamp to 0-1
            img["filter_reasons"] = reasons if reasons else ["passed_all_checks"]
            
            if llm_analysis:
                img["llm_analysis"] = llm_analysis

            # Include if confidence > threshold (0.5)
            if confidence > 0.5:
                filtered.append(img)

        return filtered

    def _is_decorative_content(self, llm_text: str) -> bool:
        """
        Determine if LLM-extracted text indicates decorative content (logo, watermark, etc).

        Args:
            llm_text: Text extracted from image by LLM.

        Returns:
            True if content appears decorative, False otherwise.
        """
        if not llm_text or not isinstance(llm_text, str):
            return True  # Empty or invalid = likely decorative
        
        text_lower = llm_text.lower()
        
        # Keywords indicating decorative content
        decorative_keywords = [
            'logo', 'watermark', 'copyright', '©', 'trademark', '™',
            'confidential', 'draft', 'proprietary', 'company name',
            'blank', 'empty slide', 'no content', 'title slide',
            'date:', 'time:', 'signature', 'page break'
        ]
        
        # Check for decorative indicators
        for keyword in decorative_keywords:
            if keyword in text_lower:
                return True
        
        # If text is very short, likely decorative
        if len(llm_text.strip()) < 5:
            return True
        
        return False

    def _calculate_entropy(self, pil_img) -> float:
        """
        Calculate image entropy (measure of visual complexity).
        Low entropy = simple/uniform = likely decorative.

        Args:
            pil_img: PIL Image object.

        Returns:
            Float entropy value.
        """
        try:
            import numpy as np
            
            # Convert to grayscale
            img_array = np.array(pil_img.convert('L'))
            
            # Calculate histogram
            hist, _ = np.histogram(img_array, bins=256, range=(0, 256))
            hist = hist / hist.sum()  # Normalize
            
            # Calculate entropy
            entropy = -np.sum(hist[hist > 0] * np.log2(hist[hist > 0]))
            return float(entropy)
        except Exception:
            # If numpy fails, return neutral value
            return self.MIN_ENTROPY + 1.0
