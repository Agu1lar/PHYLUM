# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

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

    async def word_find_text(self, path: str, query: str, limit: int = 20) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            document_path = str(Path(path).resolve())
            app = self._dispatch("Word.Application")
            app.Visible = False
            document = app.Documents.Open(document_path)
            text = document.Content.Text
            matches: List[Dict[str, Any]] = []
            lowered = text.lower()
            needle = query.lower()
            start = 0
            while needle and len(matches) < limit:
                idx = lowered.find(needle, start)
                if idx < 0:
                    break
                excerpt = text[max(idx - 120, 0): idx + len(query) + 180]
                matches.append({"offset": idx, "excerpt": excerpt.strip()})
                start = idx + len(query)
            document.Close(False)
            app.Quit()
            return {"path": document_path, "query": query, "matches": matches}

        return await asyncio.to_thread(_run)

    async def excel_read_range(self, path: str, sheet_name: Optional[str] = None, range_address: str = "A1:Z50") -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            workbook_path = str(Path(path).resolve())
            app = self._dispatch("Excel.Application")
            app.Visible = False
            workbook = app.Workbooks.Open(workbook_path)
            sheet = workbook.Worksheets(sheet_name) if sheet_name else workbook.ActiveSheet
            values = sheet.Range(range_address).Value
            workbook.Close(False)
            app.Quit()
            rows = values if isinstance(values, tuple) else ((values,),)
            return {"path": workbook_path, "sheet": sheet_name or sheet.Name, "range": range_address, "values": rows}

        return await asyncio.to_thread(_run)

    async def outlook_read_latest(self, limit: int = 10, folder: str = "inbox") -> Dict[str, Any]:
        """Read the N most recent emails from Outlook. Works in background without Outlook being visible."""
        def _run() -> Dict[str, Any]:
            app = self._dispatch("Outlook.Application")
            namespace = app.GetNamespace("MAPI")
            folder_id = {"inbox": 6, "sent": 5, "drafts": 16, "outbox": 4}.get(folder.lower(), 6)
            target = namespace.GetDefaultFolder(folder_id)
            items = target.Items
            items.Sort("[ReceivedTime]", True)
            messages: List[Dict[str, Any]] = []
            count = 0
            for item in items:
                if count >= limit:
                    break
                try:
                    attachments = []
                    try:
                        for idx in range(1, item.Attachments.Count + 1):
                            attachments.append(item.Attachments.Item(idx).FileName)
                    except Exception:
                        pass
                    body_text = getattr(item, "Body", "") or ""
                    messages.append({
                        "subject": getattr(item, "Subject", ""),
                        "sender": getattr(item, "SenderName", ""),
                        "sender_email": getattr(item, "SenderEmailAddress", ""),
                        "to": getattr(item, "To", ""),
                        "received_time": str(getattr(item, "ReceivedTime", "")),
                        "body": body_text[:3000],
                        "attachments": attachments,
                    })
                    count += 1
                except Exception:
                    continue
            return {"folder": folder, "count": len(messages), "messages": messages}

        return await asyncio.to_thread(_run)

    async def word_create_document(
        self,
        content: str,
        output_path: str,
        *,
        title: Optional[str] = None,
        visible: bool = False,
    ) -> Dict[str, Any]:
        """Create a new Word document with the given text content. Works headlessly."""
        def _run() -> Dict[str, Any]:
            destination = str(Path(output_path).resolve())
            app = self._dispatch("Word.Application")
            app.Visible = visible
            doc = app.Documents.Add()
            if title:
                doc.Content.Text = f"{title}\n\n{content}"
            else:
                doc.Content.Text = content
            doc.SaveAs2(destination)
            doc.Close(False)
            if not visible:
                app.Quit()
            return {"path": destination, "title": title}

        return await asyncio.to_thread(_run)

    async def outlook_search_messages(self, query: str, limit: int = 25) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            app = self._dispatch("Outlook.Application")
            namespace = app.GetNamespace("MAPI")
            inbox = namespace.GetDefaultFolder(6)
            items = inbox.Items
            items.Sort("[ReceivedTime]", True)
            matches: List[Dict[str, Any]] = []
            lowered_query = query.lower()
            for item in items:
                try:
                    haystack = f"{getattr(item, 'Subject', '')}\n{getattr(item, 'SenderName', '')}\n{getattr(item, 'Body', '')}".lower()
                    if lowered_query in haystack:
                        attachments = []
                        for idx in range(1, item.Attachments.Count + 1):
                            attachments.append(item.Attachments.Item(idx).FileName)
                        matches.append(
                            {
                                "subject": item.Subject,
                                "sender": item.SenderName,
                                "received_time": str(item.ReceivedTime),
                                "attachments": attachments,
                            }
                        )
                except Exception:
                    continue
                if len(matches) >= limit:
                    break
            return {"query": query, "matches": matches}

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

