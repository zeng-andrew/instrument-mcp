# 可用命令列表

## 仪器注册表 (`INSTRUMENT_REGISTRY`)

| 类型标识 | 仪器类 | 描述 |
|----------|--------|------|
| `mxa` | `KeysightMXA` | Keysight MXA N9020A 频谱仪 |

## 高层命令注册表 (`COMMAND_REGISTRY`)

### 通用命令（所有仪器可用）

| 命令名 | 参数 | 说明 |
|--------|------|------|
| `idn` | 无 | 读取仪器标识 `*IDN?` |
| `scpi_write` | `command: str` | 发送 SCPI 写命令 |
| `scpi_query` | `command: str` | 发送 SCPI 查询命令 |

### MXA 专用命令

| 命令名 | 参数 | 说明 |
|--------|------|------|
| `mxa_preset` | 无 | 恢复预设状态 `*RST` |
| `mxa_set_frequency` | `center_hz: float`, `span_hz: float` | 设置中心频率和扫宽 |
| `mxa_peak_search` | 无 | 执行峰值搜索并返回幅度 |

## MCP Tools（直接暴露给 AI 的接口）

| Tool | 说明 |
|------|------|
| `connect(address, instrument_type, alias)` | 连接仪器 |
| `disconnect(alias)` | 断开仪器 |
| `list_sessions()` | 列出活跃连接 |
| `scpi(alias, command, query)` | 通用 SCPI 透传 |
| `run_command(alias, command, params)` | 执行注册的高层命令 |
| `mxa_preset(alias)` | MXA 预设（便捷入口） |
| `mxa_set_frequency(alias, center_hz, span_hz)` | MXA 设频（便捷入口） |
| `mxa_peak_search(alias)` | MXA 峰值搜索（便捷入口） |
