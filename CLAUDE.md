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

## Xiaomi smart plug (chuangmi.plug.212a01)

米家智能插座2 — LAN control via miIO protocol (UDP, token-encrypted), no Xiaomi
cloud needed (cloud blocking on this network does not affect it). Token verified
2026-08-31: switch on/off, 11-property read all work. Not part of the MCP server
(yet) — use the standalone CLI (python-miio injected ephemerally, .venv untouched):

```bash
uv run --with python-miio mi_plug.py info|status|on|off|toggle
uv run --with python-miio mi_plug.py loop <on_s> <off_s>|loopstop|loopinfo
uv run --with python-miio mi_plug.py get|set <siid> <piid> [value]
```

Switch is `siid=2, piid=1` (bool); power metering lives in siid=5 (electric
power unit 0.01 W; voltage in V; current likely 0.01 A). Device-side
scheduling: cyclic loop task (siid=4: on/off durations + enable) verified —
runs on the plug itself, starts in the phase matching the current switch
state, disable freezes the current state; one-shot countdown (4/3) is NOT
locally triggerable — spec declares no action for siid=4, double-pressing the
button is inert on this unit, and the Mi Home app countdown was measured to
keep 4/3/4/5 untouched and fire ~1min later as a plain switch command.
Official Xiaomi IoT docs confirm the model: cloud timer/countdown is a server
scene that sends a `set_properties` RPC (same siid/piid we write) at expiry,
while local timing must be vendor-built — chuangmi's siid=4 loop task is
exactly that local timer. Approximate one-shots with `loop N 86500` /
`loop 86500 N` or host-side `sleep` + on/off.
Gotchas: creds live in gitignored `mi_plug_config.json` (committed template
`mi_plug_config.example.json`); to (re)extract a token use
Xiaomi-cloud-tokens-extractor — on this corp network its cloud login needs a
phone hotspot (TLS to account.xiaomi.com is reset) and QR login avoids email
2FA (full notes in the doc); `miiocli` crashes on Python 3.13 — use the
Python API / this script;
the "no mapping defined" warning each run is harmless; token survives
reboots but not factory reset / re-binding; IP is DHCP (`--ip` to override).
Full notes + property table: `docs/miplug_chuangmi_212a01.md`.

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

### CH340 serial: SerialException error 31 on open/reconfigure

CH340 USB-serial drivers spuriously fail `SetCommState` with error 31
(ERROR_GEN_FAILURE) — tested 10/10 times even when writing back the DCB
unchanged — yet the port works fine; stock pyserial raises
`SerialException` and kills the connection. The workaround is built into
`SerialModbusInstrument` (`_patch_ch340_error31()` in `instruments.py`):
on import it wraps pyserial's `_reconfigure_port` to ignore error 31. The
fix ships with the code — **no `.venv` patching needed, nothing to
re-apply after a venv rebuild**. (A stuck driver that also hangs on
`WriteFile` is a different problem — use `restart_ch340.bat`.)
Full write-up (Chinese): `docs/CH340_error31_fix.md`.
