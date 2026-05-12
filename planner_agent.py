"""Planner Agent: turns natural language into structured tasks.
- Rule-based decomposition + lightweight heuristics
- Dependency detection, prioritization, validation
- Produces Plan (list of Task models) compatible with tool-calling
"""
from __future__ import annotations
import re
import uuid
import asyncio
import logging
from typing import List, Dict, Any, Tuple, Optional
from pydantic import BaseModel

from canonical_tools import supported_tools as canonical_supported_tools
from planner_models import Task, Plan, ValidationResult

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Simple grammar: map verbs/keywords to tools/actions
TOOL_KEYWORDS = {
    'run command': {'tool': 'shell', 'action': 'run'},
    'execute command': {'tool': 'shell', 'action': 'run'},
    'powershell': {'tool': 'shell', 'action': 'run'},
    'remember that': {'tool': 'memory', 'action': 'set'},
    'remember': {'tool': 'memory', 'action': 'set'},
    'recall': {'tool': 'memory', 'action': 'get'},
    'forget': {'tool': 'memory', 'action': 'delete'},
    'read file': {'tool': 'filesystem', 'action': 'read'},
    'read': {'tool': 'filesystem', 'action': 'read'},
    'write file': {'tool': 'filesystem', 'action': 'write'},
    'write': {'tool': 'filesystem', 'action': 'write'},
    'delete file': {'tool': 'filesystem', 'action': 'delete'},
    'mkdir': {'tool': 'filesystem', 'action': 'mkdir'},
    'make directory': {'tool': 'filesystem', 'action': 'mkdir'},
    'list packages': {'tool': 'package_manager', 'action': 'list'},
    'search package': {'tool': 'package_manager', 'action': 'search'},
    'show package': {'tool': 'package_manager', 'action': 'show'},
    'upgrade package': {'tool': 'package_manager', 'action': 'upgrade'},
    'install': {'tool': 'package_manager', 'action': 'install'},
    'uninstall': {'tool': 'package_manager', 'action': 'uninstall'},
    'download': {'tool': 'browser', 'action': 'download'},
    'open url': {'tool': 'browser', 'action': 'open_page'},
    'browse to': {'tool': 'browser', 'action': 'open_page'},
    'search': {'tool': 'browser', 'action': 'search'},
    'scrape': {'tool': 'browser', 'action': 'scrape_structured'},
    'search web': {'tool': 'web', 'action': 'search_web'},
    'fetch page': {'tool': 'web', 'action': 'fetch_readonly'},
    'check url': {'tool': 'web', 'action': 'check_url'},
    'download file': {'tool': 'web', 'action': 'download_verified'},
    'find executable': {'tool': 'software_inventory', 'action': 'find_executable'},
    'resolve command': {'tool': 'software_inventory', 'action': 'resolve_command'},
    'find install location': {'tool': 'software_inventory', 'action': 'find_install_location'},
    'find uninstaller': {'tool': 'software_inventory', 'action': 'find_uninstaller'},
    'search installed': {'tool': 'software_inventory', 'action': 'search_installed'},
    'list installed software': {'tool': 'software_inventory', 'action': 'list_installed'},
    'get env': {'tool': 'env_manager', 'action': 'get'},
    'set env': {'tool': 'env_manager', 'action': 'set'},
    'unset env': {'tool': 'env_manager', 'action': 'unset'},
    'add to path': {'tool': 'env_manager', 'action': 'append_path'},
    'remove from path': {'tool': 'env_manager', 'action': 'remove_path'},
    'list path entries': {'tool': 'env_manager', 'action': 'list_path_entries'},
    'backup env': {'tool': 'env_manager', 'action': 'backup'},
    'restore env': {'tool': 'env_manager', 'action': 'restore'},
    'list devices': {'tool': 'driver_manager', 'action': 'list_devices'},
    'device status': {'tool': 'driver_manager', 'action': 'device_status'},
    'list drivers': {'tool': 'driver_manager', 'action': 'list_drivers'},
    'find driver': {'tool': 'driver_manager', 'action': 'find_driver_candidates'},
    'printer status': {'tool': 'driver_manager', 'action': 'printer_status'},
    'printer driver info': {'tool': 'driver_manager', 'action': 'printer_driver_info'},
    'restart spooler': {'tool': 'driver_manager', 'action': 'restart_spooler'},
    'system info': {'tool': 'os', 'action': 'overview'},
    'os info': {'tool': 'os', 'action': 'overview'},
    'inspect system': {'tool': 'os', 'action': 'full'},
    'list apps': {'tool': 'os', 'action': 'apps'},
    'list installed apps': {'tool': 'os', 'action': 'apps'},
    'list processes': {'tool': 'os', 'action': 'processes'},
    'show clipboard': {'tool': 'desktop', 'action': 'clipboard_get'},
    'get clipboard': {'tool': 'desktop', 'action': 'clipboard_get'},
    'set clipboard': {'tool': 'desktop', 'action': 'clipboard_set'},
    'copy to clipboard': {'tool': 'desktop', 'action': 'clipboard_set'},
    'list windows': {'tool': 'desktop', 'action': 'list_windows'},
    'list explorer windows': {'tool': 'desktop', 'action': 'list_explorer_windows'},
    'list file explorer windows': {'tool': 'desktop', 'action': 'list_explorer_windows'},
    'list open folders': {'tool': 'desktop', 'action': 'list_explorer_windows'},
    'list mapped drives': {'tool': 'desktop', 'action': 'list_mapped_drives'},
    'mapped drives': {'tool': 'desktop', 'action': 'list_mapped_drives'},
    'list explorer selection': {'tool': 'desktop', 'action': 'get_explorer_selection'},
    'selected files': {'tool': 'desktop', 'action': 'get_explorer_selection'},
    'open app': {'tool': 'desktop', 'action': 'open_app'},
    'launch app': {'tool': 'desktop', 'action': 'open_app'},
    'open folder': {'tool': 'desktop', 'action': 'open_path'},
    'open path': {'tool': 'desktop', 'action': 'open_path'},
    'open file': {'tool': 'desktop', 'action': 'open_file'},
    'wait for window': {'tool': 'desktop', 'action': 'wait_for_window'},
    'focus window': {'tool': 'desktop', 'action': 'focus_window'},
    'close window': {'tool': 'desktop', 'action': 'close_window'},
    'kill process': {'tool': 'desktop', 'action': 'kill_process'},
    'notify': {'tool': 'desktop', 'action': 'notify'},
    'notification': {'tool': 'desktop', 'action': 'notify'},
    'list services': {'tool': 'desktop', 'action': 'list_services'},
    'start service': {'tool': 'desktop', 'action': 'service_action'},
    'stop service': {'tool': 'desktop', 'action': 'service_action'},
    'restart service': {'tool': 'desktop', 'action': 'service_action'},
    'inspect window': {'tool': 'windows_ui', 'action': 'inspect_window'},
    'list elements': {'tool': 'windows_ui', 'action': 'list_elements'},
    'find element': {'tool': 'windows_ui', 'action': 'find_element'},
    'wait for element': {'tool': 'windows_ui', 'action': 'wait_for_element'},
    'read element text': {'tool': 'windows_ui', 'action': 'read_element_text'},
    'focused element': {'tool': 'windows_ui', 'action': 'get_focused_element'},
    'list shares': {'tool': 'share_discovery', 'action': 'list_mappings'},
    'inspect share': {'tool': 'share_discovery', 'action': 'inspect_share'},
    'find share': {'tool': 'share_discovery', 'action': 'discover_targets'},
    'recent documents': {'tool': 'document_intelligence', 'action': 'recent_documents'},
    'extract text': {'tool': 'document_intelligence', 'action': 'extract_text'},
    'inspect document': {'tool': 'document_intelligence', 'action': 'inspect_document'},
    'search document content': {'tool': 'document_intelligence', 'action': 'search_content'},
    'open document': {'tool': 'office', 'action': 'open_document'},
    'export pdf': {'tool': 'office', 'action': 'export_pdf'},
    'save as': {'tool': 'office', 'action': 'save_as_document'},
    'list workbook sheets': {'tool': 'office', 'action': 'list_workbook_sheets'},
    'draft email': {'tool': 'office', 'action': 'draft_email_with_attachment'},
    'active document path': {'tool': 'office', 'action': 'reveal_active_document_path'},
    'move': {'tool': 'filesystem', 'action': 'move'},
    'organize downloads': {'tool': 'filesystem', 'action': 'organize_downloads'},
    'organize desktop': {'tool': 'filesystem', 'action': 'organize_desktop'},
    'organize folder': {'tool': 'filesystem', 'action': 'organize_directory'},
    'find duplicates': {'tool': 'filesystem', 'action': 'detect_duplicates'},
    'clean temp': {'tool': 'filesystem', 'action': 'clean_temp'},
    'create structure': {'tool': 'filesystem', 'action': 'create_structure'},
    'undo request': {'tool': 'filesystem', 'action': 'undo'},
    'find files': {'tool': 'filesystem', 'action': 'find_files'},
    'list files': {'tool': 'filesystem', 'action': 'list'},
    'file stat': {'tool': 'filesystem', 'action': 'stat'},
    'copy file': {'tool': 'filesystem', 'action': 'copy'},
}

