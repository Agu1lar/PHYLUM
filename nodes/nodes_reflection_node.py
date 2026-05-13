# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations
import asyncio
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path
from pydantic import BaseModel

from nodes_base import BaseNode
from os_inspect_powershell import run_powershell
from fs_utils import compute_hash

from reflection_models import ReflectionReport

logger = logging.getLogger(__name__)


class ReflectionNode(BaseNode):
    """Advanced reflection node.

    Expects state to include an 'expectations' dict describing expected outcomes, for example:
      expectations: {
        'files_created': ['C:/path/to/file.txt'],
        'install': {'type':'package','name':'git'},
        'downloads': ['C:/Users/User/Downloads/file.zip'],
        'folders': ['C:/Users/User/Desktop/Images']
      }

    The node will analyze state.history and the current system to produce a ReflectionReport.
    It can recommend retrying an earlier node by returning recommendation in the report.
    """

    async def validate(self, state: Dict[str, Any]) -> bool:
        # always runnable; but prefer an expectations dict
        return True

    async def _check_files(self, files: List[str]) -> Dict[str, bool]:
        res = {}
        for f in files:
            try:
                res[f] = Path(f).exists()
            except Exception:
                res[f] = False
        return res

    async def _check_hashes(self, files: Dict[str, str]) -> Dict[str, bool]:
        res = {}
        for f, expected_hash in files.items():
            try:
                h = await compute_hash(Path(f))
                res[f] = h == expected_hash
            except Exception:
                res[f] = False
        return res

    async def _check_package(self, pkg: Dict[str, Any]) -> Dict[str, Any]:
        # support types: package manager (choco/powershell Get-Package)
        name = pkg.get('name')
        manager = pkg.get('manager', 'powershell')
        try:
            if manager == 'powershell':
                cmd = f"Get-Package -Name '{name}' -ErrorAction SilentlyContinue | Select-Object -Property Name,Version | ConvertTo-Json"
                out = await run_powershell(cmd, timeout=15)
                ok = False
                details = {'cmd_out': out}
                if out.get('returncode') == 0 and out.get('stdout'):
                    import json
                    try:
                        parsed = json.loads(out['stdout'])
                        # if parsed is list or dict indicates presence
                        if parsed:
                            ok = True
                    except Exception:
                        ok = False
                return {'installed': ok, 'details': details}
            elif manager == 'choco':
                # use choco list --local-only | findstr
                cmd = f"choco list --local-only --exact {name}"
                out = await run_powershell(cmd, timeout=15)
                ok = out.get('returncode') == 0 and name.lower() in (out.get('stdout') or '').lower()
                return {'installed': ok, 'details': out}
        except Exception as exc:
            logger.exception('package check failed')
            return {'installed': False, 'error': str(exc)}

    async def _analyze_history(self, state: Dict[str, Any]) -> Dict[str, Any]:
        hist = state.get('history', {})
        analysis = {'nodes': {}, 'errors': []}
        for node_id, rec in hist.items():
            try:
                result = rec.get('result', {})
                # detect non-zero shell return codes
                if isinstance(result, dict) and 'shell' in result:
                    shell = result['shell']
                    rc = shell.get('returncode')
                    analysis['nodes'][node_id] = {'returncode': rc, 'stdout': shell.get('stdout'), 'stderr': shell.get('stderr')}
                    if rc != 0:
                        analysis['errors'].append({'node': node_id, 'rc': rc, 'stderr': shell.get('stderr')})
                else:
                    analysis['nodes'][node_id] = {'meta': 'non-shell-result'}
            except Exception:
                logger.exception('history parse failed for %s', node_id)
        return analysis

    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        expectations = state.get('inputs', {}).get('expectations', {})
        report = ReflectionReport(verdict='unknown', details={}, checks={}, recommended_action=None)
        checks = {}
        try:
            # 1. analyze history for silent failures
            hist_analysis = await self._analyze_history(state)
            checks['history'] = hist_analysis

            # 2. verify files exist
            if expectations.get('files_created'):
                checks['files_created'] = await self._check_files(expectations['files_created'])

            # 3. verify downloaded files via hashes or paths
            if expectations.get('downloads'):
                checks['downloads'] = await self._check_files(expectations['downloads'])

            # 4. verify specific hashes
            if expectations.get('file_hashes'):
                checks['file_hashes'] = await self._check_hashes(expectations['file_hashes'])

            # 5. verify package installs
            if expectations.get('install'):
                checks['install'] = await self._check_package(expectations['install'])

            # 6. folders
            if expectations.get('folders'):
                checks['folders'] = await self._check_files(expectations['folders'])

            # Determine verdict
            failed_checks = []
            for k, v in checks.items():
                # v can be dict of booleans or nested
                def _scan(o, prefix=''):
                    if isinstance(o, dict):
                        for kk, vv in o.items():
                            _scan(vv, prefix + '.' + kk)
                    elif isinstance(o, list):
                        for idx, item in enumerate(o):
                            _scan(item, prefix + f'[{idx}]')
                    else:
                        if o is False:
                            failed_checks.append(prefix)
                _scan(v, k)
            if hist_analysis.get('errors'):
                failed_checks.append('history.errors')

            if not failed_checks:
                report.verdict = 'success'
                report.details = {'summary': 'All checks passed'}
            else:
                # decide if retry is sensible: if history shows non-zero rc for a shell node, suggest retry of that node
                recommendation = None
                if hist_analysis.get('errors'):
                    err = hist_analysis['errors'][0]
                    recommendation = {'action': 'retry', 'target_node': err['node'], 'reason': f"node returned rc {err['rc']}", 'suggested_attempts': 1}
                else:
                    # if missing files, perhaps retry download or re-run shell
                    recommendation = {'action': 'retry', 'target_node': state.get('current_node'), 'reason': 'checks failed', 'suggested_attempts': 1}
                report.verdict = 'retry'
                report.details = {'failed': failed_checks}
                report.recommended_action = recommendation
            report.checks = checks
            # persist report to state outputs for visibility
            state.setdefault('outputs', {})['reflection'] = report.dict()
            return {'reflection': report.dict()}
        except Exception as exc:
            logger.exception('reflection execute failed')
            report.verdict = 'failed'
            report.details = {'error': str(exc)}
            state.setdefault('outputs', {})['reflection'] = report.dict()
            return {'reflection': report.dict()}

    async def verify(self, state: Dict[str, Any], result: Dict[str, Any]) -> bool:
        # verify returns true if verdict is success
        try:
            rep = result.get('reflection')
            if not rep:
                return False
            return rep.get('verdict') == 'success'
        except Exception:
            return False

    async def rollback(self, state: Dict[str, Any], result: Dict[str, Any]) -> None:
        # reflection node itself is read-only; nothing to rollback
        logger.info('ReflectionNode rollback called (noop)')
        return None
