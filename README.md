# Instrument MCP Server

A Model Context Protocol (MCP) server for controlling test instruments via SCPI, Modbus-RTU, or direct USB.

Supports Keysight MXA/EXA spectrum analyzers, R&S CMW500 wireless testers, Keysight DC power supplies, Modbus-RTU chambers, and the DreamSourceLab DSLogic U3Pro16 USB logic analyzer.

## Features

- **Natural language instrument control** - Control instruments through conversational AI
- **Auto-discovery** - Automatically identifies instrument models via *IDN?
- **YAML-driven commands** - Easy to add new instruments without code changes
- **Multi-instrument sessions** - Connect and control multiple instruments simultaneously
- **Error handling** - Automatic error queue reading and diagnostics
- **AI self-learning** - AI can explore, learn, and save new instrument commands
- **USB logic analyzer support** - Direct USB control of DSLogic U3Pro16 (no VISA required)

## Prerequisites

Before installing, ensure you have:

- **Python 3.10+**
- **VISA Backend** (one of the following):
  - [NI-VISA](https://www.ni.com/en-us/support/downloads/drivers/download.ni-visa.html) (Windows/Linux)
  - [Keysight IO Libraries Suite](https://www.keysight.com/find/iosuite) (Windows)
  - [pyvisa-py](https://pyvisa-py.readthedocs.io/) (pure Python, limited support)

> **Notes**:
> - Without a VISA backend, the package cannot communicate with VISA instruments.
> - The DSLogic U3Pro16 uses USB directly via `pyusb` + `libusb-1.0.dll` and does not require VISA.

## Supported Instruments

| Instrument | Type | Connection |
|-----------|------|-----------|
| Keysight MXA N9020A / N9010A / EXA | Spectrum Analyzer | VISA (TCP/IP/GPIB/USB) |
| R&S CMW500 | Wireless Communication Tester | VISA (TCP/IP) |
| Keysight 66311B / 66311 | DC Power Supply | VISA (GPIB) |
| DreamSourceLab DSLogic U3Pro16 | USB Logic Analyzer | USB (pyusb + libusb-1.0) |
| Generic SCPI instruments | Any | VISA |
| Modbus-RTU 恒温恒湿试验箱 | Chamber | Serial (RS-232C) |

## Modbus 温箱（恒温恒湿试验箱）

Modbus-RTU 恒温恒湿试验箱，支持定值运行与程式（程序）控制。协议：9600 8N1, CRC-16/MODBUS, 站地址 1。

### Connection

```
connect(address="COM30", instrument_type="modbus_chamber", alias="chamber")
```

### 定值模式命令

| 命令 | 功能 |
|------|------|
| `mc_read_pv` | 读取当前温度 PV |
| `mc_read_sv` / `mc_set_sv` | 读取/设置设定温度 SV |
| `mc_read_status` | 读取完整状态（温度/湿度/输出/运行标志） |
| `mc_read_humidity` / `mc_read_humidity_sv` | 读取湿度 PV/SV |
| `mc_set_humidity_sv` | 设置目标湿度 SV |
| `mc_run` / `mc_stop` | 启动/停止定值运行 |

### 程式控制命令

| 命令 | 功能 |
|------|------|
| `mc_prog_read_table` | 读取任意程式的完整段表（温度/湿度/时间/循环/TS1-4） |
| `mc_prog_read_seg` | 读取指定程式的某一段 |
| `mc_prog_write_seg` | 修改已有段的设定（温度/湿度/时间/TS） |
| `mc_prog_set_cycle` | 设置某段的循环次数 |
| `mc_prog_status` | 读取程式运行状态（程式号/段号/剩余时间） |
| `mc_prog_run` | 启动程式运行 |
| `mc_prog_clear` | 清空段表 |

**程式启动方法**：先写 `0x0064` 选定程式号，再写 `0x0065=1` 启动（与定值运行同值，区别在选程式号）。

**硬件限制**（COM30 实测）：段数寄存器(reg41)只读，无法通过 Modbus 新增段或新建程式。新增段/新建程式须在面板操作——推荐在面板上把已有程式**复制**到目标号，再用 `mc_prog_write_seg` / `mc_prog_set_cycle` 远程修改各段参数。

## DSLogic U3Pro16 Logic Analyzer

The DSLogic U3Pro16 is supported as a first-class MCP instrument.

### Prerequisites

- Install a 64-bit `libusb-1.0.dll` and ensure it is discoverable:
  - Placed next to the package (`src/instrument_mcp/libusb-1.0.dll`), or
  - In the current working directory, or
  - In a directory in your `PATH`.
- Windows uses the WinUSB driver for DSLogic (installed by DSView). Close DSView before connecting from MCP.

### Connection

```
connect(address="USB", instrument_type="dslogic", alias="la")
```

### Commands

After connecting:

```
dslogic_get_status(alias="la")
dslogic_set_threshold(alias="la", vth=1.65)
dslogic_capture(alias="la", samplerate_mhz=100, samples=1024, output_path="capture.bin")
dslogic_monitor_level(alias="la", samplerate_mhz=1, duration=30, channel=-1, edge_type="both", debounce_us=0)
```

`dslogic_monitor_level` runs in stream mode on the DSLogic U3Pro16 and reports edge transitions (rising, falling, or both) with optional debouncing. It prints the initial/final state of all 16 channels and a list of detected edge events. This command is specific to the DSLogic U3Pro16 and will not work with other logic analyzers.

### Data Format

- **Stream mode** (`dslogic_monitor_level`): Uses the DSLogic `LA_CROSS_DATA` format. Data arrives as 8-byte blocks per channel, where each block contains 64 consecutive samples for that channel. Channels are interleaved in the order CH0, CH1, ..., CH15, then repeated.
- **Buffer mode** (`dslogic_capture`): Capture files are raw data from the device. With 16 channels enabled in buffer mode, each sample is typically a 2-byte little-endian word where bit `n` represents channel `n` (`CH0` = LSB).

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
│   ├── server.py            # MCP Server entry point
│   ├── instruments.py       # Instrument abstraction layer (VISA, Modbus, USB)
│   ├── dslogic_usb.py       # Low-level DSLogic U3Pro16 USB controller
│   └── commands/
│       ├── __init__.py      # YAML command loader
│       ├── mxa.yaml         # MXA spectrum analyzer commands
│       ├── cmw.yaml         # CMW500 commands
│       ├── keysight_ps.yaml # Power supply commands
│       ├── dslogic.yaml     # DSLogic U3Pro16 commands
│       └── generic.yaml     # Generic SCPI commands
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

### DSLogic U3Pro16 "Access denied" or USB errors

- **Close DSView** before connecting from MCP. DSView holds the USB device open.
- Ensure a 64-bit `libusb-1.0.dll` is available next to the package, in the working directory, or in `PATH`.
- On Windows, DSLogic uses the WinUSB driver installed by DSView. Reinstall the driver if needed.

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
