from pydantic import BaseModel
from typing import List, Optional, Dict, Any


class AppInfo(BaseModel):
    name: str
    version: Optional[str]
    publisher: Optional[str]
    install_location: Optional[str]
    uninstall_string: Optional[str]


class ProcessInfo(BaseModel):
    pid: int
    name: str
    exe: Optional[str]
    cmdline: Optional[str]
    username: Optional[str]
    cpu_percent: Optional[float]
    memory_mb: Optional[float]


class CPUInfo(BaseModel):
    physical_cores: int
    logical_cores: int
    model: Optional[str]
    freq_mhz: Optional[float]
    load_percent_per_core: List[float]
    average_load: float


class GPUInfo(BaseModel):
    name: str
    driver_version: Optional[str]
    vram_mb: Optional[int]
    adapter_ram: Optional[int]


class MemoryInfo(BaseModel):
    total_mb: int
    available_mb: int
    used_mb: int
    percent: float


class DiskPartition(BaseModel):
    device: str
    mountpoint: str
    fstype: str
    total_gb: Optional[float]
    used_gb: Optional[float]
    free_gb: Optional[float]
    percent: Optional[float]


class NetworkInterface(BaseModel):
    name: str
    mac: Optional[str]
    ip_addresses: List[str]
    is_up: bool


class PermissionInfo(BaseModel):
    is_admin: bool
    has_elevated_privileges: Optional[bool]
    user: Optional[str]


class SystemOverview(BaseModel):
    hostname: str
    platform: str
    platform_version: str
    architecture: str
    timestamp: Optional[str]


class IntrospectionResult(BaseModel):
    overview: SystemOverview
    apps: Optional[List[AppInfo]]
    processes: Optional[List[ProcessInfo]]
    cpu: Optional[CPUInfo]
    gpu: Optional[List[GPUInfo]]
    memory: Optional[MemoryInfo]
    disks: Optional[List[DiskPartition]]
    network: Optional[List[NetworkInterface]]
    permissions: Optional[PermissionInfo]
    raw: Optional[Dict[str, Any]]
