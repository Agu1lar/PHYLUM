# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Tests for the persistent codebase map."""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from codebase_map import (
    CodebaseMap,
    CodebaseMapDB,
    ConfigInfo,
    FileEntry,
    ImportInfo,
    OwnershipInfo,
    PatternScanner,
    PythonScanner,
    RouteInfo,
    SymbolInfo,
    TestInfo,
    _detect_config_type,
    _detect_language,
    _file_hash,
    _should_ignore,
    parse_codeowners,
    resolve_owner,
)

# ---------------------------------------------------------------------------
# Sample code for testing
# ---------------------------------------------------------------------------

SAMPLE_PYTHON = '''\
"""Module docstring."""
import os
import json
from pathlib import Path
from typing import Any, Dict

CONSTANT_VALUE = 42
MAX_RETRIES: int = 3

class UserService:
    """Manages users."""

    def __init__(self, db):
        self.db = db

    def get_user(self, user_id: int) -> Dict[str, Any]:
        """Fetch a user by ID."""
        return self.db.find(user_id)

    async def create_user(self, name: str, email: str) -> Dict[str, Any]:
        return self.db.insert({"name": name, "email": email})


def helper_function(x, y):
    return x + y
'''

SAMPLE_FASTAPI = '''\
from fastapi import FastAPI

app = FastAPI()

@app.get("/users")
async def list_users():
    return []

@app.post("/users")
async def create_user(data: dict):
    return data

@app.get("/users/{user_id}")
async def get_user(user_id: int):
    return {"id": user_id}
'''

SAMPLE_TEST = '''\
import pytest
from user_service import UserService

class TestUserService:
    def test_get_user(self):
        pass

    def test_create_user(self):
        pass

def test_helper():
    pass
'''

SAMPLE_JS = '''\
import React from 'react';
import { useState, useEffect } from 'react';
const axios = require('axios');

export class UserComponent {
    render() {}
}

export function fetchUsers(page) {
    return axios.get('/api/users');
}

export const createUser = async (data) => {
    return axios.post('/api/users', data);
};

app.get('/api/health', (req, res) => res.json({ok: true}));
app.post('/api/login', handleLogin);

describe('UserComponent', () => {
    it('should render users', () => {});
    test('handles empty list', () => {});
});
'''


def _tmp_workspace():
    return Path(tempfile.mkdtemp(prefix="agente_test_cmap_"))


# ---------------------------------------------------------------------------
# Utility tests
# ---------------------------------------------------------------------------

