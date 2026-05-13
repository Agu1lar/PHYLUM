import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

try:
    import wmi
except Exception:
    wmi = None


class WmiWrapper:
    def __init__(self):
        if wmi is None:
            self.conn = None
        else:
            try:
                self.conn = wmi.WMI()
            except Exception:
                logger.exception('WMI connection failed')
                self.conn = None

    def query(self, q: str) -> List[Dict[str, Any]]:
        if not self.conn:
            return []
        try:
            results = []
            for r in self.conn.query(q):
                d = {}
                for field in getattr(r, '_properties', []):
                    try:
                        d[field] = getattr(r, field)
                    except Exception:
                        d[field] = None
                results.append(d)
            return results
        except Exception:
            logger.exception('WMI query failed')
            return []

    def list_installed_apps(self) -> List[Dict[str, Any]]:
        # Try Win32_Product (slow) and also check registry via WMI
        apps = []
        try:
            if not self.conn:
                return []
            for p in self.conn.Win32_Product():
                apps.append({'name': getattr(p, 'Name', None), 'version': getattr(p, 'Version', None), 'vendor': getattr(p, 'Vendor', None)})
        except Exception:
            logger.exception('Win32_Product enumeration failed')
        return apps
