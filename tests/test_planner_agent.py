import asyncio
import pytest
from planner_agent import PlannerAgent


@pytest.mark.asyncio
async def test_planner_parses_canonical_runtime_tools():
    agent = PlannerAgent()
    text = "install vscode and list processes and list windows"
    plan, v = await agent.parse(text)
    assert v.ok
    assert len(plan.tasks) == 3
    install_tasks = [t for t in plan.tasks if t.tool == 'package_manager' and t.action == 'install']
    assert any('vscode' in (t.params.get('package') or '') for t in install_tasks)
    os_tasks = [t for t in plan.tasks if t.tool == 'os' and t.action == 'processes']
    assert os_tasks
    desktop_tasks = [t for t in plan.tasks if t.tool == 'desktop' and t.action == 'list_windows']
    assert desktop_tasks


@pytest.mark.asyncio
async def test_planner_browser_search_defaults_to_google():
    agent = PlannerAgent()
    plan, validation = await agent.parse("search cursor agent docs")

    assert validation.ok is True
    task = plan.tasks[0]
    assert task.tool == "browser"
    assert task.action == "search"
    assert task.params["base_url"] == "https://www.google.com"
    assert task.params["query"] == "cursor agent docs"
