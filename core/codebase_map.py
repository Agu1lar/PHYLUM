# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Persistent codebase map — symbols, imports, routes, tests, configs and ownership per file.

Scans a workspace directory and builds a structured, queryable map of the
codebase.  The map is persisted to SQLite so incremental updates only rescan
files whose content hash has changed.

Supported extractions:
- **Symbols**: classes, functions, constants, type aliases (Python AST)
- **Imports**: module-level import statements with alias resolution
- **Routes**: HTTP route decorators (Flask, FastAPI, Express-style)
- **Tests**: test functions/classes and the modules they target
- **Configs**: recognised config files and their key structure
- **Ownership**: CODEOWNERS-style mapping and git blame heuristic
"""
from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class SymbolInfo:
    name: str
    kind: str  # class, function, method, constant, type_alias, variable
    line: int
    end_line: int = 0
    docstring: str = ""
    decorators: List[str] = field(default_factory=list)
    parent: str = ""  # for methods: the class name
    signature: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "kind": self.kind, "line": self.line,
            "end_line": self.end_line, "docstring": self.docstring,
            "decorators": self.decorators, "parent": self.parent,
            "signature": self.signature,
        }


@dataclass
class ImportInfo:
    module: str
    names: List[str] = field(default_factory=list)  # imported names (empty = import module)
    alias: str = ""
    line: int = 0
    is_relative: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "module": self.module, "names": self.names, "alias": self.alias,
            "line": self.line, "is_relative": self.is_relative,
        }


@dataclass
class RouteInfo:
    method: str  # GET, POST, etc.
    path: str
    handler: str
    line: int = 0
    framework: str = ""  # flask, fastapi, express

    def to_dict(self) -> Dict[str, Any]:
        return {
            "method": self.method, "path": self.path, "handler": self.handler,
            "line": self.line, "framework": self.framework,
        }


@dataclass
class TestInfo:
    name: str
    kind: str  # test_function, test_class, test_method
    line: int = 0
    target_module: str = ""  # inferred module under test

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "kind": self.kind, "line": self.line,
            "target_module": self.target_module,
        }


@dataclass
class ConfigInfo:
    config_type: str  # pyproject, package_json, dockerfile, env, yaml, ini, etc.
    keys: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"config_type": self.config_type, "keys": self.keys}


@dataclass
class OwnershipInfo:
    owner: str
    pattern: str = ""
    source: str = ""  # codeowners, git_blame, manual

    def to_dict(self) -> Dict[str, Any]:
        return {"owner": self.owner, "pattern": self.pattern, "source": self.source}


@dataclass
class FileEntry:
    path: str  # relative to workspace root
    language: str
    size: int
    content_hash: str
    last_scanned: float
    symbols: List[SymbolInfo] = field(default_factory=list)
    imports: List[ImportInfo] = field(default_factory=list)
    routes: List[RouteInfo] = field(default_factory=list)
    tests: List[TestInfo] = field(default_factory=list)
    configs: List[ConfigInfo] = field(default_factory=list)
    ownership: List[OwnershipInfo] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path, "language": self.language,
            "size": self.size, "content_hash": self.content_hash,
            "last_scanned": self.last_scanned,
            "symbols": [s.to_dict() for s in self.symbols],
            "imports": [i.to_dict() for i in self.imports],
            "routes": [r.to_dict() for r in self.routes],
            "tests": [t.to_dict() for t in self.tests],
            "configs": [c.to_dict() for c in self.configs],
            "ownership": [o.to_dict() for o in self.ownership],
        }


# ---------------------------------------------------------------------------
# Default ignore patterns
# ---------------------------------------------------------------------------

DEFAULT_IGNORE_PATTERNS: List[str] = [
    "__pycache__", "node_modules", ".git", ".hg", ".svn",
    ".venv", "venv", "env", ".env", ".tox", ".mypy_cache",
    ".pytest_cache", "dist", "build", "*.egg-info",
    ".next", ".nuxt", "coverage", ".coverage",
]

LANGUAGE_EXTENSIONS: Dict[str, List[str]] = {
    "python": [".py"],
    "javascript": [".js", ".jsx", ".mjs", ".cjs"],
    "typescript": [".ts", ".tsx"],
    "json": [".json"],
    "yaml": [".yml", ".yaml"],
    "toml": [".toml"],
    "ini": [".ini", ".cfg"],
    "markdown": [".md"],
    "dockerfile": [],
    "shell": [".sh", ".bash", ".ps1"],
    "html": [".html", ".htm"],
    "css": [".css", ".scss", ".less"],
    "sql": [".sql"],
    "rust": [".rs"],
    "go": [".go"],
    "java": [".java"],
    "csharp": [".cs"],
    "powershell": [".ps1", ".psm1"],
}

CONFIG_FILE_PATTERNS: Dict[str, str] = {
    "pyproject.toml": "pyproject",
    "setup.cfg": "setuptools",
    "setup.py": "setuptools",
    "package.json": "package_json",
    "tsconfig.json": "tsconfig",
    ".eslintrc*": "eslint",
    ".prettierrc*": "prettier",
    "Dockerfile": "dockerfile",
    "docker-compose*.yml": "docker_compose",
    "docker-compose*.yaml": "docker_compose",
    ".env*": "dotenv",
    "requirements*.txt": "requirements",
    "Pipfile": "pipfile",
    "Cargo.toml": "cargo",
    "go.mod": "go_mod",
    "pytest.ini": "pytest",
    "tox.ini": "tox",
    "Makefile": "makefile",
    ".github/workflows/*.yml": "github_actions",
    ".github/workflows/*.yaml": "github_actions",
}


# ---------------------------------------------------------------------------
# File scanner
# ---------------------------------------------------------------------------

def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    try:
        h.update(path.read_bytes())
    except (OSError, PermissionError):
        return ""
    return h.hexdigest()[:16]


def _detect_language(path: Path) -> str:
    name = path.name.lower()
    if name == "dockerfile" or name.startswith("dockerfile."):
        return "dockerfile"
    if name == "makefile":
        return "makefile"
    ext = path.suffix.lower()
    for lang, exts in LANGUAGE_EXTENSIONS.items():
        if ext in exts:
            return lang
    return "unknown"


def _detect_config_type(path: Path, workspace_root: Path) -> Optional[str]:
    rel = path.relative_to(workspace_root).as_posix()
    name = path.name
    for pattern, config_type in CONFIG_FILE_PATTERNS.items():
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(rel, pattern):
            return config_type
    return None


def _should_ignore(path: Path, workspace_root: Path, ignore_patterns: List[str]) -> bool:
    rel_parts = path.relative_to(workspace_root).parts
    for part in rel_parts:
        for pattern in ignore_patterns:
            if fnmatch.fnmatch(part, pattern):
                return True
    return False


# ---------------------------------------------------------------------------
# Python AST scanner
# ---------------------------------------------------------------------------

class PythonScanner:
    """Extracts symbols, imports, routes and tests from Python files via AST."""

    ROUTE_DECORATORS = re.compile(
        r"(route|get|post|put|delete|patch|head|options|api_view|app\.route|router\.\w+)"
    )

    def scan(self, path: Path, source: str) -> FileEntry:
        entry = FileEntry(
            path="", language="python", size=len(source),
            content_hash="", last_scanned=time.time(),
        )
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            return entry

        entry.imports = self._extract_imports(tree)
        entry.symbols = self._extract_symbols(tree, source)
        entry.routes = self._extract_routes(tree, source)
        entry.tests = self._extract_tests(tree, path)
        return entry

    def _extract_imports(self, tree: ast.Module) -> List[ImportInfo]:
        imports: List[ImportInfo] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(ImportInfo(
                        module=alias.name,
                        alias=alias.asname or "",
                        line=node.lineno,
                    ))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = [a.name for a in node.names]
                imports.append(ImportInfo(
                    module=module,
                    names=names,
                    line=node.lineno,
                    is_relative=bool(node.level and node.level > 0),
                ))
        return imports

    def _extract_symbols(self, tree: ast.Module, source: str) -> List[SymbolInfo]:
        symbols: List[SymbolInfo] = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append(self._function_symbol(node))
            elif isinstance(node, ast.ClassDef):
                symbols.append(self._class_symbol(node))
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        sym = self._function_symbol(child)
                        sym.kind = "method"
                        sym.parent = node.name
                        symbols.append(sym)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        symbols.append(SymbolInfo(
                            name=target.id, kind="constant",
                            line=node.lineno, end_line=node.end_lineno or node.lineno,
                        ))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                name = node.target.id
                if name[0].isupper() and node.value:
                    symbols.append(SymbolInfo(
                        name=name, kind="type_alias",
                        line=node.lineno, end_line=node.end_lineno or node.lineno,
                    ))
        return symbols

    def _function_symbol(self, node) -> SymbolInfo:
        args = []
        for arg in node.args.args:
            args.append(arg.arg)
        sig = f"({', '.join(args)})"
        decorators = [self._decorator_name(d) for d in node.decorator_list]
        docstring = ast.get_docstring(node) or ""
        return SymbolInfo(
            name=node.name, kind="function",
            line=node.lineno, end_line=node.end_lineno or node.lineno,
            docstring=docstring[:200], decorators=decorators,
            signature=sig,
        )

    def _class_symbol(self, node: ast.ClassDef) -> SymbolInfo:
        bases = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                bases.append(base.id)
            elif isinstance(base, ast.Attribute):
                bases.append(ast.dump(base))
        sig = f"({', '.join(bases)})" if bases else ""
        decorators = [self._decorator_name(d) for d in node.decorator_list]
        docstring = ast.get_docstring(node) or ""
        return SymbolInfo(
            name=node.name, kind="class",
            line=node.lineno, end_line=node.end_lineno or node.lineno,
            docstring=docstring[:200], decorators=decorators,
            signature=sig,
        )

    @staticmethod
    def _decorator_name(node) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return f"{ast.dump(node)}"
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                return func.id
            if isinstance(func, ast.Attribute):
                parts = []
                current = func
                while isinstance(current, ast.Attribute):
                    parts.append(current.attr)
                    current = current.value
                if isinstance(current, ast.Name):
                    parts.append(current.id)
                return ".".join(reversed(parts))
        return ""

    def _extract_routes(self, tree: ast.Module, source: str) -> List[RouteInfo]:
        routes: List[RouteInfo] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                route = self._parse_route_decorator(dec, node.name, node.lineno)
                if route:
                    routes.append(route)
        return routes

    def _parse_route_decorator(self, dec, handler_name: str, line: int) -> Optional[RouteInfo]:
        if isinstance(dec, ast.Call):
            func = dec.func
            method = "ANY"
            framework = ""
            if isinstance(func, ast.Attribute):
                attr = func.attr.lower()
                if attr in ("get", "post", "put", "delete", "patch", "head", "options"):
                    method = attr.upper()
                    framework = "fastapi"
                elif attr == "route":
                    framework = "flask"
                else:
                    return None
            elif isinstance(func, ast.Name) and func.id.lower() in ("route", "api_view"):
                framework = "django"
            else:
                return None

            path = ""
            if dec.args:
                first_arg = dec.args[0]
                if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                    path = first_arg.value
            return RouteInfo(
                method=method, path=path, handler=handler_name,
                line=line, framework=framework,
            )
        return None

    def _extract_tests(self, tree: ast.Module, path: Path) -> List[TestInfo]:
        tests: List[TestInfo] = []
        filename = path.stem
        target_module = self._infer_target_module(filename)

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_"):
                    tests.append(TestInfo(
                        name=node.name, kind="test_function",
                        line=node.lineno, target_module=target_module,
                    ))
            elif isinstance(node, ast.ClassDef):
                if node.name.startswith("Test"):
                    tests.append(TestInfo(
                        name=node.name, kind="test_class",
                        line=node.lineno, target_module=target_module,
                    ))
                    for child in ast.iter_child_nodes(node):
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            if child.name.startswith("test_"):
                                tests.append(TestInfo(
                                    name=f"{node.name}.{child.name}",
                                    kind="test_method",
                                    line=child.lineno,
                                    target_module=target_module,
                                ))
        return tests

    @staticmethod
    def _infer_target_module(test_filename: str) -> str:
        if test_filename.startswith("test_"):
            return test_filename[5:]
        if test_filename.endswith("_test"):
            return test_filename[:-5]
        return ""


# ---------------------------------------------------------------------------
# Generic pattern scanner (JS/TS, configs, etc.)
# ---------------------------------------------------------------------------

class PatternScanner:
    """Regex-based scanner for non-Python files."""

    _JS_FUNC = re.compile(
        r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)",
        re.MULTILINE,
    )
    _JS_CLASS = re.compile(r"^(?:export\s+)?class\s+(\w+)", re.MULTILINE)
    _JS_ARROW = re.compile(
        r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>",
        re.MULTILINE,
    )
    _JS_IMPORT = re.compile(
        r"^import\s+(?:\{([^}]+)\}\s+from\s+|(\w+)\s+from\s+|)[\'\"]([^\'\"]+)[\'\"]",
        re.MULTILINE,
    )
    _JS_REQUIRE = re.compile(
        r"(?:const|let|var)\s+(?:\{([^}]+)\}|(\w+))\s*=\s*require\s*\(\s*[\'\"]([^\'\"]+)[\'\"]",
        re.MULTILINE,
    )
    _EXPRESS_ROUTE = re.compile(
        r"(?:app|router)\.(get|post|put|delete|patch|all)\s*\(\s*[\'\"]([^\'\"]+)[\'\"]",
        re.MULTILINE,
    )
    _JS_TEST = re.compile(
        r"(?:it|test|describe)\s*\(\s*[\'\"]([^\'\"]+)[\'\"]",
        re.MULTILINE,
    )

    def scan_js(self, source: str, path: Path) -> FileEntry:
        entry = FileEntry(
            path="", language=_detect_language(path), size=len(source),
            content_hash="", last_scanned=time.time(),
        )
        entry.symbols = self._js_symbols(source)
        entry.imports = self._js_imports(source)
        entry.routes = self._js_routes(source)
        entry.tests = self._js_tests(source, path)
        return entry

    def _js_symbols(self, source: str) -> List[SymbolInfo]:
        symbols: List[SymbolInfo] = []
        for m in self._JS_FUNC.finditer(source):
            line = source[:m.start()].count("\n") + 1
            symbols.append(SymbolInfo(
                name=m.group(1), kind="function", line=line,
                signature=f"({m.group(2).strip()})",
            ))
        for m in self._JS_CLASS.finditer(source):
            line = source[:m.start()].count("\n") + 1
            symbols.append(SymbolInfo(name=m.group(1), kind="class", line=line))
        for m in self._JS_ARROW.finditer(source):
            line = source[:m.start()].count("\n") + 1
            symbols.append(SymbolInfo(name=m.group(1), kind="function", line=line))
        return symbols

    def _js_imports(self, source: str) -> List[ImportInfo]:
        imports: List[ImportInfo] = []
        for m in self._JS_IMPORT.finditer(source):
            named = [n.strip() for n in m.group(1).split(",")] if m.group(1) else []
            default = m.group(2) or ""
            module = m.group(3)
            line = source[:m.start()].count("\n") + 1
            imports.append(ImportInfo(
                module=module, names=named or ([default] if default else []),
                line=line, is_relative=module.startswith("."),
            ))
        for m in self._JS_REQUIRE.finditer(source):
            named = [n.strip() for n in m.group(1).split(",")] if m.group(1) else []
            default = m.group(2) or ""
            module = m.group(3)
            line = source[:m.start()].count("\n") + 1
            imports.append(ImportInfo(
                module=module, names=named or ([default] if default else []),
                line=line,
            ))
        return imports

    def _js_routes(self, source: str) -> List[RouteInfo]:
        routes: List[RouteInfo] = []
        for m in self._EXPRESS_ROUTE.finditer(source):
            line = source[:m.start()].count("\n") + 1
            routes.append(RouteInfo(
                method=m.group(1).upper(), path=m.group(2),
                handler="", line=line, framework="express",
            ))
        return routes

    def _js_tests(self, source: str, path: Path) -> List[TestInfo]:
        tests: List[TestInfo] = []
        for m in self._JS_TEST.finditer(source):
            line = source[:m.start()].count("\n") + 1
            tests.append(TestInfo(
                name=m.group(1), kind="test_function", line=line,
            ))
        return tests

    def scan_config(self, path: Path, source: str, config_type: str) -> List[ConfigInfo]:
        keys: List[str] = []
        try:
            if config_type in ("package_json", "tsconfig"):
                data = json.loads(source)
                if isinstance(data, dict):
                    keys = list(data.keys())[:50]
            elif config_type == "pyproject":
                keys = self._toml_top_keys(source)
            elif config_type in ("requirements", "pipfile"):
                keys = [line.split("==")[0].split(">=")[0].strip()
                        for line in source.splitlines()
                        if line.strip() and not line.startswith("#")][:50]
            elif config_type == "dotenv":
                keys = [line.split("=")[0].strip()
                        for line in source.splitlines()
                        if line.strip() and not line.startswith("#") and "=" in line][:50]
            elif config_type in ("pytest", "tox", "setuptools"):
                keys = re.findall(r"^\[([^\]]+)\]", source, re.MULTILINE)
        except Exception:
            pass
        return [ConfigInfo(config_type=config_type, keys=keys)] if keys else [
            ConfigInfo(config_type=config_type)
        ]

    @staticmethod
    def _toml_top_keys(source: str) -> List[str]:
        return re.findall(r"^\[([^\]]+)\]", source, re.MULTILINE)[:30]


# ---------------------------------------------------------------------------
# CODEOWNERS parser
# ---------------------------------------------------------------------------

def parse_codeowners(workspace_root: Path) -> Dict[str, str]:
    """Parse CODEOWNERS file and return pattern→owner mapping."""
    owners: Dict[str, str] = {}
    for candidate in [
        workspace_root / "CODEOWNERS",
        workspace_root / ".github" / "CODEOWNERS",
        workspace_root / "docs" / "CODEOWNERS",
    ]:
        if candidate.exists():
            try:
                for line in candidate.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split()
                    if len(parts) >= 2:
                        owners[parts[0]] = parts[1]
            except (OSError, PermissionError):
                pass
            break
    return owners


def resolve_owner(rel_path: str, codeowners: Dict[str, str]) -> Optional[OwnershipInfo]:
    """Match a file path against CODEOWNERS patterns (last match wins)."""
    match = None
    for pattern, owner in codeowners.items():
        if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(
            rel_path, f"**/{pattern}"
        ):
            match = OwnershipInfo(owner=owner, pattern=pattern, source="codeowners")
    return match


# ---------------------------------------------------------------------------
# SQLite persistence layer
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    language TEXT NOT NULL,
    size INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    last_scanned REAL NOT NULL,
    data TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_files_language ON files(language);
CREATE INDEX IF NOT EXISTS idx_files_hash ON files(content_hash);

CREATE TABLE IF NOT EXISTS symbols (
    file_path TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    line INTEGER NOT NULL,
    end_line INTEGER NOT NULL DEFAULT 0,
    parent TEXT NOT NULL DEFAULT '',
    signature TEXT NOT NULL DEFAULT '',
    docstring TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (file_path) REFERENCES files(path) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_kind ON symbols(kind);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_path);

CREATE TABLE IF NOT EXISTS imports (
    file_path TEXT NOT NULL,
    module TEXT NOT NULL,
    names TEXT NOT NULL DEFAULT '[]',
    line INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (file_path) REFERENCES files(path) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_imports_module ON imports(module);
CREATE INDEX IF NOT EXISTS idx_imports_file ON imports(file_path);

CREATE TABLE IF NOT EXISTS routes (
    file_path TEXT NOT NULL,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    handler TEXT NOT NULL,
    line INTEGER NOT NULL DEFAULT 0,
    framework TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (file_path) REFERENCES files(path) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_routes_path ON routes(path);

CREATE TABLE IF NOT EXISTS tests (
    file_path TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    line INTEGER NOT NULL DEFAULT 0,
    target_module TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (file_path) REFERENCES files(path) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tests_target ON tests(target_module);
CREATE INDEX IF NOT EXISTS idx_tests_file ON tests(file_path);

CREATE TABLE IF NOT EXISTS configs (
    file_path TEXT NOT NULL,
    config_type TEXT NOT NULL,
    keys TEXT NOT NULL DEFAULT '[]',
    FOREIGN KEY (file_path) REFERENCES files(path) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ownership (
    file_path TEXT NOT NULL,
    owner TEXT NOT NULL,
    pattern TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (file_path) REFERENCES files(path) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ownership_owner ON ownership(owner);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class CodebaseMapDB:
    """SQLite-backed storage for the codebase map."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(_SCHEMA)
        return self._conn

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def get_file_hash(self, path: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT content_hash FROM files WHERE path = ?", (path,),
        ).fetchone()
        return row[0] if row else None

    def upsert_file(self, entry: FileEntry) -> None:
        c = self.conn
        c.execute("DELETE FROM files WHERE path = ?", (entry.path,))
        c.execute(
            "INSERT INTO files (path, language, size, content_hash, last_scanned, data) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (entry.path, entry.language, entry.size, entry.content_hash,
             entry.last_scanned, json.dumps(entry.to_dict(), default=str)),
        )
        for s in entry.symbols:
            c.execute(
                "INSERT INTO symbols (file_path, name, kind, line, end_line, parent, signature, docstring) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (entry.path, s.name, s.kind, s.line, s.end_line, s.parent, s.signature, s.docstring),
            )
        for i in entry.imports:
            c.execute(
                "INSERT INTO imports (file_path, module, names, line) VALUES (?, ?, ?, ?)",
                (entry.path, i.module, json.dumps(i.names), i.line),
            )
        for r in entry.routes:
            c.execute(
                "INSERT INTO routes (file_path, method, path, handler, line, framework) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (entry.path, r.method, r.path, r.handler, r.line, r.framework),
            )
        for t in entry.tests:
            c.execute(
                "INSERT INTO tests (file_path, name, kind, line, target_module) "
                "VALUES (?, ?, ?, ?, ?)",
                (entry.path, t.name, t.kind, t.line, t.target_module),
            )
        for cfg in entry.configs:
            c.execute(
                "INSERT INTO configs (file_path, config_type, keys) VALUES (?, ?, ?)",
                (entry.path, cfg.config_type, json.dumps(cfg.keys)),
            )
        for o in entry.ownership:
            c.execute(
                "INSERT INTO ownership (file_path, owner, pattern, source) VALUES (?, ?, ?, ?)",
                (entry.path, o.owner, o.pattern, o.source),
            )
        c.commit()

    def remove_file(self, path: str) -> None:
        self.conn.execute("DELETE FROM files WHERE path = ?", (path,))
        self.conn.commit()

    def all_paths(self) -> List[str]:
        return [row[0] for row in self.conn.execute("SELECT path FROM files").fetchall()]

    def get_meta(self, key: str) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value),
        )
        self.conn.commit()

    # --- Query methods ---

    def find_symbol(self, name: str, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT file_path, name, kind, line, end_line, parent, signature, docstring FROM symbols WHERE name LIKE ?"
        params: list = [f"%{name}%"]
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY file_path, line"
        return [
            {"file": r[0], "name": r[1], "kind": r[2], "line": r[3],
             "end_line": r[4], "parent": r[5], "signature": r[6], "docstring": r[7]}
            for r in self.conn.execute(sql, params).fetchall()
        ]

    def find_imports_of(self, module: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT file_path, module, names, line FROM imports WHERE module LIKE ? ORDER BY file_path",
            (f"%{module}%",),
        ).fetchall()
        return [
            {"file": r[0], "module": r[1], "names": json.loads(r[2]), "line": r[3]}
            for r in rows
        ]

    def find_tests_for(self, module: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT file_path, name, kind, line, target_module FROM tests "
            "WHERE target_module LIKE ? ORDER BY file_path, line",
            (f"%{module}%",),
        ).fetchall()
        return [
            {"file": r[0], "name": r[1], "kind": r[2], "line": r[3], "target_module": r[4]}
            for r in rows
        ]

    def find_routes(self, path_pattern: Optional[str] = None) -> List[Dict[str, Any]]:
        if path_pattern:
            rows = self.conn.execute(
                "SELECT file_path, method, path, handler, line, framework FROM routes "
                "WHERE path LIKE ? ORDER BY path",
                (f"%{path_pattern}%",),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT file_path, method, path, handler, line, framework FROM routes ORDER BY path",
            ).fetchall()
        return [
            {"file": r[0], "method": r[1], "path": r[2], "handler": r[3],
             "line": r[4], "framework": r[5]}
            for r in rows
        ]

    def find_configs(self, config_type: Optional[str] = None) -> List[Dict[str, Any]]:
        if config_type:
            rows = self.conn.execute(
                "SELECT file_path, config_type, keys FROM configs WHERE config_type = ?",
                (config_type,),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT file_path, config_type, keys FROM configs").fetchall()
        return [
            {"file": r[0], "config_type": r[1], "keys": json.loads(r[2])}
            for r in rows
        ]

    def find_owners(self, owner: Optional[str] = None) -> List[Dict[str, Any]]:
        if owner:
            rows = self.conn.execute(
                "SELECT file_path, owner, pattern, source FROM ownership WHERE owner LIKE ?",
                (f"%{owner}%",),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT file_path, owner, pattern, source FROM ownership",
            ).fetchall()
        return [
            {"file": r[0], "owner": r[1], "pattern": r[2], "source": r[3]}
            for r in rows
        ]

    def get_file(self, path: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute("SELECT data FROM files WHERE path = ?", (path,)).fetchone()
        if row:
            return json.loads(row[0])
        return None

    def stats(self) -> Dict[str, int]:
        def _count(table: str) -> int:
            return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return {
            "files": _count("files"),
            "symbols": _count("symbols"),
            "imports": _count("imports"),
            "routes": _count("routes"),
            "tests": _count("tests"),
            "configs": _count("configs"),
            "ownership": _count("ownership"),
        }

    def language_breakdown(self) -> Dict[str, int]:
        rows = self.conn.execute(
            "SELECT language, COUNT(*) FROM files GROUP BY language ORDER BY COUNT(*) DESC"
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def search_symbols(self, query: str) -> List[Dict[str, Any]]:
        return self.find_symbol(query)

    def dependency_graph(self, file_path: str) -> Dict[str, Any]:
        """Who imports this file's modules, and what does this file import."""
        stem = Path(file_path).stem
        importers = self.find_imports_of(stem)
        row = self.conn.execute("SELECT data FROM files WHERE path = ?", (file_path,)).fetchone()
        own_imports: List[Dict[str, Any]] = []
        if row:
            data = json.loads(row[0])
            own_imports = data.get("imports", [])
        return {
            "file": file_path,
            "imports": own_imports,
            "imported_by": importers,
        }


# ---------------------------------------------------------------------------
# CodebaseMap — orchestrator
# ---------------------------------------------------------------------------

class CodebaseMap:
    """Scans and maintains a persistent, queryable map of a codebase."""

    def __init__(
        self,
        workspace_root: str,
        *,
        db_path: Optional[str] = None,
        ignore_patterns: Optional[List[str]] = None,
        max_file_size: int = 512_000,
    ):
        self.workspace_root = Path(workspace_root).resolve()
        if db_path is None:
            db_path = str(self.workspace_root / ".agente" / "codebase_map.db")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = CodebaseMapDB(db_path)
        self.ignore_patterns = ignore_patterns or list(DEFAULT_IGNORE_PATTERNS)
        self.max_file_size = max_file_size
        self._python_scanner = PythonScanner()
        self._pattern_scanner = PatternScanner()
        self._codeowners: Optional[Dict[str, str]] = None

    def close(self) -> None:
        self.db.close()

    @property
    def codeowners(self) -> Dict[str, str]:
        if self._codeowners is None:
            self._codeowners = parse_codeowners(self.workspace_root)
        return self._codeowners

    def scan(self, *, force: bool = False) -> Dict[str, Any]:
        """Full or incremental scan of the workspace.

        Returns a summary with counts of new, updated, removed, and unchanged files.
        """
        start = time.time()
        existing_paths = set(self.db.all_paths())
        disk_paths: Set[str] = set()

        scanned = updated = removed = unchanged = errors = 0

        for file_path in self._walk_files():
            rel = file_path.relative_to(self.workspace_root).as_posix()
            disk_paths.add(rel)

            content_hash = _file_hash(file_path)
            if not content_hash:
                errors += 1
                continue

            if not force:
                stored_hash = self.db.get_file_hash(rel)
                if stored_hash == content_hash:
                    unchanged += 1
                    continue

            try:
                entry = self._scan_file(file_path, rel, content_hash)
                self.db.upsert_file(entry)
                if rel in existing_paths:
                    updated += 1
                else:
                    scanned += 1
            except Exception:
                logger.debug("Failed to scan %s", rel, exc_info=True)
                errors += 1

        stale = existing_paths - disk_paths
        for removed_path in stale:
            self.db.remove_file(removed_path)
            removed += 1

        elapsed = round(time.time() - start, 2)
        self.db.set_meta("last_scan", str(time.time()))
        self.db.set_meta("workspace_root", str(self.workspace_root))

        return {
            "new": scanned, "updated": updated, "removed": removed,
            "unchanged": unchanged, "errors": errors,
            "total_files": scanned + updated + unchanged,
            "elapsed_seconds": elapsed,
        }

    def _walk_files(self):
        for dirpath, dirnames, filenames in os.walk(self.workspace_root):
            dp = Path(dirpath)
            if _should_ignore(dp, self.workspace_root, self.ignore_patterns):
                dirnames.clear()
                continue
            dirnames[:] = [
                d for d in dirnames
                if not _should_ignore(dp / d, self.workspace_root, self.ignore_patterns)
            ]
            for fname in filenames:
                fp = dp / fname
                if fp.stat().st_size > self.max_file_size:
                    continue
                lang = _detect_language(fp)
                if lang == "unknown":
                    continue
                yield fp

    def _scan_file(self, file_path: Path, rel_path: str, content_hash: str) -> FileEntry:
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError):
            source = ""

        language = _detect_language(file_path)

        if language == "python":
            entry = self._python_scanner.scan(file_path, source)
        elif language in ("javascript", "typescript"):
            entry = self._pattern_scanner.scan_js(source, file_path)
        else:
            entry = FileEntry(
                path=rel_path, language=language, size=len(source),
                content_hash=content_hash, last_scanned=time.time(),
            )

        entry.path = rel_path
        entry.language = language
        entry.size = file_path.stat().st_size
        entry.content_hash = content_hash
        entry.last_scanned = time.time()

        config_type = _detect_config_type(file_path, self.workspace_root)
        if config_type:
            entry.configs = self._pattern_scanner.scan_config(file_path, source, config_type)

        owner = resolve_owner(rel_path, self.codeowners)
        if owner:
            entry.ownership = [owner]

        return entry

    # --- Public query API ---

    def find_symbol(self, name: str, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.db.find_symbol(name, kind)

    def find_imports(self, module: str) -> List[Dict[str, Any]]:
        return self.db.find_imports_of(module)

    def find_tests_for(self, module: str) -> List[Dict[str, Any]]:
        return self.db.find_tests_for(module)

    def find_routes(self, path_pattern: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.db.find_routes(path_pattern)

    def find_configs(self, config_type: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.db.find_configs(config_type)

    def find_owners(self, owner: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.db.find_owners(owner)

    def get_file(self, path: str) -> Optional[Dict[str, Any]]:
        return self.db.get_file(path)

    def stats(self) -> Dict[str, Any]:
        s = self.db.stats()
        s["workspace_root"] = str(self.workspace_root)
        s["last_scan"] = self.db.get_meta("last_scan")
        s["languages"] = self.db.language_breakdown()
        return s

    def dependency_graph(self, file_path: str) -> Dict[str, Any]:
        return self.db.dependency_graph(file_path)

    def search(self, query: str) -> Dict[str, Any]:
        """Unified search across all dimensions."""
        return {
            "symbols": self.db.find_symbol(query)[:20],
            "imports": self.db.find_imports_of(query)[:20],
            "tests": self.db.find_tests_for(query)[:20],
            "routes": self.db.find_routes(query)[:10],
            "configs": self.db.find_configs(query if query in {
                v for v in CONFIG_FILE_PATTERNS.values()
            } else None)[:10],
            "owners": self.db.find_owners(query)[:20],
        }
