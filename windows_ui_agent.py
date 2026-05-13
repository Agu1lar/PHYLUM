from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psutil

from windows_ui_models import WindowsUiElement, WindowsUiSelector

logger = logging.getLogger(__name__)
SELECTOR_MEMORY_PATH = Path(__file__).resolve().parent / "agent_workspace" / "windows_ui_selector_memory.json"

try:  # pragma: no cover - optional dependency
    from pywinauto import Desktop
    from pywinauto.keyboard import send_keys
except Exception:  # pragma: no cover - optional dependency
    Desktop = None
    send_keys = None


class WindowsUiUnavailable(RuntimeError):
    pass


def _ensure_backend() -> None:
    if Desktop is None:
        raise WindowsUiUnavailable(
            "Windows UI Automation backend is unavailable. Install pywinauto to enable native UI automation."
        )


def _safe_rect(wrapper) -> Dict[str, int]:
    try:
        rect = wrapper.rectangle()
        return {"left": int(rect.left), "top": int(rect.top), "right": int(rect.right), "bottom": int(rect.bottom)}
    except Exception:
        return {}


def _safe_process_name(process_id: Optional[int]) -> Optional[str]:
    if not process_id:
        return None
    try:
        return psutil.Process(process_id).name()
    except Exception:
        return None


