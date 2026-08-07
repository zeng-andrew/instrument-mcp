# Instrument MCP - Project Guide

## Overview

This is a Model Context Protocol (MCP) server for controlling test instruments (MXA, CMW500, power supplies) via SCPI commands over VISA.

## Architecture

### Core Components

- **server.py** - FastMCP server with session management
- **instruments.py** - VISA communication layer (pyvisa-based)
- **commands/__init__.py** - Dynamic YAML command loader
- **commands/*.yaml** - SCPI command definitions per instrument

### Command Registration Flow

1. YAML files define commands with SCPI templates
2. `register_commands_from_yaml()` loads them at startup
3. Pydantic models are auto-generated for parameter validation
4. Commands are registered as MCP tools with annotations

### Session Management

- `_sessions` dict maps alias -> VisaInstrument instance
- `connect()` opens connection and auto-detects instrument type
- `disconnect()` closes and cleans up
- Sessions persist until explicitly disconnected or server stops

## Key Design Decisions

### Why YAML for commands?

- Non-programmers can add instrument support
- No code changes needed for new SCPI commands
- Self-documenting with descriptions and defaults

### Why dynamic Pydantic models?

- Type-safe parameter validation without boilerplate
- MCP framework requires structured inputs
- Auto-generated from YAML definitions

## Adding a New Command

1. Edit the appropriate YAML file in `src/instrument_mcp/commands/`
2. Follow the existing command structure
3. Restart the server

Example:
```yaml
- name: mxa_my_new_command
  description: What this command does
  annotations:
    readOnlyHint: true  # or destructiveHint: true
  params:
    - name: freq_mhz
      type: number
      description: Frequency in MHz
      default: 1000
  scpi_template:
    write: "FREQ:CENT {freq_mhz} MHz"
```

## tinySA / tinySA Ultra+ (tinysa)

Zeeenko ZS-407 etc. — firmware `tinySA4_v1.4-217` verified (COM44, USB CDC).
Not VISA/SCPI: plain text commands over serial, responses end with the `ch> `
prompt; `TinySAInstrument` in `instruments.py` handles echo/prompt/binary IO.

```python
connect(address="COM44", instrument_type="tinysa", alias="sa")
```

Key tools (from `commands/tinysa.yaml`, handlers in `tinysa_handler.py`):
- `tinysa_sweep_get` / `tinysa_sweep_set` — scan range + points (`sweep` cmd)
- `tinysa_scan` — text sweep, returns freq/level CSV; `tinysa_scanraw` — binary
  sweep, returns dBm CSV (points up to 290 vs unlimited, 10-50x faster)
- `tinysa_marker` — peak/on/off; `tinysa_freq` — single-point level at a
  frequency (uses one-point scan; `marker N <freq>` has no readback)
- `tinysa_capture` — screenshot, returns path to a 480x320 RGB565->BMP file
- `tinysa_raw_command` — pass through any command (`help` lists all)

Verified quirks: `scan` needs `outmask 3` for the frequency column; `scanraw`
bytes are LSB-first (docs say MSB); dBm = raw/32 - 174 (Ultra); capture is
307200 bytes fixed (must read exactly, pixel data can contain `ch> `); the
COM port is STM32 USB CDC so baud rate is cosmetic; enlarge pyserial RX buffer
(`set_buffer_size`) to avoid overflow. Full notes: `docs/tinysa_zs407.md`.

## Testing

### Manual testing with MCP inspector

```bash
mcp dev src/instrument_mcp/server.py
```

### Testing instrument connections

```python
from instrument_mcp.instruments import VisaInstrument

inst = VisaInstrument("TCPIP::192.168.1.100::INSTR")
inst.open()
print(inst.query("*IDN?"))
inst.close()
```

## Common Issues

### pyvisa not found

Install NI-VISA or Keysight IO Libraries Suite for VISA backend.

### Connection timeout

- Check network connectivity: `ping 192.168.1.100`
- Verify instrument is not locked by another session
- Increase timeout in VisaInstrument constructor

### Command errors

- Use `debug_last_error` tool to read instrument error queue
- Check parameter types (numbers vs strings)
- Verify instrument is in correct mode for command
