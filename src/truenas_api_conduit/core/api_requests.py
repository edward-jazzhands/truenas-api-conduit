from typing import Any


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