# Priority by tool importance (lower number = higher priority)
TOOL_PRIORITY = {
    'desktop': 5,
    'windows_ui': 4,
    'share_discovery': 6,
    'document_intelligence': 7,
    'office': 7,
    'driver_manager': 6,
    'os': 8,
    'software_inventory': 9,
    'env_manager': 9,
    'shell': 20,
    'package_manager': 10,
    'web': 25,
    'filesystem': 30,
    'browser': 40,
    'memory': 50,
}


class PlannerAgent:
    def __init__(self, *, supported_tools: Optional[List[str]] = None):
        self.supported_tools = set(supported_tools or canonical_supported_tools())

    async def parse(self, text: str) -> Tuple[Plan, ValidationResult]:
        """Main entry point. Returns Plan and ValidationResult."""
        text = text.strip()
        tasks: List[Task] = []
        warnings: List[str] = []
        errors: List[str] = []

        # normalize
        lowered = text.lower()

        # simple split by ' and ' or commas/semicolons
        parts = re.split(r"\band\b|;|,", lowered)
        idx = 0
        for part in parts:
            part = part.strip()
            if not part:
                continue
            special_task = self._infer_open_or_launch_task(part, idx)
            if special_task is not None:
                tasks.append(special_task)
                idx += 1
                continue
            # try to match multi-word keys first
            found = False
            for kw in sorted(TOOL_KEYWORDS.keys(), key=lambda s: -len(s)):
                if kw in part:
                    mapping = TOOL_KEYWORDS[kw]
                    tool = mapping['tool']
                    action = mapping['action']
                    if tool not in self.supported_tools:
                        warnings.append(f"Tool '{tool}' not supported; skipping: {part}")
                        found = True
                        break
                    params = self._extract_params_for_action(action, part, kw)
                    tid = f"task-{idx}-{uuid.uuid4().hex[:6]}"
                    priority = TOOL_PRIORITY.get(tool, 50)
                    task = Task(id=tid, tool=tool, action=action, params=params, priority=priority)
                    tasks.append(task)
                    idx += 1
                    found = True
                    break
            if not found:
                # fallback heuristics: 'install X' pattern
                m = re.match(r"install\s+(?P<pkg>[\w\-\.]+)", part)
                if m:
                    pkg = m.group('pkg')
                    tool = 'package_manager'
                    if tool not in self.supported_tools:
                        warnings.append(f"Tool '{tool}' not supported; skipping: {part}")
                        continue
                    params = {'package': pkg}
                    tid = f"task-{idx}-{uuid.uuid4().hex[:6]}"
                    task = Task(id=tid, tool=tool, action='install', params=params, priority=TOOL_PRIORITY.get(tool, 50))
                    tasks.append(task)
                    idx += 1
                    continue
                # otherwise unrecognized
                warnings.append(f"Could not parse: '{part}'")

        # detect dependencies and refine
        tasks = self._detect_dependencies(tasks)

        # prioritize (lower priority first)
        tasks.sort(key=lambda t: t.priority)

        plan = Plan(original_text=text, tasks=tasks)

        # validate tasks
        v = self.validate_plan(plan)

        return plan, v

    def _extract_params_for_action(self, action: str, part: str, kw: str) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if action == 'run':
            command = part
            for prefix in ('run command', 'execute command'):
                if command.startswith(prefix):
                    command = command[len(prefix):].strip()
                    break
            if command.startswith('powershell'):
                command = command[len('powershell'):].strip()
            params['command'] = command
            params['shell'] = 'powershell'
        if action == 'set':
            m = re.search(r"remember(?:\s+that)?\s+(?P<key>[\w\-.]+)\s+(?:is|=|as)\s+(?P<value>.+)", part)
            if m:
                params['key'] = m.group('key')
                params['value'] = {'text': m.group('value').strip()}
            else:
                params['key'] = 'note'
                params['value'] = {'text': part.replace(kw, '', 1).strip()}
        if action == 'get':
            m = re.search(r"recall\s+(?P<key>[\w\-.]+)", part)
            if m:
                params['key'] = m.group('key')
        if action == 'delete' and kw in ('forget',):
            m = re.search(r"forget\s+(?P<key>[\w\-.]+)", part)
            if m:
                params['key'] = m.group('key')
        # examples: 'install vscode', 'download https://...' 'move photos to Pictures'
        if action in ('install', 'uninstall', 'search', 'show', 'upgrade'):
            # try to pick token after keyword
            m = re.search(rf"{re.escape(kw)}\s+(?P<name>[\w\-\.]+)", part)
            if m:
                params['package'] = m.group('name')
            params['manager'] = 'winget' if 'winget' in part else 'choco'
        if action == 'list' and 'package' in part:
            params['manager'] = 'choco'
        if action in ('read', 'delete', 'mkdir', 'organize_directory', 'detect_duplicates', 'clean_temp', 'find_files', 'list', 'stat'):
            path = self._extract_path(part)
            if path:
                params['path'] = path
        if action == 'write':
            m = re.search(r"write\s+(?P<content>.+?)\s+to\s+(?P<path>[A-Za-z]:[\\/][^\n]+)", part)
            if m:
                params['content'] = m.group('content').strip().strip("'\"")
                params['path'] = m.group('path').strip()
            else:
                path = self._extract_path(part)
                if path:
                    params['path'] = path
                    content = part.split(path, 1)[0].replace('write', '', 1).replace('file', '', 1).strip()
                    if content:
                        params['content'] = content.strip().strip("'\"")
        if action == 'download':
            m = re.search(r"(https?://\S+)", part)
            if m:
                params['url'] = m.group(1)
        if action in {'search_web', 'fetch_readonly', 'check_url', 'download_verified'}:
            m = re.search(r"(https?://\S+)", part)
            if m:
                params['url'] = m.group(1)
            query = part.replace(kw, '', 1).strip()
            if query and 'http' not in query:
                params['query'] = query.strip("'\"")
        if action == 'open_page':
            m = re.search(r"(https?://\S+)", part)
            if m:
                params['url'] = m.group(1)
        if action == 'open_app':
            app_name = part.replace(kw, '', 1).strip().strip("'\"")
            if app_name:
                params['app_name'] = app_name
        if action in {'open_path', 'open_file'}:
            path = self._extract_path(part)
            if path:
                params['path'] = path
        if action == 'wait_for_window':
            title = part.replace(kw, '', 1).strip().strip("'\"")
            if title:
                params['title'] = title
        if action == 'search':
            m = re.search(r"search\s+(?P<query>.+?)\s+(?:on|in)\s+(?P<url>https?://\S+)", part)
            if m:
                params['query'] = m.group('query').strip().strip("'\"")
                params['base_url'] = m.group('url')
                params['query_selector'] = 'input[type="search"], input[name="q"], input[type="text"]'
            else:
                query = part.replace(kw, '', 1).strip()
                if query:
                    params['query'] = query
                    params['base_url'] = 'https://www.google.com'
                    params['query_selector'] = 'textarea[name="q"], input[name="q"]'
        if action == 'scrape_structured':
            m = re.search(r"(https?://\S+)", part)
            if m:
                params['url'] = m.group(1)
                params['extractors'] = {'body': 'body'}
        if action == 'move':
            # move X to Y
            m = re.search(r"move\s+(?P<src>\S+)\s+to\s+(?P<dst>\S+)", part)
            if m:
                params['path'] = m.group('src')
                params['dest'] = m.group('dst')
        if action == 'copy':
            m = re.search(r"copy(?:\s+file)?\s+(?P<src>\S+)\s+to\s+(?P<dst>\S+)", part)
            if m:
                params['path'] = m.group('src')
                params['dest'] = m.group('dst')
        if action == 'clipboard_set':
            text = part.replace(kw, '', 1).strip().strip("'\"")
            if text:
                params['text'] = text
        if action == 'focus_window':
            title = part.replace(kw, '', 1).strip().strip("'\"")
            if title:
                params['title'] = title
        if action == 'close_window':
            title = part.replace(kw, '', 1).strip().strip("'\"")
            if title:
                params['title'] = title
        if action == 'kill_process':
            process_name = part.replace(kw, '', 1).strip().strip("'\"")
            if process_name:
                params['process_name'] = process_name
        if action == 'notify':
            message = part.replace(kw, '', 1).strip().strip("'\"")
            if message:
                params['message'] = message
        if action == 'service_action':
            service_action = 'restart' if 'restart service' in part else 'stop' if 'stop service' in part else 'start'
            params['service_action'] = service_action
            service_name = re.sub(r'^(start|stop|restart)\s+service\s+', '', part).strip().strip("'\"")
            if service_name:
                params['service_name'] = service_name
        if action in {'inspect_window', 'list_elements', 'find_element', 'wait_for_element', 'read_element_text'}:
            title = part.replace(kw, '', 1).strip().strip("'\"")
            if title:
                params['title'] = title
        if action == 'inspect_share':
            path = self._extract_path(part)
            if path:
                params['path'] = path
        if action == 'discover_targets':
            query = part.replace(kw, '', 1).strip().strip("'\"")
            if query:
                params['query'] = query
        if action in {'inspect_document', 'extract_text', 'open_document', 'export_pdf', 'list_workbook_sheets'}:
            path = self._extract_path(part)
            if path:
                params['path'] = path
        if action == 'search_content':
            path = self._extract_path(part)
            if path:
                params['root'] = path
            query = part.replace(kw, '', 1).replace(path or '', '', 1).strip().strip("'\"")
            if query:
                params['query'] = query
        if action == 'save_as_document':
            matches = re.search(r"save as\s+(?P<source>[A-Za-z]:[\\/][^\n]+?)\s+to\s+(?P<dest>[A-Za-z]:[\\/][^\n]+)", part)
            if matches:
                params['path'] = matches.group('source').strip()
                params['output_path'] = matches.group('dest').strip()
        if action == 'draft_email_with_attachment':
            attachment = self._extract_path(part)
            if attachment:
                params['attachment_path'] = attachment
        if action == 'reveal_active_document_path':
            app_name = part.replace(kw, '', 1).strip().strip("'\"")
            if app_name:
                params['app_name'] = app_name
        if action in {'find_executable', 'find_install_location', 'find_uninstaller', 'search_installed', 'find_driver_candidates', 'device_status', 'printer_status', 'printer_driver_info'}:
            query = part.replace(kw, '', 1).strip().strip("'\"")
            if query:
                params['query'] = query
        if action == 'resolve_command':
            command = part.replace(kw, '', 1).strip().strip("'\"")
            if command:
                params['command'] = command
        if action in {'get', 'set', 'unset'} and 'env' in kw:
            m = re.search(r"env\s+(?P<name>[\w\-.]+)(?:\s+(?:to|as|=)\s+(?P<value>.+))?", part)
            if m:
                params['name'] = m.group('name')
                if m.group('value') is not None:
                    params['value'] = m.group('value').strip().strip("'\"")
            params['scope'] = 'user'
        if action in {'append_path', 'remove_path'}:
            entry = part.replace(kw, '', 1).strip().strip("'\"")
            if entry:
                params['entry'] = entry
            params['scope'] = 'user'
        if action == 'backup':
            params['scope'] = 'user'
        if action == 'restore':
            backup_id = part.replace(kw, '', 1).strip().strip("'\"")
            if backup_id:
                params['backup_id'] = backup_id
        if action == 'create_structure':
            path = self._extract_path(part)
            if path:
                params['path'] = path
            params['template'] = {'docs': {}, 'src': {}, 'bin': {}}
        if action == 'undo':
            m = re.search(r"undo(?:\s+request)?\s+(?P<request>[\w:\-]+)", part)
            if m:
                params['request_id'] = m.group('request')
        if action == 'list_installed':
            pass
        return params

    def _extract_path(self, text: str) -> Optional[str]:
        unc_match = re.search(r"(\\\\[^\s,;]+(?:\\[^\s,;]+)*)", text)
        if unc_match:
            return unc_match.group(1).strip().strip("'\"")
        path_match = re.search(r"([A-Za-z]:[\\/][^,\n]+)", text)
        if path_match:
            return path_match.group(1).strip().strip("'\"")
        quoted_match = re.search(r"['\"](?P<path>[A-Za-z]:[\\/][^'\"]+)['\"]", text)
        if quoted_match:
            return quoted_match.group('path').strip()
        return None

    def _infer_open_or_launch_task(self, part: str, idx: int) -> Optional[Task]:
        prefixes = ("open ", "launch ", "start ")
        prefix = next((item for item in prefixes if part.startswith(item)), None)
        if not prefix:
            return None

        if part.startswith("start service") or part.startswith("restart service") or part.startswith("stop service"):
            return None

        target = part[len(prefix):].strip().strip("'\"")
        if not target:
            return None

        if re.search(r"https?://\S+", target):
            return Task(
                id=f"task-{idx}-{uuid.uuid4().hex[:6]}",
                tool="browser",
                action="open_page",
                params={"url": re.search(r"(https?://\S+)", target).group(1)},
                priority=TOOL_PRIORITY.get("browser", 50),
            )

        path = self._extract_path(target)
        if path:
            action = "open_file" if re.search(r"\.[A-Za-z0-9]{1,6}$", path) else "open_path"
            return Task(
                id=f"task-{idx}-{uuid.uuid4().hex[:6]}",
                tool="desktop",
                action=action,
                params={"path": path},
                priority=TOOL_PRIORITY.get("desktop", 50),
            )

        if target.startswith("\\\\"):
            return Task(
                id=f"task-{idx}-{uuid.uuid4().hex[:6]}",
                tool="desktop",
                action="open_path",
                params={"path": target},
                priority=TOOL_PRIORITY.get("desktop", 50),
            )

        return Task(
            id=f"task-{idx}-{uuid.uuid4().hex[:6]}",
            tool="desktop",
            action="open_app",
            params={"app_name": target},
            priority=TOOL_PRIORITY.get("desktop", 50),
        )

    def _detect_dependencies(self, tasks: List[Task]) -> List[Task]:
        # Simple rule: if there's an install task for a package that will be used by a later task, make later depend on install.
        name_to_task = {}
        for t in tasks:
            if t.tool == 'package_manager' and t.action == 'install' and t.params.get('package'):
                name_to_task[t.params['package'].lower()] = t.id

        for t in tasks:
            # example: if filesystem action references an executable name, depend on its install
            if t.tool == 'filesystem' and t.action.startswith('organize'):
                # no dependency
                continue
            # if browser download references package name? skip
            # generic heuristic: if param value contains a package name
            for v in t.params.values():
                if isinstance(v, str):
                    low = v.lower()
                    for pkg, tid in name_to_task.items():
                        if pkg in low:
                            if tid not in t.depends_on:
                                t.depends_on.append(tid)
                                # boost priority so install runs first
                                t.priority = max(t.priority, TOOL_PRIORITY.get('filesystem', 50))
        return tasks

    def validate_plan(self, plan: Plan) -> ValidationResult:
        errors: List[str] = []
        warnings: List[str] = []
        # check required params
        for t in plan.tasks:
            if t.tool == 'shell':
                if not t.params.get('command'):
                    errors.append(f"command required for task {t.id}")
            if t.tool == 'package_manager' and t.action in ('install', 'uninstall', 'search', 'show', 'upgrade'):
                if not t.params.get('package'):
                    errors.append(f"package required for task {t.id}")
            if t.tool == 'memory' and t.action in ('set', 'get', 'delete'):
                if not t.params.get('key'):
                    errors.append(f"key required for task {t.id}")
            if t.tool == 'memory' and t.action == 'set' and not t.params.get('value'):
                errors.append(f"value required for task {t.id}")
            if t.tool == 'memory' and t.action == 'upsert_entity':
                if not t.params.get('key') or not t.params.get('entity_type'):
                    errors.append(f"key and entity_type required for task {t.id}")
            if t.tool == 'filesystem' and t.action in ('read', 'delete', 'mkdir', 'organize_directory', 'detect_duplicates', 'clean_temp', 'find_files', 'list', 'stat'):
                if not t.params.get('path'):
                    errors.append(f"path required for task {t.id}")
            if t.tool == 'filesystem' and t.action == 'write':
                if not t.params.get('path'):
                    errors.append(f"path required for task {t.id}")
                if t.params.get('content') is None:
                    errors.append(f"content required for task {t.id}")
            if t.tool == 'filesystem' and t.action == 'move':
                if not t.params.get('path') or not t.params.get('dest'):
                    errors.append(f"move requires path and dest for task {t.id}")
            if t.tool == 'filesystem' and t.action == 'copy':
                if not t.params.get('path') or not t.params.get('dest'):
                    errors.append(f"copy requires path and dest for task {t.id}")
            if t.tool == 'filesystem' and t.action == 'find_files' and not t.params.get('pattern'):
                t.params['pattern'] = '*'
            if t.tool == 'filesystem' and t.action == 'create_structure':
                if not t.params.get('path') or not t.params.get('template'):
                    errors.append(f"create_structure requires path and template for task {t.id}")
            if t.tool == 'filesystem' and t.action == 'undo' and not t.params.get('request_id'):
                errors.append(f"undo requires request_id for task {t.id}")
            if t.tool == 'browser':
                if t.action in ('open_page', 'download', 'scrape_structured') and not t.params.get('url'):
                    errors.append(f"url required for task {t.id}")
                if t.action == 'search' and (not t.params.get('query') or not t.params.get('base_url') or not t.params.get('query_selector')):
                    errors.append(f"search requires query, base_url and query_selector for task {t.id}")
                if t.action == 'bridge_native_dialog' and not (t.params.get('title') or t.params.get('process_name')):
                    errors.append(f"title or process_name required for task {t.id}")
            if t.tool == 'web':
                if t.action == 'search_web' and not t.params.get('query'):
                    errors.append(f"query required for task {t.id}")
                if t.action in {'fetch_readonly', 'extract_links', 'check_url', 'download_verified'} and not t.params.get('url'):
                    errors.append(f"url required for task {t.id}")
            if t.tool == 'package_manager':
                if not t.params.get('manager'):
                    t.params['manager'] = 'choco'
            if t.tool == 'software_inventory':
                if t.action == 'resolve_command' and not t.params.get('command'):
                    errors.append(f"command required for task {t.id}")
                if t.action in {'search_installed', 'find_executable', 'find_install_location', 'find_uninstaller'} and not t.params.get('query'):
                    errors.append(f"query required for task {t.id}")
            if t.tool == 'env_manager':
                if t.action in {'get', 'set', 'unset'} and not t.params.get('name'):
                    errors.append(f"name required for task {t.id}")
                if t.action == 'set' and t.params.get('value') is None:
                    errors.append(f"value required for task {t.id}")
                if t.action in {'append_path', 'remove_path'} and not t.params.get('entry'):
                    errors.append(f"entry required for task {t.id}")
                if t.action == 'restore' and not t.params.get('backup_id'):
                    errors.append(f"backup_id required for task {t.id}")
            if t.tool == 'driver_manager':
                if t.action in {'device_status', 'find_driver_candidates', 'printer_status', 'printer_driver_info'} and not t.params.get('query'):
                    errors.append(f"query required for task {t.id}")
            if t.tool == 'desktop':
                if t.action in {'open_path', 'open_file'} and not t.params.get('path'):
                    errors.append(f"path required for task {t.id}")
                if t.action == 'open_app' and not (t.params.get('app_name') or t.params.get('app_path')):
                    errors.append(f"app_name or app_path required for task {t.id}")
                if t.action == 'wait_for_window' and not (t.params.get('title') or t.params.get('process_name') or t.params.get('hwnd')):
                    errors.append(f"title, process_name or hwnd required for task {t.id}")
                if t.action == 'clipboard_set' and not t.params.get('text'):
                    errors.append(f"text required for task {t.id}")
                if t.action in {'focus_window', 'close_window'} and not (t.params.get('title') or t.params.get('hwnd')):
                    errors.append(f"title or hwnd required for task {t.id}")
                if t.action == 'kill_process' and not (t.params.get('pid') or t.params.get('process_name') or t.params.get('title')):
                    errors.append(f"pid, process_name or title required for task {t.id}")
                if t.action == 'notify' and not t.params.get('message'):
                    errors.append(f"message required for task {t.id}")
                if t.action == 'service_action':
                    if not t.params.get('service_name') or not t.params.get('service_action'):
                        errors.append(f"service_action requires service_name and service_action for task {t.id}")
            if t.tool == 'windows_ui':
                if t.action in {'inspect_window', 'list_elements', 'find_element', 'wait_for_element', 'read_element_text'} and not (
                    t.params.get('title') or t.params.get('process_name') or t.params.get('hwnd')
                ):
                    errors.append(f"title, process_name or hwnd required for task {t.id}")
            if t.tool == 'share_discovery' and t.action == 'inspect_share' and not t.params.get('path'):
                errors.append(f"path required for task {t.id}")
            if t.tool == 'document_intelligence':
                if t.action in {'inspect_document', 'extract_text'} and not t.params.get('path'):
                    errors.append(f"path required for task {t.id}")
                if t.action == 'search_content' and (not t.params.get('root') or not t.params.get('query')):
                    errors.append(f"root and query required for task {t.id}")
            if t.tool == 'office':
                if t.action in {'open_document', 'export_pdf', 'list_workbook_sheets'} and not t.params.get('path'):
                    errors.append(f"path required for task {t.id}")
                if t.action == 'save_as_document' and (not t.params.get('path') or not t.params.get('output_path')):
                    errors.append(f"path and output_path required for task {t.id}")
                if t.action == 'reveal_active_document_path' and not t.params.get('app_name'):
                    errors.append(f"app_name required for task {t.id}")
        if not plan.tasks:
            errors.append("no supported tasks parsed from input")
        if errors:
            return ValidationResult(ok=False, errors=errors, warnings=warnings if warnings else None)
        return ValidationResult(ok=True, warnings=warnings if warnings else None)


