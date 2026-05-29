from typing import Any

# Server Metrics I want to collect:

#   - System uptime (days) - system.info
#   - CPU usage (percent)  - reporting.get_data "{"name":"cpu"}, {"start":$start,"end":$end,"aggregate":true}"
#   - CPU temperature (degrees C)
#   - RAM usage (percent)
#   - Disk usage (percent) - disk.query or possibly pool.query
#   - Network usage (bytes/s)
#   - Number of active alerts - alert.list


def system_info(req_id: int) -> dict[str, Any]:
    """contains the result.uptime stat"""

    return {
        "id": req_id,
        "jsonrpc": "2.0",
        "method": "system.info",
        "params": [],
    }


def pool_query(req_id: int) -> dict[str, Any]:
    """contains result.size, result.allocated, result.free"""

    return {
        "id": req_id,
        "jsonrpc": "2.0",
        "method": "pool.query",
        "params": [],
    }
