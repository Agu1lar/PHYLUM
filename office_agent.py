from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, Optional

try:  # pragma: no cover - optional dependency
    import pythoncom
    import win32com.client
except Exception:  # pragma: no cover - optional dependency
    pythoncom = None
    win32com = None


class OfficeComUnavailable(RuntimeError):
    pass


def _ensure_com() -> None:
    if pythoncom is None or win32com is None:
        raise OfficeComUnavailable("Office COM automation is unavailable in this environment.")


def _office_app_for_path(path: str):
    suffix = Path(path).suffix.lower()
    if suffix in {".doc", ".docx", ".rtf"}:
        return "Word.Application"
    if suffix in {".xls", ".xlsx", ".xlsm"}:
        return "Excel.Application"
    if suffix in {".ppt", ".pptx"}:
        return "PowerPoint.Application"
    raise ValueError(f"unsupported Office file type: {suffix}")


class OfficeAgent:
    def _dispatch(self, prog_id: str):
        _ensure_com()
        pythoncom.CoInitialize()
        return win32com.client.Dispatch(prog_id)

    async def open_document(self, path: str, visible: bool = True) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            prog_id = _office_app_for_path(path)
            app = self._dispatch(prog_id)
            app.Visible = visible
            if prog_id == "Word.Application":
                document = app.Documents.Open(str(Path(path).resolve()))
                return {"application": "word", "path": document.FullName, "name": document.Name}
            if prog_id == "Excel.Application":
                workbook = app.Workbooks.Open(str(Path(path).resolve()))
                return {"application": "excel", "path": workbook.FullName, "name": workbook.Name}
            presentation = app.Presentations.Open(str(Path(path).resolve()))
            return {"application": "powerpoint", "path": presentation.FullName, "name": presentation.Name}

        return await asyncio.to_thread(_run)

    async def export_pdf(self, path: str, output_path: Optional[str] = None) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            source = str(Path(path).resolve())
            destination = str(Path(output_path).resolve()) if output_path else str(Path(path).with_suffix(".pdf").resolve())
            prog_id = _office_app_for_path(path)
            app = self._dispatch(prog_id)
            app.Visible = False
            if prog_id == "Word.Application":
                document = app.Documents.Open(source)
                document.ExportAsFixedFormat(destination, 17)
                document.Close(False)
                app.Quit()
            elif prog_id == "Excel.Application":
                workbook = app.Workbooks.Open(source)
                workbook.ExportAsFixedFormat(0, destination)
                workbook.Close(False)
                app.Quit()
            else:
                presentation = app.Presentations.Open(source, WithWindow=False)
                presentation.SaveAs(destination, 32)
                presentation.Close()
                app.Quit()
            return {"source_path": source, "output_path": destination}

        return await asyncio.to_thread(_run)

    async def save_as_document(self, path: str, output_path: str) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            source = str(Path(path).resolve())
            destination = str(Path(output_path).resolve())
            prog_id = _office_app_for_path(path)
            app = self._dispatch(prog_id)
            app.Visible = False
            if prog_id == "Word.Application":
                document = app.Documents.Open(source)
                document.SaveAs2(destination)
                document.Close(False)
                app.Quit()
            elif prog_id == "Excel.Application":
                workbook = app.Workbooks.Open(source)
                workbook.SaveAs(destination)
                workbook.Close(False)
                app.Quit()
            else:
                presentation = app.Presentations.Open(source, WithWindow=False)
                presentation.SaveAs(destination)
                presentation.Close()
                app.Quit()
            return {"source_path": source, "output_path": destination}

        return await asyncio.to_thread(_run)

    async def list_workbook_sheets(self, path: str) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            workbook_path = str(Path(path).resolve())
            app = self._dispatch("Excel.Application")
            app.Visible = False
            workbook = app.Workbooks.Open(workbook_path)
            sheets = [sheet.Name for sheet in workbook.Worksheets]
            workbook.Close(False)
            app.Quit()
            return {"path": workbook_path, "sheets": sheets}

        return await asyncio.to_thread(_run)

    async def draft_email_with_attachment(
        self,
        *,
        to: Optional[str] = None,
        subject: Optional[str] = None,
        body: Optional[str] = None,
        attachment_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            app = self._dispatch("Outlook.Application")
            mail = app.CreateItem(0)
            if to:
                mail.To = to
            if subject:
                mail.Subject = subject
            if body:
                mail.Body = body
            if attachment_path:
                mail.Attachments.Add(str(Path(attachment_path).resolve()))
            mail.Display()
            return {"to": to, "subject": subject, "attachment_path": attachment_path}

        return await asyncio.to_thread(_run)

    async def reveal_active_document_path(self, app_name: str) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            lowered = app_name.lower()
            if "word" in lowered:
                app = self._dispatch("Word.Application")
                document = app.ActiveDocument
                return {"application": "word", "path": document.FullName, "name": document.Name}
            if "excel" in lowered:
                app = self._dispatch("Excel.Application")
                workbook = app.ActiveWorkbook
                return {"application": "excel", "path": workbook.FullName, "name": workbook.Name}
            if "powerpoint" in lowered:
                app = self._dispatch("PowerPoint.Application")
                presentation = app.ActivePresentation
                return {"application": "powerpoint", "path": presentation.FullName, "name": presentation.Name}
            raise ValueError(f"unsupported Office app: {app_name}")

        return await asyncio.to_thread(_run)

