from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


def _run_powershell_json(script: str) -> List[Dict[str, Any]]:
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "share discovery command failed")
    stdout = (completed.stdout or "").strip()
    if not stdout:
        return []
    payload = json.loads(stdout)
    return payload if isinstance(payload, list) else [payload]


class ShareDiscoveryAgent:
    async def list_mappings(self) -> Dict[str, Any]:
        script = r"""
$items = @()
try {
  $items += Get-SmbMapping | Select-Object LocalPath, RemotePath, Status, UserName
} catch {}
try {
  $items += Get-PSDrive -PSProvider FileSystem | Where-Object { $_.DisplayRoot } | Select-Object @{n='LocalPath';e={$_.Name + ':'}}, @{n='RemotePath';e={$_.DisplayRoot}}, @{n='Status';e={'OK'}}, @{n='UserName';e={$null}}
} catch {}
$items | Sort-Object LocalPath, RemotePath -Unique | ConvertTo-Json -Depth 5 -Compress
"""
        items = await asyncio.to_thread(_run_powershell_json, script)
        return {"mappings": items}

    async def list_explorer_context(self) -> Dict[str, Any]:
        script = r"""
$shell = New-Object -ComObject Shell.Application
$items = @()
foreach ($window in $shell.Windows()) {
  try {
    $selected = @()
    try { $selected = @($window.Document.SelectedItems() | ForEach-Object { $_.Path }) } catch {}
    $items += [pscustomobject]@{
      title = [string]$window.LocationName
      location_path = try { [string]$window.Document.Folder.Self.Path } catch { $null }
      location_url = [string]$window.LocationURL
      selected_items = $selected
    }
  } catch {}
}
$items | ConvertTo-Json -Depth 6 -Compress
"""
        items = await asyncio.to_thread(_run_powershell_json, script)
        return {"windows": items}

    async def inspect_share(self, path: str, limit: int = 25) -> Dict[str, Any]:
        path_literal = json.dumps(path)
        script = f"""
$path = {path_literal}
$exists = Test-Path -LiteralPath $path
$items = @()
$acl = $null
if ($exists) {{
  try {{
    $items = Get-ChildItem -LiteralPath $path -Force -ErrorAction Stop | Select-Object -First {max(limit, 1)} Name, FullName, PSIsContainer, Length, LastWriteTime
  }} catch {{}}
  try {{
    $acl = Get-Acl -LiteralPath $path | Select-Object Owner, AccessToString
  }} catch {{}}
}}
[pscustomobject]@{{
  path = $path
  exists = $exists
  items = $items
  acl = $acl
}} | ConvertTo-Json -Depth 6 -Compress
"""
        items = await asyncio.to_thread(_run_powershell_json, script)
        return items[0] if items else {"path": path, "exists": False, "items": [], "acl": None}

    async def inspect_corporate_share(self, path: str, limit: int = 50) -> Dict[str, Any]:
        base = await self.inspect_share(path, limit=limit)
        path_literal = json.dumps(path)
        script = f"""
$path = {path_literal}
$root = $null
$server = $null
$share = $null
if ($path.StartsWith('\\\\')) {{
  $parts = $path.TrimStart('\\').Split('\\')
  if ($parts.Length -ge 2) {{
    $server = $parts[0]
    $share = $parts[1]
    $root = '\\\\' + $server + '\\' + $share
  }}
}}
$mapping = $null
try {{
  $mapping = Get-SmbMapping | Where-Object {{ $_.RemotePath -eq $root -or $path.StartsWith($_.RemotePath) }} | Select-Object -First 1 LocalPath, RemotePath, Status, UserName
}} catch {{}}
$dfs = $false
try {{
  if ($root) {{ $dfs = [bool](Get-DfsnFolderTarget -Path $root -ErrorAction SilentlyContinue) }}
}} catch {{}}
[pscustomobject]@{{
  server = $server
  share = $share
  root = $root
  mapping = $mapping
  dfs_detected = $dfs
  recommended_followups = @('document_intelligence.index_documents', 'share_discovery.inspect_share', 'desktop.explorer_navigate')
}} | ConvertTo-Json -Depth 6 -Compress
"""
        details = await asyncio.to_thread(_run_powershell_json, script)
        corporate = details[0] if details else {}
        base["corporate"] = corporate
        base["origin"] = corporate.get("root") or path
        return base

    async def discover_targets(self, query: Optional[str] = None) -> Dict[str, Any]:
        query_literal = json.dumps(query or "")
        script = f"""
$query = {query_literal}.ToLowerInvariant()
$results = @()
try {{
  $results += Get-SmbMapping | ForEach-Object {{
    [pscustomobject]@{{ kind='mapping'; label=($_.LocalPath + ' -> ' + $_.RemotePath); path=$_.RemotePath; local_path=$_.LocalPath; status=$_.Status }}
  }}
}} catch {{}}
try {{
  $results += Get-PSDrive -PSProvider FileSystem | Where-Object {{ $_.DisplayRoot }} | ForEach-Object {{
    [pscustomobject]@{{ kind='drive'; label=($_.Name + ': -> ' + $_.DisplayRoot); path=$_.DisplayRoot; local_path=($_.Name + ':'); status='OK' }}
  }}
}} catch {{}}
if ($query) {{
  $results = $results | Where-Object {{
    $_.label.ToLowerInvariant().Contains($query) -or
    ([string]$_.path).ToLowerInvariant().Contains($query) -or
    ([string]$_.local_path).ToLowerInvariant().Contains($query)
  }}
}}
$results | Sort-Object kind, label -Unique | ConvertTo-Json -Depth 6 -Compress
"""
        candidates = await asyncio.to_thread(_run_powershell_json, script)
        return {"candidates": candidates}

