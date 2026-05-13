import pytest

from canonical_tools import action_metadata, tool_schema_by_name
from desktop_windows_agent import DesktopWindowsAgent
from planner_agent import PlannerAgent
from windows_ui_agent import WindowsUiAgent


@pytest.mark.asyncio
async def test_explorer_file_adapters_rename_copy_move_and_inspect_installer(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("hello", encoding="utf-8")
    copy_dest = tmp_path / "copies"
    move_dest = tmp_path / "moved"
    copy_dest.mkdir()
    move_dest.mkdir()
    installer = tmp_path / "setup.msi"
    installer.write_bytes(b"fake-msi")

    agent = DesktopWindowsAgent()

    renamed = await agent.explorer_rename_path(str(source), "renamed.txt")
    renamed_path = tmp_path / "renamed.txt"
    assert renamed["output_path"] == str(renamed_path)
    assert renamed_path.exists()

    copied = await agent.explorer_copy_path(str(renamed_path), str(copy_dest))
    assert copied["output_path"] == str(copy_dest / "renamed.txt")
    assert (copy_dest / "renamed.txt").read_text(encoding="utf-8") == "hello"

    moved = await agent.explorer_move_path(str(renamed_path), str(move_dest))
    assert moved["output_path"] == str(move_dest / "renamed.txt")
    assert not renamed_path.exists()
    assert (move_dest / "renamed.txt").exists()

    inspected = await agent.inspect_installer(str(installer))
    assert inspected["installer_type"] == "msi"
    assert inspected["setup_guidance"]["needs_admin_likely"] is True


def test_dialog_classifier_covers_print_file_auth_and_setup():
    agent = WindowsUiAgent()

    print_profile = agent._classify_dialog({"title": "Print"}, [{"title": "Printer"}, {"title": "Copies"}])
    file_profile = agent._classify_dialog({"title": "Save As"}, [{"title": "File name"}, {"title": "Open"}])
    auth_profile = agent._classify_dialog({"title": "Sign in"}, [{"title": "Username"}, {"title": "Password"}])
    setup_profile = agent._classify_dialog({"title": "Setup"}, [{"title": "License"}, {"title": "Install"}])

    assert print_profile["kind"] == "print_dialog"
    assert file_profile["kind"] == "file_picker"
    assert auth_profile["kind"] == "auth_popup"
    assert setup_profile["kind"] == "installer_setup"


def test_phase3_actions_are_exposed_in_canonical_schemas():
    desktop_actions = set(tool_schema_by_name("desktop")["function"]["parameters"]["properties"]["action"]["enum"])
    windows_actions = set(tool_schema_by_name("windows_ui")["function"]["parameters"]["properties"]["action"]["enum"])
    driver_actions = set(tool_schema_by_name("driver_manager")["function"]["parameters"]["properties"]["action"]["enum"])
    share_actions = set(tool_schema_by_name("share_discovery")["function"]["parameters"]["properties"]["action"]["enum"])
    office_actions = set(tool_schema_by_name("office")["function"]["parameters"]["properties"]["action"]["enum"])

    assert {"explorer_context", "explorer_rename_path", "inspect_installer"}.issubset(desktop_actions)
    assert "inspect_dialog" in windows_actions
    assert "printer_diagnostics" in driver_actions
    assert "inspect_corporate_share" in share_actions
    assert {"word_find_text", "excel_read_range", "outlook_search_messages"}.issubset(office_actions)
    assert action_metadata("desktop", "explorer_move_path")["approval_mode"] == "single"
    assert action_metadata("windows_ui", "inspect_dialog")["approval_mode"] == "none"


@pytest.mark.asyncio
async def test_planner_routes_phase3_domain_actions(tmp_path):
    installer = tmp_path / "setup.msi"
    installer.write_bytes(b"fake")
    doc = tmp_path / "doc.docx"
    doc.write_bytes(b"fake")

    installer_plan, installer_validation = await PlannerAgent().parse(f"inspect installer {installer}")
    assert installer_validation.ok
    assert installer_plan.tasks[0].tool == "desktop"
    assert installer_plan.tasks[0].action == "inspect_installer"

    office_plan, office_validation = await PlannerAgent().parse(f"word find text contrato {doc}")
    assert office_validation.ok
    assert office_plan.tasks[0].tool == "office"
    assert office_plan.tasks[0].action == "word_find_text"
    assert office_plan.tasks[0].params["query"] == "contrato"
