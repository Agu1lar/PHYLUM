# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Visual Perception Engine — screenshot capture, OCR and element detection.

Captures the current state of a screen, window or region via screenshot,
then extracts visual elements (text, controls, bounding boxes) using OCR
and optional image analysis.  Results are structured so that the grounding
layer can map them back to native UIA selectors.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageGrab
except ImportError:
    Image = None  # type: ignore[assignment,misc]
    ImageGrab = None  # type: ignore[assignment,misc]

try:
    import pytesseract
except ImportError:
    pytesseract = None  # type: ignore[assignment]

try:
    import win32gui
    import win32ui
    import win32con
    import win32api
except ImportError:
    win32gui = None  # type: ignore[assignment]
    win32ui = None  # type: ignore[assignment]
    win32con = None  # type: ignore[assignment]
    win32api = None  # type: ignore[assignment]


SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "agent_workspace" / "screenshots"


@dataclass
class VisualElement:
    """A text region or control detected in a screenshot."""
    text: str
    bbox: Tuple[int, int, int, int]  # (left, top, right, bottom)
    confidence: float = 0.0
    element_type: str = "text"
    center: Tuple[int, int] = (0, 0)
    source: str = "ocr"

    def __post_init__(self):
        if self.center == (0, 0) and self.bbox != (0, 0, 0, 0):
            l, t, r, b = self.bbox
            self.center = ((l + r) // 2, (t + b) // 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "bbox": {"left": self.bbox[0], "top": self.bbox[1], "right": self.bbox[2], "bottom": self.bbox[3]},
            "confidence": self.confidence,
            "element_type": self.element_type,
            "center": {"x": self.center[0], "y": self.center[1]},
            "source": self.source,
        }


@dataclass
class ScreenshotState:
    """Full visual state captured at a point in time."""
    image_path: str
    timestamp: float
    width: int
    height: int
    source: str  # "full_screen", "window", "region"
    hwnd: Optional[int] = None
    window_title: Optional[str] = None
    elements: List[VisualElement] = field(default_factory=list)
    image_hash: str = ""
    redacted: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "image_path": self.image_path,
            "timestamp": self.timestamp,
            "width": self.width,
            "height": self.height,
            "source": self.source,
            "hwnd": self.hwnd,
            "window_title": self.window_title,
            "elements": [e.to_dict() for e in self.elements],
            "image_hash": self.image_hash,
            "element_count": len(self.elements),
            "redacted": self.redacted,
        }


