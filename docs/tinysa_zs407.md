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
| `data 0..2` | 每行一个电平(dBm，科学计数)，点数=当前扫描点数；0=当前值 1=已存储 2=测量 |
| `frequencies` | 每行一个频点(Hz)，点数=当前扫描点数（非全频点表） |
| `status` | 实测 `Resumed`（暂停时 `Paused`） |
| `trigger auto|normal|single|{dBm}` | 设置触发；**无参只回用法（无当前值回读）** |
| `trace` | 无参返回 `{id}: {unit} {reflevel} {scale}`，如 `1: dBm 0.000000000 10.000000000` |
| `trace dBm|dBmV|dBuV|V|W` | 设显示单位；`trace store/clear/subtract`；`trace scale|reflevel auto|{level}` |
| `hop {start} {stop} {points|step} {outmask}` | Ultra 专用多点测量；第 3 参 <450 视为点数否则步进(Hz)；**输出 N+1 行含两端点**；outmask 1=频率 2=电平 3=两者 |
| `vbat` | 返回 `{n} mV`，如 `4247 mV` |
| `caloutput off|30|15|10|4|3|2|1` | 校准信号输出(MHz)；**无参只回用法** |
| `leveloffset` | 无参输出全表: 每行 `leveloffset {key} {value}`（low/low_output/switch/lna/direct/ultra 等 22 项） |
| `correction` | 无参回用法；`correction {table}` 输出 `index frequency value` 表（low/lna/ultra/... 每表 20 点） |
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

## 6. Ultra mode 解锁（关键：900 MHz 上限 → 12 GHz）

**现象**：`sweep` 设置上限被钳制在 900 MHz（超出静默截断为 900 MHz，不报错），
但 `scan`/`scanraw`/`hop`/`freq` 单次测量不受限（可测到 ≥3 GHz）。

**原因（固件源码确认，sa_core.c `update_min_max_freq()`）**：
- `config.ultra == false`（**出厂默认**）→ `maxFreq = NORMAL_MAX_FREQ`（Ultra+ 为
  900 MHz，正常模式输入低通滤波器限制）
- `config.ultra == true` → 无谐波时 `maxFreq = ULTRA_MAX_FREQ`（~6.3 GHz）；
  `setting.harmonic`（TINYSA4 默认 3，sa_core.c:652）→
  `maxFreq = harmonic × MAX_LO_FREQ − IF` ≈ **12 GHz（harmonic=3）/ 20 GHz（=5）**
- wiki 规格书：normal mode 100kHz-800MHz（Ultra+ 900MHz）；ULTRA mode 至 6GHz
  （ZS407 为 7.3GHz 校准上限）；wiki 页面：最高 12GHz（HARMONIC 3 默认）

**启用（串口）**：
```
ultra on          # 内存生效（cmd_ultra 不自动保存！）
saveconfig        # 必须手动保存，重启后才保持（实测验证）
```
- 菜单方式：CONFIG/MORE/ENABLE ULTRA，需输入解锁码 **4321**（ui.c:2840），
  菜单方式自动 `config_save()`
- `ultra off|on|auto|start {freq}|harm {freq}` 查看用法：`ultra`
- **注意**：boot 后 `sweep` 显示 0-900 MHz 只是"扫描设置"未持久化（setting 结构
  不随 saveconfig 保存），频率上限已解锁；开机后执行 `tinysa_sweep_set`
  设 3G-12G 即可。要持久化为开机预设可用 `save 0`

**Ultra 模式代价**（wiki）：
- 每个频点多测数次（镜像/杂散消除算法）：3-12 GHz 101 点实测 15.8 s；
  0-6 GHz 全扫 ≈ 14 s
- 捕捉不到极短/扫描/宽带(>1MHz)信号；输入端口 LO 泄漏增大（可达 -10 dBm）
- 2.5 GHz 以上灵敏度约低 10 dB，5.3 GHz 以上约低 25 dB；校准至 6 GHz
  （ZS407 7.3 GHz），6 GHz 以上无频率校正、灵敏度急剧下降

## 7. 谐波测试流程（实测可用）

1. 确认已解锁：`ultra on` + `saveconfig`（一次性）
2. DUT 信号接 RF 输入，**保持 < -30 dBm**（避免过载/互调，保 SFDR；建议
   attenuate auto）
3. 设扫描范围覆盖基波至目标谐波：如 `sweep 3G 12G 101` 或直接用
   `tinysa_scan(start_hz=3e9, stop_hz=12e9, points=101)`
4. 识别 n×f0 处的峰值（每个峰对应基波的 n 次谐波）
5. RBW 默认（10-30 kHz）即可；spur removal AUTO 在 ultra 模式自动启用
   （`spur on/off` 可手动控制）
6. 例（40 MHz 信号，wiki）：范围设到 ≥250 MHz，可看到 80/120/160/200/240 MHz
   谐波峰，DDS/SI5351/ADF4351 源的谐波分布特征不同
