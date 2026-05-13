# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime


class BrowserRequest(BaseModel):
    url: Optional[str]
    actions: Optional[List[Dict[str, Any]]]
    timeout: Optional[int] = Field(30, gt=0)
    retries: Optional[int] = Field(2, ge=1)
    headless: Optional[bool] = True
    browser: Optional[str] = Field('chromium')  # chromium, firefox, webkit


class LoginCredentials(BaseModel):
    username: str
    password: str
    extra: Optional[Dict[str, Any]] = None


class DownloadInfo(BaseModel):
    url: str
    suggested_filename: Optional[str]
    path: Optional[str]
    size: Optional[int]
    finished_at: Optional[datetime]


class BrowserResponse(BaseModel):
    ok: bool
    url: Optional[str]
    title: Optional[str]
    content_snippet: Optional[str]
    console: List[str] = []
    downloads: List[DownloadInfo] = []
    screenshot_path: Optional[str]
    error: Optional[str]
