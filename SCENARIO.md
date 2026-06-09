# 使用场景：射频功率测量

## 背景

工程师小李需要测量一个 2.4GHz WiFi 模块的发射功率。他面前有一台 Keysight MXA N9020A 频谱仪，通过网线连接到公司局域网，IP 是 `192.168.1.50`。

小李不懂 SCPI 命令，也不熟悉频谱仪操作，但他会用自然语言描述需求。

---

## 步骤 1：连接仪器

**小李对 AI 说**：
> "帮我连接频谱仪，地址是 192.168.1.50"

**AI 调用**：
```json
{
  "name": "connect",
  "arguments": {
    "address": "TCPIP0::192.168.1.50::inst0::INSTR",
    "instrument_type": "auto",
    "alias": "mxa"
  }
}
```

**MCP 返回**：
```
[PASS] 已连接: TCPIP0::192.168.1.50::inst0::INSTR
  IDN: Keysight Technologies,N9020A,MY12345678,A.12.34
  识别型号: mxa
  可用命令数: 15
  示例命令: idn, mxa_preset, mxa_set_frequency, mxa_peak_search...
```

**效果**：AI 自动识别出这是 MXA N9020A，加载了对应的 15 个命令。

---

## 步骤 2：设置测量参数

**小李说**：
> "我要看 2.4GHz 附近的信号，扫宽 100MHz"

**AI 调用**：
```json
{
  "name": "mxa_set_frequency",
  "arguments": {
    "alias": "mxa",
    "params_json": "{\"center_hz\": 2400000000, \"span_hz\": 100000000}"
  }
}
```

**MCP 返回**：
```
[PASS] FREQ:CENT 2400000000 Hz, SPAN 100000000 Hz
```

**AI 继续设置参考电平**：
> "参考电平设 10dBm"

```json
{
  "name": "mxa_set_ref_level",
  "arguments": {
    "alias": "mxa",
    "params_json": "{\"level_dbm\": 10}"
  }
}
```

---

## 步骤 3：执行测量

**小李说**：
> "搜索峰值，告诉我功率多少"

**AI 调用**：
```json
{
  "name": "mxa_peak_search",
  "arguments": {
    "alias": "mxa"
  }
}
```

**MCP 返回**：
```
[PASS] Peak: -12.34 dBm
```

**小李**：
> "功率有点低，我再看看详细数据"

**AI 调用读取迹线**：
```json
{
  "name": "mxa_read_trace",
  "arguments": {
    "alias": "mxa"
  }
}
```

---

## 步骤 4：命令调试（遇到不兼容）

**小李换了一台新仪器 N9030B PXA**：
> "换到另一台频谱仪，地址 192.168.1.51"

**AI 连接后发现型号不匹配**：
```
[PASS] 已连接: TCPIP0::192.168.1.51::inst0::INSTR
  IDN: Keysight Technologies,N9030B,MY87654321,B.05.20
  识别型号: generic
  可用命令数: 5
```

**N9030B 的 `mxa_peak_search` 命令执行失败**：
```
[FAIL] CALC:MARK:MAX 执行错误 | 仪器错误: -113,"Undefined header"
```

**AI 自动调试**：
> "命令不兼容，让我查一下错误"

**AI 调用 `debug_last_error`**：
```json
{
  "name": "debug_last_error",
  "arguments": {
    "alias": "pxa"
  }
}
```

**MCP 返回**：
```
[PASS] 调试信息
  失败命令: mxa_peak_search
  参数: {}
  仪器错误队列:
    - -113,"Undefined header"
    - +0,"No error"

  建议:
    1. 检查命令参数类型（如数字不要加引号）
    2. 使用 discover_my_instrument 确认仪器型号
    3. 使用 scpi_query 发送 *IDN? 确认通信正常
    4. 查阅仪器编程手册确认 SCPI 语法
```

**AI 手动探索正确语法**：
> "让我试试 PXA 的峰值搜索命令"

```json
{
  "name": "scpi_query",
  "arguments": {
    "alias": "pxa",
    "params_json": "{\"command\": \"CALC:MARK1:MAX?\"}"
  }
}
```

**成功！AI 记录正确命令**：
> "PXA 用 `CALC:MARK1:MAX?` 而不是 `CALC:MARK:MAX`，我记下来了"

---

## 步骤 5：迭代命令配置

**小李或 AI 创建新配置文件** `commands/pxa.yaml`：

```yaml
instrument_type: pxa
description: Keysight PXA N9030B 频谱仪

model_keywords:
  - "N9030B"
  - "PXA"

commands:
  - name: pxa_peak_search
    description: PXA 峰值搜索
    annotations:
      readOnlyHint: true
    params: []
    scpi_template:
      write: "CALC:MARK1:MAX"
      query: "CALC:MARK1:Y?"
```

**重启 MCP 后**，N9030B 自动识别为 `pxa`，使用正确的命令集。

---

## 效果总结

| 传统方式 | 用 MCP + AI |
|---------|------------|
| 查 SCPI 手册 30 分钟 | 自然语言描述，AI 自动调用 |
| 手动输入 `:FREQ:CENT 2.4GHz` | 说"设中心频率 2.4GHz" |
| 换仪器后命令不兼容，调试半天 | AI 自动识别型号，失败时自动诊断 |
| 命令记在脑子里或 Excel | 命令配置在 YAML，版本可控，团队共享 |
| 新人学习成本高 | 新人直接对话操作 |

---

## 适用人群

- **射频工程师**：快速验证，不用记 SCPI
- **测试技术员**：按 SOP 对话执行测试步骤
- **实习生/新人**：自然语言学习仪器操作
- **远程支持**：AI 协助排查，读取错误队列分析
