"""Reference client for the agent API of an atomicswapd node.

Reads the market (/api/v1), recomputes the taker's safety checks locally
and compares them with the server, and can serve the same read tools over
MCP. Standard library only. It holds no keys and sends no transactions.
"""

__version__ = '0.1.0'

from .client import ApiError, Client, NetworkError       # noqa: E402,F401
from .report import check_offer                           # noqa: E402,F401
