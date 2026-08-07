# tinySA / Zeeenko ZS-407 频谱仪串口控制实录

> 实物验证: 2026-08, ZS-407 (TinySA ULTRA+), 固件 `tinySA4_v1.4-217-gc5dd31f`, COM44。
> 官方协议文档: https://tinysa.org/wiki/pmwiki.php?n=Main.USBInterface

## 1. 连接方式

- COM44 是 STM32 **USB CDC** 虚拟串口（`USB\VID_0483&PID_5740`），不是 CH340
  桥接芯片——**波特率设置仅示意，数据按 USB 速度传输**（capture 307200 字节
  实测约 0.5s 传完，约 570 KB/s）。若改用 UART 引脚接线（RX/TX/GND），
  115200 波特率有效。
- 因 USB CDC 突发速度快，pyserial 默认 4096 字节接收缓冲会溢出丢字节，
  `TinySAInstrument.open()` 里用 `set_buffer_size(rx_size=4MB)` 放大，
  读大块数据用阻塞式 `read(n)` 紧循环（不要用 in_waiting + sleep 轮询）。
- 文本命令以 `\r\n` 结尾；设备先**回显命令**，响应以 **`ch> ` 提示符**结束。

## 2. 实测确认的命令行为

| 命令 | 实测结果 |
| --- | --- |
| `version` | `tinySA4_v1.4-217-gc5dd31f` + `HW Version:V0.5.4 max2871` |
| `info` | 第一行 `tinySA ULTRA+ ZS407`，含固件/构建时间/平台 STM32F303xC |
| `sweep` | 无参列出 `{start} {stop} {points}`，如 `100000001 900000000 450` |
| `sweep center 300M` / `sweep span 100M` / `sweep cw 350M` | 生效，k/M/G 后缀可用 |
| `sweep start 100M` / `sweep stop 500M` | 生效 |
| `sweep 200000000 400000000` | 直接设起止 |
| `scan {start} {stop} {points} {outmask}` | 每点一行；outmask=3 输出 3 列: `freq level stored`；outmask=2 只有 2 列(无频率)。**点数上限 290** |
| `scanraw {start} {stop} {points}` | 二进制；无点数上限（实测 2000 可用），耗时随 RBW/跨度 |
| `marker 1 peak` | 返回 `{id} {idx} {freq} {level}`，如 `1 444 5033333333 -8.47e+01` |
| `marker 1 on/off` | 无输出；`marker {id} {freq}` **无回读输出**，电平须另测 |
| `freq 350M` | 暂停扫描并设测量频率（无输出） |
| `data 1` | 450 行电平（最后完整扫描的数据） |
| `frequencies` | 设备全部频点表（远超当前扫描范围） |
| `status` | 如 `Paused` / `Running`（实测显示 `Paused`） |
| `capture` | 纯二进制: 回显 + 307200 字节 RGB565 + `ch> ` 提示符 |
| `capture rle` | 本固件不支持 RLE（原样发原始帧） |

## 3. 关键二进制格式（实测与文档有出入处）

### scanraw

文档声称 `'{' ('x' MSB LSB) '}'`，**实测线上字节序为 LSB 在前**：

```
{ x LSB MSB  x LSB MSB  ...  }
```

- 电平换算（Ultra 系）：`dBm = raw/32 - 174`
- 验证: 200 MHz 处原始值 `0x0AB2` → `0x0AB2/32 - 174 = -88.44 dBm`，
  与同一频率文本 scan 的 `-88.4375 dBm` 完全一致
- 提示符安全：正常电平范围 raw ≈ 0x0A80~0x0B00，高字节远小于 `0x68`，
  数据中不可能出现 `ch> ` (0x63 0x68 0x3E 0x20) 序列，可按提示符截断解析

### capture

- 屏幕 480x320（TINYSA4 定义，ST7796S），像素 RGB565 小端（2 字节/点），
  共 307200 字节，帧尾紧跟 `ch> `
- 截图中 "ch> " 序列**可能**出现在像素数据内，因此读取必须按固定字节数
  `read_exact(307200)`，不能按提示符截断
- MCP 侧已实现 RGB565 → 24-bit BMP 转换（`tinysa_capture`，纯 Python 无依赖）

## 4. 扫描速度参考

- 与 RBW、跨度强相关：RBW 100 kHz + 跨度 100M-500M 时约 0.16 s/点；
  建议默认点数 101，跨度过大或窄 RBW 时预留充足超时（handler 按
  `max(10, 3 + points*0.03)` 秒计算）
- 增大 `repeat`（1~1000）可降低噪声，但按比例变慢

## 5. 排查

- `tinysa_raw_command` 可透传任意命令（如 `help` 列出全量命令）自助探索
- 若响应读不到 `ch> `，检查是否被其他软件占用 COM44；USB CDC 拔插后需重连
