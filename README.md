# Instrument MCP Server

A Model Context Protocol (MCP) server for controlling test instruments via SCPI commands.

Supports Keysight MXA/EXA spectrum analyzers, R&S CMW500 wireless testers, and Keysight DC power supplies.

## Features

- **Natural language instrument control** - Control instruments through conversational AI
- **Auto-discovery** - Automatically identifies instrument models via *IDN?
- **YAML-driven commands** - Easy to add new instruments without code changes
- **Multi-instrument sessions** - Connect and control multiple instruments simultaneously
- **Error handling** - Automatic error queue reading and diagnostics
- **AI self-learning** - AI can explore, learn, and save new instrument commands

## Prerequisites

Before installing, ensure you have:

- **Python 3.10+**
- **VISA Backend** (one of the following):
  - [NI-VISA](https://www.ni.com/en-us/support/downloads/drivers/download.ni-visa.html) (Windows/Linux)
  - [Keysight IO Libraries Suite](https://www.keysight.com/find/iosuite) (Windows)
  - [pyvisa-py](https://pyvisa-py.readthedocs.io/) (pure Python, limited support)

> **Note**: Without a VISA backend, the package will install but cannot communicate with instruments.

## Supported Instruments

| Instrument | Type | Connection |
|-----------|------|-----------|
| Keysight MXA N9020A / N9010A / EXA | Spectrum Analyzer | VISA (TCP/IP/GPIB/USB) |
| R&S CMW500 | Wireless Communication Tester | VISA (TCP/IP) |
| Keysight 66311B / 66311 | DC Power Supply | VISA (GPIB) |
| Generic SCPI instruments | Any | VISA |

## Installation

### Using uv (recommended)

```bash
# 从 PyPI 安装
uv tool install instrument-mcp

# 或从本地源码安装
uv tool install .

# 或开发模式安装
uv tool install --editable .
```

### Using pip

```bash
pip install instrument-mcp
```

### From source

```bash
git clone https://github.com/yourusername/instrument-mcp.git
cd instrument-mcp
pip install -e .
```

## Usage

### As a standalone MCP Server

```bash
# 启动服务器（stdio 模式，用于 MCP 客户端）
instrument-mcp

# 或使用 Python 模块
python -m instrument_mcp.server

# 使用 uv 运行（无需全局安装）
uv run instrument-mcp
```

### Configure in Claude Desktop

Add to your Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "instrument": {
      "command": "instrument-mcp"
    }
  }
}
```

Or with uv (if not globally installed):

```json
{
  "mcpServers": {
    "instrument": {
      "command": "uv",
      "args": ["run", "instrument-mcp"]
    }
  }
}
```

Or with full path:

```json
{
  "mcpServers": {
    "instrument": {
      "command": "python",
      "args": ["-m", "instrument_mcp.server"]
    }
  }
}
```

### Using with Claude Code

```bash
# Add to your project
claude mcp add instrument instrument-mcp
```

## Example Commands

Once connected, you can use natural language:

```
"Connect to the MXA at 192.168.1.100"
"Set center frequency to 2.4 GHz with 100 MHz span"
"Run a peak search and read the marker"
"Configure harmonic measurement with 7 harmonics"
"Fetch the harmonic amplitude values"
```

Or with the CMW500:

```
"Connect to CMW500 at 172.22.1.3"
"Preset the instrument"
"Set LTE Band 7, 20MHz bandwidth"
"Turn on the cell"
"Check UE connection status"
```

## Development

```bash
# Install in development mode
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check .

# Debug with MCP inspector
mcp dev src/instrument_mcp/server.py
```

## Project Structure

```
instrument-mcp/
├── src/instrument_mcp/
│   ├── server.py          # MCP Server entry point
│   ├── instruments.py     # VISA communication layer
│   └── commands/
│       ├── __init__.py    # YAML command loader
│       ├── mxa.yaml       # MXA spectrum analyzer commands
│       ├── cmw.yaml       # CMW500 commands
│       ├── keysight_ps.yaml # Power supply commands
│       └── generic.yaml   # Generic SCPI commands
├── pyproject.toml
└── README.md
```

## Adding New Instruments

1. Create a new YAML file in `src/instrument_mcp/commands/`
2. Define `instrument_type`, `model_keywords`, and `commands`
3. Restart the server - commands are loaded automatically

Example YAML structure:

```yaml
instrument_type: my_instrument
description: My Test Instrument
model_keywords:
  - "MYMODEL"
  - "MYBRAND"
commands:
  - name: my_command
    description: Does something useful
    params:
      - name: param1
        type: string
        description: A parameter
        default: "default_value"
    scpi_template:
      query: "MY:CMD? {param1}"
```

## Troubleshooting

### "No module named 'pyvisa'" or VISA errors

Install a VISA backend:

```bash
# Option 1: NI-VISA (recommended for Windows)
# Download from https://www.ni.com/en-us/support/downloads/drivers/download.ni-visa.html

# Option 2: pyvisa-py (pure Python, limited support)
pip install pyvisa-py
```

### Connection timeout

- Verify instrument is powered on and network reachable: `ping 192.168.1.100`
- Check instrument is not locked by another software
- Verify VISA address format: `TCPIP::IP::INSTR`, `GPIB0::5::INSTR`

### Commands not found after saving

Restart the MCP Server to reload commands from `.instrument_mcp/` directory.

## Release Checklist (for maintainers)

Before publishing to PyPI:

- [ ] Update version in `pyproject.toml`
- [ ] Update `CHANGELOG.md`
- [ ] Run tests: `pytest`
- [ ] Run lint: `ruff check .`
- [ ] Build package: `python -m build`
- [ ] Test installation: `pip install dist/*.whl`
- [ ] Upload to PyPI: `twine upload dist/*`

## License

MIT License