class TestUtilities:
    def test_detect_language_python(self, tmp_path):
        assert _detect_language(tmp_path / "foo.py") == "python"

    def test_detect_language_js(self, tmp_path):
        assert _detect_language(tmp_path / "app.js") == "javascript"

    def test_detect_language_ts(self, tmp_path):
        assert _detect_language(tmp_path / "app.tsx") == "typescript"

    def test_detect_language_dockerfile(self, tmp_path):
        assert _detect_language(tmp_path / "Dockerfile") == "dockerfile"

    def test_detect_language_unknown(self, tmp_path):
        assert _detect_language(tmp_path / "file.xyz") == "unknown"

    def test_file_hash(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello", encoding="utf-8")
        h = _file_hash(f)
        assert isinstance(h, str) and len(h) == 16

    def test_file_hash_missing(self, tmp_path):
        assert _file_hash(tmp_path / "missing.txt") == ""

    def test_file_hash_changes(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello", encoding="utf-8")
        h1 = _file_hash(f)
        f.write_text("world", encoding="utf-8")
        h2 = _file_hash(f)
        assert h1 != h2

    def test_should_ignore_pycache(self, tmp_path):
        target = tmp_path / "__pycache__" / "foo.pyc"
        target.parent.mkdir()
        assert _should_ignore(target, tmp_path, ["__pycache__"])

    def test_should_ignore_node_modules(self, tmp_path):
        target = tmp_path / "node_modules" / "pkg" / "index.js"
        assert _should_ignore(target, tmp_path, ["node_modules"])

    def test_should_not_ignore_src(self, tmp_path):
        target = tmp_path / "src" / "main.py"
        assert not _should_ignore(target, tmp_path, ["__pycache__"])

    def test_detect_config_type(self, tmp_path):
        assert _detect_config_type(tmp_path / "package.json", tmp_path) == "package_json"
        assert _detect_config_type(tmp_path / "pyproject.toml", tmp_path) == "pyproject"
        assert _detect_config_type(tmp_path / "pytest.ini", tmp_path) == "pytest"
        assert _detect_config_type(tmp_path / "random.py", tmp_path) is None


# ---------------------------------------------------------------------------
# Python scanner
# ---------------------------------------------------------------------------

class TestPythonScanner:
    def setup_method(self):
        self.scanner = PythonScanner()

    def test_extract_imports(self, tmp_path):
        entry = self.scanner.scan(tmp_path / "mod.py", SAMPLE_PYTHON)
        modules = [i.module for i in entry.imports]
        assert "os" in modules
        assert "json" in modules
        assert "pathlib" in modules

    def test_extract_classes(self, tmp_path):
        entry = self.scanner.scan(tmp_path / "mod.py", SAMPLE_PYTHON)
        classes = [s for s in entry.symbols if s.kind == "class"]
        assert len(classes) == 1
        assert classes[0].name == "UserService"
        assert "Manages users" in classes[0].docstring

    def test_extract_methods(self, tmp_path):
        entry = self.scanner.scan(tmp_path / "mod.py", SAMPLE_PYTHON)
        methods = [s for s in entry.symbols if s.kind == "method"]
        names = {m.name for m in methods}
        assert "get_user" in names
        assert "create_user" in names
        assert "__init__" in names
        for m in methods:
            assert m.parent == "UserService"

    def test_extract_functions(self, tmp_path):
        entry = self.scanner.scan(tmp_path / "mod.py", SAMPLE_PYTHON)
        funcs = [s for s in entry.symbols if s.kind == "function"]
        assert any(f.name == "helper_function" for f in funcs)

    def test_extract_constants(self, tmp_path):
        entry = self.scanner.scan(tmp_path / "mod.py", SAMPLE_PYTHON)
        consts = [s for s in entry.symbols if s.kind == "constant"]
        assert any(c.name == "CONSTANT_VALUE" for c in consts)

    def test_extract_routes(self, tmp_path):
        entry = self.scanner.scan(tmp_path / "api.py", SAMPLE_FASTAPI)
        assert len(entry.routes) == 3
        paths = {r.path for r in entry.routes}
        assert "/users" in paths
        assert "/users/{user_id}" in paths
        methods = {r.method for r in entry.routes}
        assert "GET" in methods
        assert "POST" in methods

    def test_extract_tests(self, tmp_path):
        entry = self.scanner.scan(tmp_path / "test_user_service.py", SAMPLE_TEST)
        assert len(entry.tests) >= 3
        kinds = {t.kind for t in entry.tests}
        assert "test_class" in kinds
        assert "test_function" in kinds
        assert "test_method" in kinds
        assert all(t.target_module == "user_service" for t in entry.tests)

    def test_syntax_error_returns_empty(self, tmp_path):
        entry = self.scanner.scan(tmp_path / "bad.py", "def broken(:\n  pass")
        assert entry.symbols == []
        assert entry.imports == []

    def test_function_signature(self, tmp_path):
        entry = self.scanner.scan(tmp_path / "mod.py", SAMPLE_PYTHON)
        func = next(s for s in entry.symbols if s.name == "helper_function")
        assert "x" in func.signature
        assert "y" in func.signature

    def test_decorator_extraction(self, tmp_path):
        entry = self.scanner.scan(tmp_path / "api.py", SAMPLE_FASTAPI)
        routed = [s for s in entry.symbols if s.decorators]
        assert len(routed) >= 3


# ---------------------------------------------------------------------------
# JS/TS pattern scanner
# ---------------------------------------------------------------------------

class TestPatternScanner:
    def setup_method(self):
        self.scanner = PatternScanner()

    def test_js_symbols(self, tmp_path):
        entry = self.scanner.scan_js(SAMPLE_JS, tmp_path / "app.js")
        names = {s.name for s in entry.symbols}
        assert "UserComponent" in names
        assert "fetchUsers" in names
        assert "createUser" in names

    def test_js_imports(self, tmp_path):
        entry = self.scanner.scan_js(SAMPLE_JS, tmp_path / "app.js")
        modules = {i.module for i in entry.imports}
        assert "react" in modules
        assert "axios" in modules

    def test_js_routes(self, tmp_path):
        entry = self.scanner.scan_js(SAMPLE_JS, tmp_path / "app.js")
        assert len(entry.routes) >= 2
        paths = {r.path for r in entry.routes}
        assert "/api/health" in paths
        assert "/api/login" in paths

    def test_js_tests(self, tmp_path):
        entry = self.scanner.scan_js(SAMPLE_JS, tmp_path / "app.test.js")
        assert len(entry.tests) >= 2

    def test_config_package_json(self, tmp_path):
        source = json.dumps({"name": "my-app", "version": "1.0.0", "scripts": {}})
        configs = self.scanner.scan_config(tmp_path / "package.json", source, "package_json")
        assert len(configs) == 1
        assert "name" in configs[0].keys
        assert "scripts" in configs[0].keys

    def test_config_requirements(self, tmp_path):
        source = "flask==2.0\nrequests>=2.28\n# comment\npytest"
        configs = self.scanner.scan_config(tmp_path / "requirements.txt", source, "requirements")
        assert len(configs) == 1
        assert "flask" in configs[0].keys
        assert "pytest" in configs[0].keys

    def test_config_dotenv(self, tmp_path):
        source = "DB_HOST=localhost\nDB_PORT=5432\n# comment\nSECRET_KEY=abc"
        configs = self.scanner.scan_config(tmp_path / ".env", source, "dotenv")
        assert len(configs) == 1
        assert "DB_HOST" in configs[0].keys
        assert "SECRET_KEY" in configs[0].keys


# ---------------------------------------------------------------------------
# CODEOWNERS
# ---------------------------------------------------------------------------

class TestCodeowners:
    def test_parse_codeowners(self, tmp_path):
        co = tmp_path / "CODEOWNERS"
        co.write_text(
            "# comment\n"
            "*.py @backend-team\n"
            "*.js @frontend-team\n"
            "docs/ @docs-team\n",
            encoding="utf-8",
        )
        owners = parse_codeowners(tmp_path)
        assert owners["*.py"] == "@backend-team"
        assert owners["*.js"] == "@frontend-team"

    def test_parse_github_codeowners(self, tmp_path):
        gh = tmp_path / ".github"
        gh.mkdir()
        co = gh / "CODEOWNERS"
        co.write_text("*.rs @rust-team\n", encoding="utf-8")
        owners = parse_codeowners(tmp_path)
        assert owners["*.rs"] == "@rust-team"

    def test_resolve_owner_match(self):
        owners = {"*.py": "@backend", "tests/*": "@qa"}
        result = resolve_owner("src/main.py", owners)
        assert result is not None
        assert result.owner == "@backend"

    def test_resolve_owner_no_match(self):
        owners = {"*.py": "@backend"}
        result = resolve_owner("src/main.rs", owners)
        assert result is None


# ---------------------------------------------------------------------------
# SQLite DB
# ---------------------------------------------------------------------------

class TestCodebaseMapDB:
    def setup_method(self):
        self.tmpdir = _tmp_workspace()
        self.db = CodebaseMapDB(str(self.tmpdir / "test.db"))

    def teardown_method(self):
        self.db.close()
        shutil.rmtree(str(self.tmpdir), ignore_errors=True)

    def _sample_entry(self, path="src/main.py") -> FileEntry:
        return FileEntry(
            path=path, language="python", size=100,
            content_hash="abc123", last_scanned=1.0,
            symbols=[
                SymbolInfo(name="MyClass", kind="class", line=1, end_line=10, docstring="A class"),
                SymbolInfo(name="my_func", kind="function", line=12, end_line=15, signature="(x, y)"),
                SymbolInfo(name="my_method", kind="method", line=3, parent="MyClass"),
            ],
            imports=[
                ImportInfo(module="os", line=1),
                ImportInfo(module="json", names=["loads", "dumps"], line=2),
            ],
            routes=[
                RouteInfo(method="GET", path="/api/users", handler="list_users", line=5, framework="fastapi"),
            ],
            tests=[
                TestInfo(name="test_my_func", kind="test_function", line=1, target_module="main"),
            ],
            configs=[
                ConfigInfo(config_type="pyproject", keys=["tool", "project"]),
            ],
            ownership=[
                OwnershipInfo(owner="@team", pattern="*.py", source="codeowners"),
            ],
        )

    def test_upsert_and_get_file(self):
        entry = self._sample_entry()
        self.db.upsert_file(entry)
        result = self.db.get_file("src/main.py")
        assert result is not None
        assert result["path"] == "src/main.py"
        assert result["language"] == "python"
        assert len(result["symbols"]) == 3
        assert len(result["imports"]) == 2

    def test_get_file_hash(self):
        entry = self._sample_entry()
        self.db.upsert_file(entry)
        assert self.db.get_file_hash("src/main.py") == "abc123"
        assert self.db.get_file_hash("nonexistent") is None

    def test_remove_file(self):
        entry = self._sample_entry()
        self.db.upsert_file(entry)
        self.db.remove_file("src/main.py")
        assert self.db.get_file("src/main.py") is None
        assert self.db.find_symbol("MyClass") == []

    def test_find_symbol(self):
        self.db.upsert_file(self._sample_entry())
        results = self.db.find_symbol("MyClass")
        assert len(results) == 1
        assert results[0]["kind"] == "class"

    def test_find_symbol_by_kind(self):
        self.db.upsert_file(self._sample_entry())
        results = self.db.find_symbol("my", kind="function")
        assert len(results) == 1
        assert results[0]["name"] == "my_func"

    def test_find_imports_of(self):
        self.db.upsert_file(self._sample_entry())
        results = self.db.find_imports_of("json")
        assert len(results) == 1
        assert "loads" in results[0]["names"]

    def test_find_tests_for(self):
        self.db.upsert_file(self._sample_entry())
        results = self.db.find_tests_for("main")
        assert len(results) == 1
        assert results[0]["name"] == "test_my_func"

    def test_find_routes(self):
        self.db.upsert_file(self._sample_entry())
        results = self.db.find_routes("/api")
        assert len(results) == 1
        assert results[0]["method"] == "GET"

    def test_find_configs(self):
        self.db.upsert_file(self._sample_entry())
        results = self.db.find_configs("pyproject")
        assert len(results) == 1

    def test_find_owners(self):
        self.db.upsert_file(self._sample_entry())
        results = self.db.find_owners("@team")
        assert len(results) == 1

    def test_stats(self):
        self.db.upsert_file(self._sample_entry())
        s = self.db.stats()
        assert s["files"] == 1
        assert s["symbols"] == 3
        assert s["imports"] == 2
        assert s["routes"] == 1
        assert s["tests"] == 1

    def test_language_breakdown(self):
        self.db.upsert_file(self._sample_entry("a.py"))
        self.db.upsert_file(self._sample_entry("b.py"))
        breakdown = self.db.language_breakdown()
        assert breakdown["python"] == 2

    def test_all_paths(self):
        self.db.upsert_file(self._sample_entry("a.py"))
        self.db.upsert_file(self._sample_entry("b.py"))
        paths = self.db.all_paths()
        assert set(paths) == {"a.py", "b.py"}

    def test_meta(self):
        self.db.set_meta("version", "1.0")
        assert self.db.get_meta("version") == "1.0"
        assert self.db.get_meta("missing") is None

    def test_dependency_graph(self):
        self.db.upsert_file(self._sample_entry("src/main.py"))
        entry2 = self._sample_entry("src/other.py")
        entry2.imports = [ImportInfo(module="main", line=1)]
        self.db.upsert_file(entry2)
        graph = self.db.dependency_graph("src/main.py")
        assert graph["file"] == "src/main.py"
        assert len(graph["imports"]) == 2
        assert len(graph["imported_by"]) >= 1

    def test_upsert_replaces_old_data(self):
        entry = self._sample_entry()
        self.db.upsert_file(entry)
        assert len(self.db.find_symbol("MyClass")) == 1
        entry.symbols = [SymbolInfo(name="NewClass", kind="class", line=1)]
        entry.content_hash = "new_hash"
        self.db.upsert_file(entry)
        assert len(self.db.find_symbol("MyClass")) == 0
        assert len(self.db.find_symbol("NewClass")) == 1


# ---------------------------------------------------------------------------
# CodebaseMap (full integration)
# ---------------------------------------------------------------------------

class TestCodebaseMap:
    def setup_method(self):
        self.ws = _tmp_workspace()
        self.db_path = str(self.ws / ".agente" / "map.db")
        self._write_files()
        self.cmap = CodebaseMap(str(self.ws), db_path=self.db_path)

    def teardown_method(self):
        self.cmap.close()
        shutil.rmtree(str(self.ws), ignore_errors=True)

    def _write_files(self):
        (self.ws / "src").mkdir()
        (self.ws / "tests").mkdir()
        (self.ws / "src" / "main.py").write_text(SAMPLE_PYTHON, encoding="utf-8")
        (self.ws / "src" / "api.py").write_text(SAMPLE_FASTAPI, encoding="utf-8")
        (self.ws / "tests" / "test_main.py").write_text(SAMPLE_TEST, encoding="utf-8")
        (self.ws / "package.json").write_text(
            json.dumps({"name": "test", "version": "1.0.0"}), encoding="utf-8",
        )
        (self.ws / "requirements.txt").write_text("flask==2.0\npytest\n", encoding="utf-8")

    def test_scan_returns_summary(self):
        result = self.cmap.scan()
        assert result["new"] >= 3
        assert result["errors"] == 0
        assert result["elapsed_seconds"] >= 0

    def test_incremental_scan(self):
        result1 = self.cmap.scan()
        result2 = self.cmap.scan()
        assert result2["new"] == 0
        assert result2["updated"] == 0
        assert result2["unchanged"] == result1["new"]

    def test_scan_detects_changes(self):
        self.cmap.scan()
        (self.ws / "src" / "main.py").write_text("# changed\n" + SAMPLE_PYTHON, encoding="utf-8")
        result = self.cmap.scan()
        assert result["updated"] == 1

    def test_scan_detects_removals(self):
        self.cmap.scan()
        (self.ws / "src" / "api.py").unlink()
        result = self.cmap.scan()
        assert result["removed"] == 1

    def test_force_scan(self):
        self.cmap.scan()
        result = self.cmap.scan(force=True)
        assert result["updated"] >= 3

    def test_find_symbol(self):
        self.cmap.scan()
        results = self.cmap.find_symbol("UserService")
        assert len(results) >= 1
        assert results[0]["kind"] == "class"

    def test_find_imports(self):
        self.cmap.scan()
        results = self.cmap.find_imports("os")
        assert len(results) >= 1

    def test_find_tests_for(self):
        self.cmap.scan()
        results = self.cmap.find_tests_for("main")
        assert any("test_main" in r["file"] for r in results)

    def test_find_routes(self):
        self.cmap.scan()
        results = self.cmap.find_routes("/users")
        assert len(results) >= 1

    def test_find_configs(self):
        self.cmap.scan()
        results = self.cmap.find_configs("package_json")
        assert len(results) >= 1

    def test_stats(self):
        self.cmap.scan()
        s = self.cmap.stats()
        assert s["files"] >= 3
        assert s["symbols"] >= 5
        assert "python" in s["languages"]
        assert s["workspace_root"] == str(self.ws)

    def test_get_file(self):
        self.cmap.scan()
        result = self.cmap.get_file("src/main.py")
        assert result is not None
        assert result["language"] == "python"
        assert len(result["symbols"]) >= 3

    def test_dependency_graph(self):
        self.cmap.scan()
        graph = self.cmap.dependency_graph("src/main.py")
        assert graph["file"] == "src/main.py"
        assert "imports" in graph

    def test_search(self):
        self.cmap.scan()
        results = self.cmap.search("UserService")
        assert len(results["symbols"]) >= 1

    def test_ignores_pycache(self):
        cache_dir = self.ws / "__pycache__"
        cache_dir.mkdir()
        (cache_dir / "foo.pyc").write_text("cached", encoding="utf-8")
        self.cmap.scan()
        assert self.cmap.get_file("__pycache__/foo.pyc") is None

    def test_codeowners_integration(self):
        co = self.ws / "CODEOWNERS"
        co.write_text("*.py @python-team\n", encoding="utf-8")
        self.cmap._codeowners = None
        self.cmap.scan(force=True)
        owners = self.cmap.find_owners("@python-team")
        assert len(owners) >= 1


# ---------------------------------------------------------------------------
# Data model serialization
# ---------------------------------------------------------------------------

class TestDataModels:
    def test_symbol_to_dict(self):
        s = SymbolInfo(name="foo", kind="function", line=1, signature="(x)")
        d = s.to_dict()
        assert d["name"] == "foo"
        assert d["signature"] == "(x)"

    def test_import_to_dict(self):
        i = ImportInfo(module="os", names=["path"], line=1)
        d = i.to_dict()
        assert d["module"] == "os"
        assert d["names"] == ["path"]

    def test_route_to_dict(self):
        r = RouteInfo(method="GET", path="/api", handler="index", line=1)
        d = r.to_dict()
        assert d["method"] == "GET"

    def test_test_to_dict(self):
        t = TestInfo(name="test_foo", kind="test_function", line=1, target_module="foo")
        d = t.to_dict()
        assert d["target_module"] == "foo"

    def test_config_to_dict(self):
        c = ConfigInfo(config_type="pyproject", keys=["tool"])
        d = c.to_dict()
        assert d["config_type"] == "pyproject"

    def test_ownership_to_dict(self):
        o = OwnershipInfo(owner="@team", pattern="*.py", source="codeowners")
        d = o.to_dict()
        assert d["owner"] == "@team"

    def test_file_entry_to_dict(self):
        e = FileEntry(
            path="test.py", language="python", size=100,
            content_hash="abc", last_scanned=1.0,
            symbols=[SymbolInfo(name="X", kind="class", line=1)],
        )
        d = e.to_dict()
        assert d["path"] == "test.py"
        assert len(d["symbols"]) == 1


# ---------------------------------------------------------------------------
# Canonical tools integration
# ---------------------------------------------------------------------------

class TestCanonicalIntegration:
    def test_codebase_map_in_supported_tools(self):
        from canonical_tools import supported_tools
        assert "codebase_map" in supported_tools()

    def test_action_metadata_exists(self):
        from canonical_tools import action_metadata
        meta = action_metadata("codebase_map", "scan")
        assert meta["semantic_type"] == "inspection"
        assert meta["mutates_state"] is False

    def test_task_title(self):
        from canonical_tools import task_title
        title = task_title("codebase_map", "find_symbol", {"name": "UserService"})
        assert "UserService" in title

    def test_tool_definitions_include_codebase_map(self):
        from canonical_tools import tool_definitions
        defs = tool_definitions()
        names = [d["function"]["name"] for d in defs]
        assert "codebase_map" in names

    def test_normalize_agentic_task(self):
        from canonical_tools import normalize_agentic_task
        task = normalize_agentic_task(
            "codebase_map",
            {"action": "find_symbol", "name": "MyClass"},
            task_id="test-1",
        )
        assert task["tool"] == "codebase_map"
        assert task["action"] == "find_symbol"
        assert task["params"]["name"] == "MyClass"


class TestToolRegistry:
    def test_registry_has_codebase_map(self):
        from tool_registry import ToolRegistry
        registry = ToolRegistry()
        assert "codebase_map" in registry.tools
        assert registry.supports("codebase_map")
