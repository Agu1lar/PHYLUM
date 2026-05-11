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

from planner_models import Task, Plan, ValidationResult

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Simple grammar: map verbs/keywords to tools/actions
TOOL_KEYWORDS = {
    'install': {'tool': 'package_manager', 'action': 'install'},
    'uninstall': {'tool': 'package_manager', 'action': 'uninstall'},
    'organize downloads': {'tool': 'filesystem', 'action': 'organize_downloads'},
    'organize desktop': {'tool': 'filesystem', 'action': 'organize_desktop'},
    'organize': {'tool': 'filesystem', 'action': 'organize_directory'},
    'cleanup temp': {'tool': 'filesystem', 'action': 'clean_temp'},
    'clean': {'tool': 'filesystem', 'action': 'clean_temp'},
    'find duplicates': {'tool': 'filesystem', 'action': 'detect_duplicates'},
    'download': {'tool': 'browser', 'action': 'download'},
    'open': {'tool': 'browser', 'action': 'open_page'},
    'search': {'tool': 'browser', 'action': 'search'},
    'scrape': {'tool': 'browser', 'action': 'scrape_structured'},
    'move': {'tool': 'filesystem', 'action': 'move'},
    'create': {'tool': 'filesystem', 'action': 'create_structure'},
}

# Priority by tool importance (lower number = higher priority)
TOOL_PRIORITY = {
    'package_manager': 10,
    'filesystem': 30,
    'browser': 40,
    'memory': 50,
    'os': 5,
}


class PlannerAgent:
    def __init__(self, *, supported_tools: Optional[List[str]] = None):
        self.supported_tools = set(supported_tools or ['package_manager', 'filesystem', 'browser', 'memory', 'os'])

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
        # examples: 'install vscode', 'download https://...' 'move photos to Pictures'
        if action in ('install', 'uninstall'):
            # try to pick token after keyword
            m = re.search(rf"{re.escape(kw)}\s+(?P<name>[\w\-\.]+)", part)
            if m:
                params['package'] = m.group('name')
        if action == 'download':
            m = re.search(r"(https?://\S+)", part)
            if m:
                params['url'] = m.group(1)
        if action == 'move':
            # move X to Y
            m = re.search(r"move\s+(?P<src>\S+)\s+to\s+(?P<dst>\S+)", part)
            if m:
                params['src'] = m.group('src')
                params['dest'] = m.group('dst')
        return params

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
            if t.tool == 'package_manager' and t.action in ('install', 'uninstall'):
                if not t.params.get('package'):
                    errors.append(f"package required for task {t.id}")
            if t.tool == 'filesystem' and t.action == 'move':
                if not t.params.get('src') or not t.params.get('dest'):
                    errors.append(f"move requires src and dest for task {t.id}")
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