# small compatibility node to integrate with graph
class PlannerNode(BaseModel):
    id: str = 'planner'

    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        text = state.get('inputs', {}).get('text') or state.get('inputs', {}).get('action', {}).get('text')
        if not text:
            raise ValueError('no text to plan')
        agent = PlannerAgent()
        plan, validation = await agent.parse(text)
        # attach to state
        state.setdefault('outputs', {})['plan'] = plan.dict()
        state.setdefault('outputs', {})['plan_validation'] = validation.dict()
        return {'plan': plan.dict(), 'validation': validation.dict()}

    async def validate(self, state: Dict[str, Any]) -> bool:
        return 'inputs' in state and (state['inputs'].get('text') or state['inputs'].get('action'))

    async def verify(self, state: Dict[str, Any], result: Dict[str, Any]) -> bool:
        # verification: plan produced and valid
        val = result.get('validation')
        return val and val.get('ok', False)


# quick CLI for manual testing
if __name__ == '__main__':
    import asyncio
    async def main():
        p = PlannerAgent()
        examples = [
            'Install vscode and organize downloads',
            'Install git, then clone repo and organize desktop',
            'Download https://example.com/file.zip and extract',
        ]
        for ex in examples:
            plan, v = await p.parse(ex)
            print('IN:', ex)
            print(plan.json(indent=2))
            print('VALID:', v.json())

    asyncio.run(main())