class WindowsUiAgent:
    def __init__(self, *, selector_memory_path: Optional[Path] = None):
        self._element_cache: Dict[str, Dict[str, Any]] = {}
        self._selector_memory_path = selector_memory_path or SELECTOR_MEMORY_PATH
        self._selector_memory: Dict[str, Any] = self._load_selector_memory()

    def _desktop(self):
        _ensure_backend()
        return Desktop(backend="uia")

    def _load_selector_memory(self) -> Dict[str, Any]:
        try:
            if self._selector_memory_path.exists():
                data = json.loads(self._selector_memory_path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
        except Exception:
            logger.exception("failed to load Windows UI selector memory")
        return {}

    def _save_selector_memory(self) -> None:
        try:
            self._selector_memory_path.parent.mkdir(parents=True, exist_ok=True)
            self._selector_memory_path.write_text(
                json.dumps(self._selector_memory, ensure_ascii=True, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception:
            logger.exception("failed to save Windows UI selector memory")

    def _info_value(self, info, *names: str) -> Any:
        for name in names:
            value = getattr(info, name, None)
            if value not in (None, ""):
                return value
        return None

    def _compact_snapshot(self, wrapper) -> Dict[str, Any]:
        info = getattr(wrapper, "element_info", None)
        if info is None:
            return {}
        process_id = self._info_value(info, "process_id")
        return {
            "title": self._info_value(info, "name", "title"),
            "control_type": self._info_value(info, "control_type"),
            "auto_id": self._info_value(info, "automation_id", "auto_id"),
            "class_name": self._info_value(info, "class_name"),
            "hwnd": self._info_value(info, "handle", "hwnd"),
            "process_id": process_id,
            "process_name": _safe_process_name(process_id),
        }

    def _safe_parent(self, wrapper):
        for method_name in ("parent", "parent_ctrl"):
            method = getattr(wrapper, method_name, None)
            if callable(method):
                try:
                    return method()
                except Exception:
                    return None
        return None

    def _safe_children(self, wrapper) -> List[Any]:
        for method_name in ("children", "descendants"):
            method = getattr(wrapper, method_name, None)
            if callable(method):
                try:
                    return list(method())
                except Exception:
                    return []
        return []

    def _context_for(self, wrapper) -> Dict[str, Any]:
        parent = self._safe_parent(wrapper)
        parent_info = self._compact_snapshot(parent) if parent is not None else {}
        ancestors: List[Dict[str, Any]] = []
        current = parent
        seen: set[int] = set()
        while current is not None and id(current) not in seen and len(ancestors) < 5:
            seen.add(id(current))
            ancestors.append(self._compact_snapshot(current))
            current = self._safe_parent(current)
        siblings: List[Dict[str, Any]] = []
        if parent is not None:
            for sibling in self._safe_children(parent):
                if sibling is wrapper:
                    continue
                compact = self._compact_snapshot(sibling)
                if compact:
                    siblings.append(compact)
                if len(siblings) >= 10:
                    break
        return {"parent": parent_info, "ancestors": ancestors, "siblings": siblings}

    def _selector_from_element(self, element: WindowsUiElement) -> Dict[str, Any]:
        selector = {
            "title": element.title,
            "control_type": element.control_type,
            "auto_id": element.auto_id,
            "class_name": element.class_name,
            "process_name": element.process_name,
        }
        if element.parent:
            selector["parent_title"] = element.parent.get("title")
            selector["parent_control_type"] = element.parent.get("control_type")
        ancestor_titles = [item.get("title") for item in element.ancestors if item.get("title")]
        if ancestor_titles:
            selector["ancestor_titles"] = ancestor_titles[:3]
        sibling_titles = [item.get("title") for item in element.siblings if item.get("title")]
        if sibling_titles:
            selector["sibling_titles"] = sibling_titles[:5]
        return {key: value for key, value in selector.items() if value not in (None, "", [], {})}

    def _snapshot(self, wrapper) -> WindowsUiElement:
        info = getattr(wrapper, "element_info", None)
        process_id = self._info_value(info, "process_id")
        automation_id = self._info_value(info, "automation_id", "auto_id")
        class_name = self._info_value(info, "class_name")
        title = self._info_value(info, "name", "title")
        control_type = self._info_value(info, "control_type")
        handle = self._info_value(info, "handle", "hwnd")
        context = self._context_for(wrapper)
        element_id = "|".join(
            str(part)
            for part in (
                handle or "no-hwnd",
                process_id or "no-pid",
                control_type or "no-ctrl",
                automation_id or "no-autoid",
                title or "no-title",
            )
        )
        element = WindowsUiElement(
            element_id=element_id,
            title=title,
            control_type=control_type,
            auto_id=automation_id,
            class_name=class_name,
            hwnd=int(handle) if handle else None,
            process_id=int(process_id) if process_id else None,
            process_name=_safe_process_name(process_id),
            enabled=bool(wrapper.is_enabled()) if hasattr(wrapper, "is_enabled") else None,
            visible=bool(wrapper.is_visible()) if hasattr(wrapper, "is_visible") else None,
            rectangle=_safe_rect(wrapper),
            parent=context["parent"],
            ancestors=context["ancestors"],
            siblings=context["siblings"],
        )
        element.selector = self._selector_from_element(element)
        previous = self._element_cache.get(element_id) or {}
        if previous.get("match_score") is not None:
            element.match_score = previous.get("match_score")
        if previous.get("match_reasons"):
            element.match_reasons = list(previous.get("match_reasons") or [])
        self._element_cache[element_id] = {
            "hwnd": element.hwnd,
            "title": element.title,
            "process_name": element.process_name,
            "selector": element.selector,
            "match_score": element.match_score,
            "match_reasons": element.match_reasons,
        }
        return element

    def _all_windows(self):
        return list(self._desktop().windows())

    def _resolve_window(self, *, hwnd: Optional[int] = None, title: Optional[str] = None, process_name: Optional[str] = None):
        windows = self._all_windows()
        if hwnd is not None:
            matches = [window for window in windows if getattr(window.element_info, "handle", None) == hwnd]
        else:
            matches = windows
            if title:
                lowered = title.lower()
                matches = [window for window in matches if lowered in str(getattr(window.element_info, "name", "")).lower()]
            if process_name:
                lowered_process = process_name.lower()
                matches = [
                    window
                    for window in matches
                    if lowered_process in str(_safe_process_name(getattr(window.element_info, "process_id", None)) or "").lower()
                ]
        if not matches:
            raise ValueError("window not found")
        return matches[0]

    def _candidate_wrappers(self, window_wrapper, *, include_children: bool = True) -> List[Any]:
        candidates = [window_wrapper]
        if include_children:
            try:
                candidates.extend(window_wrapper.descendants())
            except Exception:
                logger.exception("failed to enumerate descendants for window")
        return candidates

    def _text_similarity(self, expected: Optional[str], actual: Optional[str], *, exact: bool = False) -> float:
        if not expected:
            return 0.0
        expected_norm = " ".join(str(expected).lower().split())
        actual_norm = " ".join(str(actual or "").lower().split())
        if not actual_norm:
            return -1.0
        if expected_norm == actual_norm:
            return 1.0
        if exact:
            return -1.0
        if expected_norm in actual_norm or actual_norm in expected_norm:
            return 0.82
        expected_tokens = set(expected_norm.split())
        actual_tokens = set(actual_norm.split())
        if not expected_tokens:
            return 0.0
        overlap = len(expected_tokens & actual_tokens) / len(expected_tokens)
        if overlap >= 0.5:
            return 0.35 + (overlap * 0.35)
        return -0.2

    def _field_similarity(self, expected: Optional[str], actual: Optional[str]) -> float:
        if not expected:
            return 0.0
        expected_norm = str(expected).lower()
        actual_norm = str(actual or "").lower()
        if not actual_norm:
            return -1.0
        if expected_norm == actual_norm:
            return 1.0
        if expected_norm in actual_norm or actual_norm in expected_norm:
            return 0.65
        return -0.35

    def _score_selector(self, element: WindowsUiElement, selector: WindowsUiSelector, *, ordinal: int = 0) -> Tuple[float, List[str]]:
        score = 0.0
        weight = 0.0
        reasons: List[str] = []

        def add(name: str, value: float, field_weight: float) -> None:
            nonlocal score, weight
            weight += field_weight
            score += value * field_weight
            if value > 0:
                reasons.append(name)

        if selector.hwnd is not None:
            add("hwnd", 1.0 if element.hwnd == selector.hwnd else -1.0, 4.0)
        if selector.auto_id:
            add("auto_id", self._field_similarity(selector.auto_id, element.auto_id), 4.0)
        if selector.control_type:
            add("control_type", self._field_similarity(selector.control_type, element.control_type), 2.0)
        if selector.class_name:
            add("class_name", self._field_similarity(selector.class_name, element.class_name), 1.5)
        if selector.process_name:
            add("process_name", self._field_similarity(selector.process_name, element.process_name), 1.5)
        if selector.title:
            add("title", self._text_similarity(selector.title, element.title, exact=bool(selector.exact_title)), 3.0)
        if selector.parent_title:
            add("parent_title", self._text_similarity(selector.parent_title, element.parent.get("title")), 1.6)
        if selector.parent_control_type:
            add("parent_control_type", self._field_similarity(selector.parent_control_type, element.parent.get("control_type")), 1.2)
        for anchor in selector.ancestor_titles:
            best = max([self._text_similarity(anchor, item.get("title")) for item in element.ancestors] or [-1.0])
            add("ancestor_title", best, 0.9)
        for anchor in selector.sibling_titles:
            best = max([self._text_similarity(anchor, item.get("title")) for item in element.siblings] or [-1.0])
            add("sibling_title", best, 0.6)
        if selector.near_title:
            neighbor_scores = [self._text_similarity(selector.near_title, item.get("title")) for item in element.siblings]
            neighbor_scores.extend(self._text_similarity(selector.near_title, item.get("title")) for item in element.ancestors)
            add("near_title", max(neighbor_scores or [-1.0]), 0.8)
        if selector.index is not None:
            distance = abs(max(selector.index, 0) - ordinal)
            add("index", max(0.0, 1.0 - (distance / 5.0)), 0.5)

        if weight <= 0:
            return 0.0, reasons
        confidence = max(0.0, min(1.0, (score / weight + 1.0) / 2.0))
        return confidence, reasons

    def _matches_selector(self, wrapper, selector: WindowsUiSelector) -> bool:
        element = self._snapshot(wrapper)
        score, _ = self._score_selector(element, selector)
        strict_fields = [selector.hwnd, selector.auto_id, selector.control_type, selector.class_name]
        threshold = 0.74 if any(value not in (None, "") for value in strict_fields) else 0.62
        return score >= threshold

    def _window_app_key(self, window_wrapper) -> str:
        info = getattr(window_wrapper, "element_info", None)
        process_id = self._info_value(info, "process_id")
        process_name = _safe_process_name(process_id) or "unknown"
        version = "unknown"
        try:
            proc = psutil.Process(process_id)
            exe = proc.exe()
            if exe:
                version = f"{Path(exe).name}:{int(Path(exe).stat().st_mtime)}"
        except Exception:
            pass
        return f"{process_name.lower()}::{version}"

    def _intent_key(self, *, title: Optional[str], process_name: Optional[str], selector: Optional[Dict[str, Any]], element_id: Optional[str]) -> str:
        payload = {
            "title": title,
            "process_name": process_name,
            "selector": selector or {},
            "element_id": element_id,
        }
        raw = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _remember_selector(
        self,
        *,
        window_wrapper,
        title: Optional[str],
        process_name: Optional[str],
        requested_selector: Optional[Dict[str, Any]],
        element_id: Optional[str],
        element: WindowsUiElement,
        score: float,
    ) -> None:
        if not requested_selector and not element_id:
            return
        app_key = self._window_app_key(window_wrapper)
        intent_key = self._intent_key(title=title, process_name=process_name, selector=requested_selector, element_id=element_id)
        app_memory = self._selector_memory.setdefault(app_key, {})
        previous = app_memory.get(intent_key, {})
        success_count = int(previous.get("success_count") or 0) + 1
        app_memory[intent_key] = {
            "selector": element.selector,
            "requested_selector": requested_selector or {},
            "element_id": element.element_id,
            "score": round(score, 4),
            "success_count": success_count,
            "last_seen": int(time.time()),
        }
        self._save_selector_memory()

    def _remembered_selector(
        self,
        *,
        window_wrapper,
        title: Optional[str],
        process_name: Optional[str],
        selector: Optional[Dict[str, Any]],
        element_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        app_key = self._window_app_key(window_wrapper)
        intent_key = self._intent_key(title=title, process_name=process_name, selector=selector, element_id=element_id)
        remembered = self._selector_memory.get(app_key, {}).get(intent_key)
        if isinstance(remembered, dict) and isinstance(remembered.get("selector"), dict):
            return remembered["selector"]
        return None

    def _record_successful_target(
        self,
        *,
        window_wrapper,
        title: Optional[str],
        process_name: Optional[str],
        requested_selector: Optional[Dict[str, Any]],
        element_id: Optional[str],
        target,
    ) -> WindowsUiElement:
        element = self._snapshot(target)
        if not isinstance(element, WindowsUiElement):
            return element
        score = float(element.match_score or 1.0)
        self._remember_selector(
            window_wrapper=window_wrapper,
            title=title,
            process_name=process_name,
            requested_selector=requested_selector,
            element_id=element_id,
            element=element,
            score=score,
        )
        return element

    def _resolve_candidates(
        self,
        *,
        hwnd: Optional[int] = None,
        title: Optional[str] = None,
        process_name: Optional[str] = None,
        selector: Optional[Dict[str, Any]] = None,
        element_id: Optional[str] = None,
        include_children: bool = True,
        max_results: int = 25,
    ) -> Tuple[Any, List[Any]]:
        window = self._resolve_window(hwnd=hwnd, title=title, process_name=process_name)
        effective_selector = selector
        if element_id and element_id in self._element_cache:
            cached = self._element_cache[element_id]
            effective_selector = cached.get("selector") or effective_selector
        if not effective_selector:
            return window, [window]

        selector_model = WindowsUiSelector(**effective_selector)
        scored_matches: List[Tuple[float, int, Any, WindowsUiElement, List[str]]] = []
        candidates = self._candidate_wrappers(window, include_children=include_children)
        for ordinal, candidate in enumerate(candidates):
            try:
                element = self._snapshot(candidate)
                score, reasons = self._score_selector(element, selector_model, ordinal=ordinal)
                if score >= 0.58:
                    scored_matches.append((score, ordinal, candidate, element, reasons))
            except Exception:
                logger.exception("failed to inspect UI candidate")
        if not scored_matches:
            remembered = self._remembered_selector(
                window_wrapper=window,
                title=title,
                process_name=process_name,
                selector=selector,
                element_id=element_id,
            )
            if remembered and remembered != effective_selector:
                selector_model = WindowsUiSelector(**remembered)
                for ordinal, candidate in enumerate(candidates):
                    try:
                        element = self._snapshot(candidate)
                        score, reasons = self._score_selector(element, selector_model, ordinal=ordinal)
                        if score >= 0.58:
                            scored_matches.append((score, ordinal, candidate, element, reasons + ["selector_memory"]))
                    except Exception:
                        logger.exception("failed to inspect UI candidate from selector memory")
        scored_matches.sort(key=lambda item: (-item[0], item[1]))
        matches = [item[2] for item in scored_matches[:max_results]]
        for score, _, candidate, element, reasons in scored_matches[:max_results]:
            element.match_score = round(score, 4)
            element.match_reasons = reasons
            self._element_cache[element.element_id] = {
                "hwnd": element.hwnd,
                "title": element.title,
                "process_name": element.process_name,
                "selector": element.selector,
                "match_score": element.match_score,
                "match_reasons": element.match_reasons,
            }
        return window, matches

    async def inspect_window(self, *, hwnd: Optional[int] = None, title: Optional[str] = None, process_name: Optional[str] = None, include_children: bool = True, max_results: int = 25) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            window = self._resolve_window(hwnd=hwnd, title=title, process_name=process_name)
            snapshot = self._snapshot(window)
            children = []
            if include_children:
                for child in self._candidate_wrappers(window, include_children=True)[1:max_results + 1]:
                    children.append(self._snapshot(child).dict())
            return {"window": snapshot.dict(), "children": children}

        return await asyncio.to_thread(_run)

    async def list_elements(self, *, hwnd: Optional[int] = None, title: Optional[str] = None, process_name: Optional[str] = None, selector: Optional[Dict[str, Any]] = None, include_children: bool = True, max_results: int = 50) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            _, matches = self._resolve_candidates(
                hwnd=hwnd,
                title=title,
                process_name=process_name,
                selector=selector,
                include_children=include_children,
                max_results=max_results,
            )
            return {"elements": [self._snapshot(match).dict() for match in matches]}

        return await asyncio.to_thread(_run)

    async def find_element(self, *, hwnd: Optional[int] = None, title: Optional[str] = None, process_name: Optional[str] = None, selector: Optional[Dict[str, Any]] = None, element_id: Optional[str] = None, include_children: bool = True, max_results: int = 10) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            window, matches = self._resolve_candidates(
                hwnd=hwnd,
                title=title,
                process_name=process_name,
                selector=selector,
                element_id=element_id,
                include_children=include_children,
                max_results=max_results,
            )
            payload = [self._snapshot(match).dict() for match in matches]
            best = payload[0] if payload else None
            best_score = float(best.get("match_score") or 0.0) if best else 0.0
            second_score = float(payload[1].get("match_score") or 0.0) if len(payload) > 1 else 0.0
            resolved = bool(best) and (len(payload) == 1 or (best_score >= 0.72 and best_score - second_score >= 0.12))
            if resolved and best:
                self._remember_selector(
                    window_wrapper=window,
                    title=title,
                    process_name=process_name,
                    requested_selector=selector,
                    element_id=element_id,
                    element=WindowsUiElement(**best),
                    score=best_score,
                )
            return {
                "matches": payload,
                "best_match": best,
                "ambiguity_resolved": resolved,
                "resolution": {
                    "best_score": round(best_score, 4),
                    "second_score": round(second_score, 4),
                    "strategy": "ranked_selector_healing" if resolved else "candidate_ranking",
                },
            }

        return await asyncio.to_thread(_run)

    def _classify_dialog(self, window: Dict[str, Any], children: List[Dict[str, Any]]) -> Dict[str, Any]:
        text = " ".join(
            str(item or "")
            for item in [
                window.get("title"),
                window.get("control_type"),
                *(child.get("title") for child in children),
                *(child.get("auto_id") for child in children),
            ]
        ).lower()
        profiles = {
            "print_dialog": ["print", "printer", "impressora", "copies", "pages"],
            "file_picker": ["open", "save as", "file name", "nome do arquivo", "filename", "address"],
            "auth_popup": ["password", "senha", "sign in", "login", "username", "usuario", "credential"],
            "installer_setup": ["setup", "install", "installer", "license", "finish", "next"],
        }
        scores = {kind: sum(1 for term in terms if term in text) for kind, terms in profiles.items()}
        best_kind = max(scores, key=scores.get)
        return {
            "kind": best_kind if scores[best_kind] else "generic_dialog",
            "confidence": min(1.0, scores[best_kind] / 3.0),
            "signals": scores,
            "recommended_actions": {
                "print_dialog": ["select printer", "set copies/pages", "invoke print"],
                "file_picker": ["set file name", "navigate folder", "invoke open/save"],
                "auth_popup": ["fill username/password", "submit credentials"],
                "installer_setup": ["inspect license/options", "invoke next/install/finish"],
                "generic_dialog": ["inspect controls", "rank buttons by title"],
            }.get(best_kind if scores[best_kind] else "generic_dialog", []),
        }

    async def inspect_dialog(self, *, hwnd: Optional[int] = None, title: Optional[str] = None, process_name: Optional[str] = None, include_children: bool = True, max_results: int = 50) -> Dict[str, Any]:
        details = await self.inspect_window(
            hwnd=hwnd,
            title=title,
            process_name=process_name,
            include_children=include_children,
            max_results=max_results,
        )
        details["dialog"] = self._classify_dialog(details.get("window") or {}, details.get("children") or [])
        return details

    async def wait_for_element(self, *, hwnd: Optional[int] = None, title: Optional[str] = None, process_name: Optional[str] = None, selector: Optional[Dict[str, Any]] = None, element_id: Optional[str] = None, include_children: bool = True, timeout_seconds: int = 15) -> Dict[str, Any]:
        deadline = time.monotonic() + max(timeout_seconds, 1)
        while time.monotonic() < deadline:
            result = await self.find_element(
                hwnd=hwnd,
                title=title,
                process_name=process_name,
                selector=selector,
                element_id=element_id,
                include_children=include_children,
                max_results=5,
            )
            if result["matches"]:
                return {"element": result["matches"][0], "matches": result["matches"]}
            await asyncio.sleep(0.35)
        raise TimeoutError("element did not appear before timeout")

    async def invoke_element(self, *, hwnd: Optional[int] = None, title: Optional[str] = None, process_name: Optional[str] = None, selector: Optional[Dict[str, Any]] = None, element_id: Optional[str] = None) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            window, matches = self._resolve_candidates(
                hwnd=hwnd,
                title=title,
                process_name=process_name,
                selector=selector,
                element_id=element_id,
                include_children=True,
                max_results=5,
            )
            if not matches:
                raise ValueError("element not found")
            target = matches[0]
            errors: List[str] = []

            def _attempt(method_name: str, callback) -> Optional[str]:
                try:
                    callback()
                    return method_name
                except Exception as exc:
                    logger.debug("invoke_element fallback %s failed", method_name, exc_info=True)
                    errors.append(f"{method_name}: {exc}")
                    return None

            _attempt("set_focus", lambda: target.set_focus()) if hasattr(target, "set_focus") else None

            used_method = None
            if hasattr(target, "invoke"):
                used_method = _attempt("invoke", lambda: target.invoke())
            if used_method is None and hasattr(target, "select"):
                used_method = _attempt("select", lambda: target.select())
            if used_method is None and hasattr(target, "click_input"):
                used_method = _attempt("click_input", lambda: target.click_input())
            if used_method is None and hasattr(target, "click"):
                used_method = _attempt("click", lambda: target.click())
            if used_method is None and hasattr(target, "type_keys"):
                used_method = _attempt("type_keys_enter", lambda: target.type_keys("{ENTER}", set_foreground=True))
            if used_method is None:
                details = " | ".join(errors) if errors else "element does not support invoke"
                raise RuntimeError(details)
            element = self._record_successful_target(
                window_wrapper=window,
                title=title,
                process_name=process_name,
                requested_selector=selector,
                element_id=element_id,
                target=target,
            )
            return {"element": element.dict(), "method": used_method, "fallback_errors": errors}

        return await asyncio.to_thread(_run)

    async def set_text(self, *, text: str, hwnd: Optional[int] = None, title: Optional[str] = None, process_name: Optional[str] = None, selector: Optional[Dict[str, Any]] = None, element_id: Optional[str] = None) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            window, matches = self._resolve_candidates(
                hwnd=hwnd,
                title=title,
                process_name=process_name,
                selector=selector,
                element_id=element_id,
                include_children=True,
                max_results=5,
            )
            if not matches:
                raise ValueError("element not found")
            target = matches[0]
            if hasattr(target, "set_edit_text"):
                target.set_edit_text(text)
            elif hasattr(target, "type_keys"):
                target.type_keys("^a{BACKSPACE}", set_foreground=True)
                target.type_keys(text, with_spaces=True, pause=0.01)
            else:
                raise RuntimeError("element does not support text input")
            element = self._record_successful_target(
                window_wrapper=window,
                title=title,
                process_name=process_name,
                requested_selector=selector,
                element_id=element_id,
                target=target,
            )
            return {"element": element.dict(), "text": text}

        return await asyncio.to_thread(_run)

    async def select_item(self, *, item_text: str, hwnd: Optional[int] = None, title: Optional[str] = None, process_name: Optional[str] = None, selector: Optional[Dict[str, Any]] = None, element_id: Optional[str] = None) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            window, matches = self._resolve_candidates(
                hwnd=hwnd,
                title=title,
                process_name=process_name,
                selector=selector,
                element_id=element_id,
                include_children=True,
                max_results=5,
            )
            if not matches:
                raise ValueError("element not found")
            target = matches[0]
            if hasattr(target, "select"):
                target.select(item_text)
            elif hasattr(target, "type_keys"):
                target.type_keys(item_text, with_spaces=True)
            else:
                raise RuntimeError("element does not support selection")
            element = self._record_successful_target(
                window_wrapper=window,
                title=title,
                process_name=process_name,
                requested_selector=selector,
                element_id=element_id,
                target=target,
            )
            return {"element": element.dict(), "item_text": item_text}

        return await asyncio.to_thread(_run)

    async def send_hotkey(self, hotkey: str) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            _ensure_backend()
            if send_keys is None:
                raise WindowsUiUnavailable("send_keys backend is unavailable")
            send_keys(hotkey)
            return {"hotkey": hotkey}

        return await asyncio.to_thread(_run)

    async def scroll(self, *, direction: Optional[str] = None, amount: int = 1, hwnd: Optional[int] = None, title: Optional[str] = None, process_name: Optional[str] = None, selector: Optional[Dict[str, Any]] = None, element_id: Optional[str] = None) -> Dict[str, Any]:
        key = "{PGDN}" if str(direction or "down").lower() != "up" else "{PGUP}"

        def _run() -> Dict[str, Any]:
            window, matches = self._resolve_candidates(
                hwnd=hwnd,
                title=title,
                process_name=process_name,
                selector=selector,
                element_id=element_id,
                include_children=True,
                max_results=5,
            )
            if not matches:
                raise ValueError("element not found")
            target = matches[0]
            if not hasattr(target, "type_keys"):
                raise RuntimeError("element does not support keyboard scrolling")
            for _ in range(max(amount, 1)):
                target.type_keys(key, set_foreground=True)
            element = self._record_successful_target(
                window_wrapper=window,
                title=title,
                process_name=process_name,
                requested_selector=selector,
                element_id=element_id,
                target=target,
            )
            return {"element": element.dict(), "direction": direction or "down", "amount": amount}

        return await asyncio.to_thread(_run)

    async def read_element_text(self, *, hwnd: Optional[int] = None, title: Optional[str] = None, process_name: Optional[str] = None, selector: Optional[Dict[str, Any]] = None, element_id: Optional[str] = None) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            window, matches = self._resolve_candidates(
                hwnd=hwnd,
                title=title,
                process_name=process_name,
                selector=selector,
                element_id=element_id,
                include_children=True,
                max_results=5,
            )
            if not matches:
                raise ValueError("element not found")
            target = matches[0]
            text = ""
            if hasattr(target, "window_text"):
                text = target.window_text()
            elif hasattr(target, "texts"):
                text = "\n".join(target.texts())
            element = self._record_successful_target(
                window_wrapper=window,
                title=title,
                process_name=process_name,
                requested_selector=selector,
                element_id=element_id,
                target=target,
            )
            return {"element": element.dict(), "text": text}

        return await asyncio.to_thread(_run)

    async def get_focused_element(self) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            desktop = self._desktop()
            focused = desktop.get_focus()
            return {"element": self._snapshot(focused).dict()}

        return await asyncio.to_thread(_run)

