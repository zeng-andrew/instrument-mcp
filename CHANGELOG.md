# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial release of instrument-mcp
- Support for Keysight MXA/EXA spectrum analyzers
- Support for R&S CMW500 wireless communication testers
- Support for Keysight 66311B DC power supplies
- Auto-discovery of instrument models via *IDN?
- YAML-driven command configuration
- AI self-learning: explore_scpi, save_learned_command tools
- Project-level command extension via .instrument_mcp/ directory
- MCP stdio transport support

### Known Issues
- Requires external VISA backend (NI-VISA or Keysight IO Libraries)
- Some CMW500 routing scenarios may need manual configuration
