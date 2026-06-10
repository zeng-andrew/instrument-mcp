"""MCP Server entry point for python -m instrument_mcp.

Usage:
    # Run with MCP inspector (development)
    mcp dev src/instrument_mcp/server.py

    # Run directly (production, requires stdio client like Claude Desktop)
    python -m instrument_mcp.server

    # Or after installation
    instrument-mcp

Note:
    This server uses stdio transport. It will exit immediately if run
    directly without a client (like Claude Desktop/Code) because stdio
    requires an active connection.
"""

from instrument_mcp.server import main

if __name__ == "__main__":
    main()
