import asyncio
import pytest
from planner_agent import PlannerAgent


@pytest.mark.asyncio
async def test_planner_simple_install_and_organize():
    agent = PlannerAgent()
    text = "Install vscode and organize downloads"
    plan, v = await agent.parse(text)
    assert v.ok
    # expect two tasks
    assert len(plan.tasks) >= 2
    # find install task
    install_tasks = [t for t in plan.tasks if t.tool == 'package_manager' and t.action == 'install']
    assert any('vscode' in (t.params.get('package') or '') for t in install_tasks)
    # find organize downloads
    fs_tasks = [t for t in plan.tasks if t.tool == 'filesystem' and 'organize' in t.action]
    assert fs_tasks