class VisualPerceptionEngine:
    """Captures screenshots and extracts visual elements via OCR."""

    def __init__(self, *, screenshot_dir: Optional[Path] = None, max_stored: int = 50):
        self.screenshot_dir = screenshot_dir or SCREENSHOT_DIR
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.max_stored = max_stored

    def _available(self) -> bool:
        return Image is not None and ImageGrab is not None

    def _ocr_available(self) -> bool:
        return pytesseract is not None

    async def capture_screen(self, *, region: Optional[Tuple[int, int, int, int]] = None) -> ScreenshotState:
        """Capture the full screen or a specific region."""
        if not self._available():
            raise RuntimeError("Pillow (PIL) is required for visual perception. Install it with: pip install Pillow")
        img = await asyncio.to_thread(ImageGrab.grab, bbox=region)
        source = "region" if region else "full_screen"
        return await self._process_image(img, source=source)

    async def capture_window(self, *, hwnd: Optional[int] = None, title: Optional[str] = None) -> ScreenshotState:
        """Capture a specific window by hwnd or title."""
        if not self._available():
            raise RuntimeError("Pillow (PIL) is required for visual perception")

        resolved_hwnd = hwnd
        window_title = title

        if resolved_hwnd is None and title and win32gui is not None:
            resolved_hwnd = await asyncio.to_thread(win32gui.FindWindow, None, title)
            if not resolved_hwnd:
                raise ValueError(f"Window not found: {title}")

        if resolved_hwnd and win32gui is not None:
            window_title = window_title or await asyncio.to_thread(win32gui.GetWindowText, resolved_hwnd)
            rect = await asyncio.to_thread(win32gui.GetWindowRect, resolved_hwnd)
            img = await asyncio.to_thread(ImageGrab.grab, bbox=rect)
        else:
            img = await asyncio.to_thread(ImageGrab.grab)

        return await self._process_image(
            img, source="window", hwnd=resolved_hwnd, window_title=window_title,
        )

    async def extract_elements(self, image: Any) -> List[VisualElement]:
        """Run OCR on a PIL Image and return detected visual elements."""
        if not self._ocr_available():
            return []
        try:
            data = await asyncio.to_thread(
                pytesseract.image_to_data, image, output_type=pytesseract.Output.DICT,
            )
        except Exception as exc:
            logger.warning("OCR extraction failed: %s", exc)
            return []

        elements: List[VisualElement] = []
        n = len(data.get("text", []))
        for i in range(n):
            text = (data["text"][i] or "").strip()
            if not text:
                continue
            conf = float(data["conf"][i]) if data["conf"][i] != -1 else 0.0
            if conf < 20:
                continue
            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            elements.append(VisualElement(
                text=text,
                bbox=(x, y, x + w, y + h),
                confidence=conf / 100.0,
                element_type="text",
                source="ocr",
            ))
        return elements

    async def detect_ui_patterns(self, elements: List[VisualElement]) -> Dict[str, List[VisualElement]]:
        """Classify detected elements into UI pattern categories."""
        patterns: Dict[str, List[VisualElement]] = {
            "buttons": [],
            "labels": [],
            "inputs": [],
            "errors": [],
            "modals": [],
        }
        button_keywords = {"ok", "cancel", "yes", "no", "save", "close", "open", "apply",
                          "submit", "accept", "decline", "retry", "abort", "ignore",
                          "next", "back", "finish", "browse", "delete", "remove"}
        error_keywords = {"error", "failed", "failure", "exception", "denied", "not found",
                         "access denied", "permission", "invalid", "warning", "critical"}
        modal_keywords = {"are you sure", "confirm", "do you want", "would you like",
                         "saving", "loading", "please wait", "processing"}

        for el in elements:
            text_lower = el.text.lower().strip()
            if text_lower in button_keywords or (len(text_lower) < 15 and el.confidence > 0.7):
                if text_lower in button_keywords:
                    patterns["buttons"].append(el)
            if any(kw in text_lower for kw in error_keywords):
                patterns["errors"].append(el)
            if any(kw in text_lower for kw in modal_keywords):
                patterns["modals"].append(el)
            if el.confidence > 0.5:
                patterns["labels"].append(el)

        return patterns

    async def _process_image(
        self,
        img: Any,
        *,
        source: str,
        hwnd: Optional[int] = None,
        window_title: Optional[str] = None,
    ) -> ScreenshotState:
        ts = time.time()
        w, h = img.size
        img_hash = await asyncio.to_thread(self._hash_image, img)
        img_path = self.screenshot_dir / f"screenshot_{int(ts * 1000)}_{img_hash[:8]}.png"
        await asyncio.to_thread(img.save, str(img_path), "PNG")

        elements = await self.extract_elements(img)

        self._cleanup_old_screenshots()

        return ScreenshotState(
            image_path=str(img_path),
            timestamp=ts,
            width=w,
            height=h,
            source=source,
            hwnd=hwnd,
            window_title=window_title,
            elements=elements,
            image_hash=img_hash,
        )

    @staticmethod
    def _hash_image(img: Any) -> str:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return hashlib.sha256(buf.getvalue()).hexdigest()[:16]

    def _cleanup_old_screenshots(self) -> None:
        try:
            files = sorted(self.screenshot_dir.glob("screenshot_*.png"), key=lambda p: p.stat().st_mtime)
            while len(files) > self.max_stored:
                files.pop(0).unlink(missing_ok=True)
        except Exception:
            pass


def redact_screenshot(image_path: str, regions: List[Tuple[int, int, int, int]]) -> str:
    """Black out sensitive regions in a screenshot for safe storage/display."""
    if Image is None:
        raise RuntimeError("Pillow required for redaction")
    img = Image.open(image_path)
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    for bbox in regions:
        draw.rectangle(bbox, fill="black")
    redacted_path = image_path.replace(".png", "_redacted.png")
    img.save(redacted_path, "PNG")
    return redacted_path
