"""Windows OS Introspection Agent
Provides async methods to collect system info using psutil, WMI, PowerShell and pywin32 where available.
Returns Pydantic models (json-serializable).
"""
import asyncio
import logging
import platform
import socket
from datetime import datetime
from typing import List, Optional, Dict, Any
from pathlib import Path

import psutil

from os_inspect_models import (AppInfo, ProcessInfo, CPUInfo, GPUInfo, MemoryInfo,
                               DiskPartition, NetworkInterface, PermissionInfo,
                               SystemOverview, IntrospectionResult)
from os_inspect_powershell import run_powershell
from os_inspect_wmi import WmiWrapper

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

wmi_wrapper = WmiWrapper()


async def _get_overview() -> SystemOverview:
    hostname = socket.gethostname()
    plat = platform.system()
    ver = platform.version()
    arch = platform.machine()
    ts = datetime.utcnow().isoformat()
    return SystemOverview(hostname=hostname, platform=plat, platform_version=ver, architecture=arch, timestamp=ts)


async def _get_apps() -> List[AppInfo]:
    # prefer WMI list but may be slow; also try powershell inventor
    apps = []
    try:
        apps_wmi = await asyncio.to_thread(wmi_wrapper.list_installed_apps)
        for a in apps_wmi:
            apps.append(AppInfo(name=a.get('name') or 'unknown', version=a.get('version'), publisher=a.get('vendor'), install_location=None, uninstall_string=None))
    except Exception:
        logger.exception('apps wmi failed')
    # supplement with PowerShell Get-Package
    try:
        ps = "Get-Package | Select-Object -Property Name, Version, ProviderName | ConvertTo-Json"
        out = await run_powershell(ps, timeout=20)
        if out.get('returncode') == 0 and out.get('stdout'):
            import json
            try:
                parsed = json.loads(out['stdout'])
                if isinstance(parsed, list):
                    for p in parsed:
                        apps.append(AppInfo(name=p.get('Name') or 'unknown', version=p.get('Version'), publisher=p.get('ProviderName'), install_location=None, uninstall_string=None))
                elif isinstance(parsed, dict):
                    apps.append(AppInfo(name=parsed.get('Name') or 'unknown', version=parsed.get('Version'), publisher=parsed.get('ProviderName')))
            except Exception:
                logger.exception('ps output parse failed')
    except Exception:
        logger.exception('powershell Get-Package failed')
    return apps


async def _get_processes(limit: int = 200) -> List[ProcessInfo]:
    procs = []
    try:
        for p in psutil.process_iter(['pid', 'name', 'exe', 'cmdline', 'username', 'cpu_percent', 'memory_info']):
            try:
                mem_mb = None
                mi = p.info.get('memory_info')
                if mi:
                    mem_mb = mi.rss / (1024 * 1024)
                procs.append(ProcessInfo(pid=p.info['pid'], name=p.info.get('name') or '', exe=p.info.get('exe'), cmdline=' '.join(p.info.get('cmdline') or []), username=p.info.get('username'), cpu_percent=p.info.get('cpu_percent'), memory_mb=mem_mb))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if len(procs) >= limit:
                break
    except Exception:
        logger.exception('process enumeration failed')
    return procs


async def _get_cpu() -> CPUInfo:
    try:
        phys = psutil.cpu_count(logical=False) or 1
        logical = psutil.cpu_count(logical=True) or phys
        freqs = psutil.cpu_freq()
        freq = freqs.current if freqs else None
        percore = psutil.cpu_percent(percpu=True)
        avg = psutil.cpu_percent()
        model = None
        # try WMI for model
        try:
            raw = wmi_wrapper.query('SELECT Name,NumberOfCores,NumberOfLogicalProcessors FROM Win32_Processor')
            if raw and isinstance(raw, list):
                model = raw[0].get('Name')
        except Exception:
            logger.exception('wmi cpu failed')
        return CPUInfo(physical_cores=phys, logical_cores=logical, model=model, freq_mhz=freq, load_percent_per_core=percore, average_load=avg)
    except Exception:
        logger.exception('cpu gather failed')
        return CPUInfo(physical_cores=1, logical_cores=1, model=None, freq_mhz=None, load_percent_per_core=[0.0], average_load=0.0)


async def _get_gpu() -> List[GPUInfo]:
    gpus = []
    try:
        raw = wmi_wrapper.query('SELECT Name, DriverVersion, AdapterRAM FROM Win32_VideoController')
        for r in raw:
            vram = None
            if r.get('AdapterRAM'):
                try:
                    vram = int(r.get('AdapterRAM')) // (1024 * 1024)
                except Exception:
                    vram = None
            gpus.append(GPUInfo(name=r.get('Name') or 'unknown', driver_version=r.get('DriverVersion'), vram_mb=vram, adapter_ram=r.get('AdapterRAM')))
    except Exception:
        logger.exception('gpu gather failed')
    return gpus


