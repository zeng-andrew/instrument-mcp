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