async def _get_memory() -> MemoryInfo:
    try:
        vm = psutil.virtual_memory()
        return MemoryInfo(total_mb=int(vm.total / (1024 * 1024)), available_mb=int(vm.available / (1024 * 1024)), used_mb=int(vm.used / (1024 * 1024)), percent=vm.percent)
    except Exception:
        logger.exception('memory gather failed')
        return MemoryInfo(total_mb=0, available_mb=0, used_mb=0, percent=0.0)


async def _get_disks() -> List[DiskPartition]:
    parts = []
    try:
        for p in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(p.mountpoint)
                parts.append(DiskPartition(device=p.device, mountpoint=p.mountpoint, fstype=p.fstype, total_gb=round(usage.total / (1024**3), 2), used_gb=round(usage.used / (1024**3), 2), free_gb=round(usage.free / (1024**3), 2), percent=usage.percent))
            except Exception:
                parts.append(DiskPartition(device=p.device, mountpoint=p.mountpoint, fstype=p.fstype, total_gb=None, used_gb=None, free_gb=None, percent=None))
    except Exception:
        logger.exception('disk gather failed')
    return parts


async def _get_network() -> List[NetworkInterface]:
    nets = []
    try:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        for name, addrl in addrs.items():
            ips = []
            mac = None
            for a in addrl:
                if a.family.name == 'AF_INET' or getattr(a.family, 'value', None) == 2:
                    ips.append(a.address)
                if a.family.name == 'AF_LINK' or getattr(a.family, 'value', None) == 17:
                    mac = a.address
            is_up = stats.get(name).isup if stats.get(name) else False
            nets.append(NetworkInterface(name=name, mac=mac, ip_addresses=ips, is_up=is_up))
    except Exception:
        logger.exception('network gather failed')
    return nets


async def _get_permissions() -> PermissionInfo:
    try:
        # check admin via ctypes
        import ctypes
        is_admin = False
        try:
            is_admin = (ctypes.windll.shell32.IsUserAnAdmin() != 0)
        except Exception:
            is_admin = False
        user = None
        try:
            import getpass
            user = getpass.getuser()
        except Exception:
            user = None
        # has_elevated_privileges try via token (pywin32 optional)
        has_elev = None
        try:
            import win32security
            import win32con
            token = win32security.OpenProcessToken(win32security.GetCurrentProcess(), win32con.TOKEN_QUERY)
            # placeholder check
            has_elev = is_admin
        except Exception:
            has_elev = None
        return PermissionInfo(is_admin=is_admin, has_elevated_privileges=has_elev, user=user)
    except Exception:
        logger.exception('permission gather failed')
        return PermissionInfo(is_admin=False, has_elevated_privileges=None, user=None)


async def introspect(full: bool = True) -> IntrospectionResult:
    overview = await _get_overview()
    # Run expensive ops in parallel
    tasks = [asyncio.create_task(_get_processes()), asyncio.create_task(_get_cpu()), asyncio.create_task(_get_gpu()), asyncio.create_task(_get_memory()), asyncio.create_task(_get_disks()), asyncio.create_task(_get_network()), asyncio.create_task(_get_permissions())]
    apps_task = asyncio.create_task(_get_apps())

    results = await asyncio.gather(*tasks, return_exceptions=True)
    procs, cpu, gpus, memory, disks, network, permissions = results
    apps = await apps_task

    # normalize exceptions
    def _unwrap(x):
        if isinstance(x, Exception):
            logger.exception('task failed: %s', x)
            return None
        return x

    procs = _unwrap(procs) or []
    cpu = _unwrap(cpu)
    gpus = _unwrap(gpus) or []
    memory = _unwrap(memory)
    disks = _unwrap(disks) or []
    network = _unwrap(network) or []
    permissions = _unwrap(permissions)

    # Convert to models
    procs_models = [p for p in procs]
    gpus_models = [g for g in gpus]

    raw = {
        'psutil_version': getattr(psutil, '__version__', None),
        'wmi_available': bool(wmi_wrapper.conn)
    }

    return IntrospectionResult(overview=overview, apps=apps, processes=procs_models, cpu=cpu, gpu=gpus_models, memory=memory, disks=disks, network=network, permissions=permissions, raw=raw)


# convenience CLI
if __name__ == '__main__':
    import json
    data = asyncio.run(introspect())
    print(json.dumps(data.dict(), indent=2, default=str))
