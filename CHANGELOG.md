# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- SerialModbusInstrument 接收路径改为后台线程 + 环形缓冲区：驱动层 RX 缓冲
  持续腾空，transact 按「站号+功能码+CRC」滑窗取帧，帧前噪声丢弃并记日志、
  帧后多余数据保留，避免残余字节导致帧错位与 CH340 断连
- 修复接收线程 read(256) 阻塞导致短帧被扣住整个 timeout（单次读 ~700ms -> ~200ms）
- `_find_frame` 新增 FC=0x10(写多寄存器) 响应帧识别
- 同步到 Loom 框架：ChamberHelper 新增程式控制方法（get_program_status/
  get_program_seg/get_program_table/write_program_seg/set_program_cycle/
  run_program）+ FC=16 协议支持 + 单元测试 16 项全部通过

### Added
- 新增 tinySA / tinySA Ultra+（Zeeenko ZS-407，COM44 实物验证 2026-08）频谱仪支持：
  - `TinySAInstrument` 串口驱动（`instruments.py`）：文本命令 + "ch> " 提示符协议，
    scanraw 二进制解码（dBm = raw/32 - 174）、capture 480x320 RGB565 帧读取；
    兼容 open/close/write/query 接口，`*IDN?` 映射为 info 输出
  - `commands/tinysa.yaml` + `tinysa_handler.py` 共 12 个 MCP 工具：
    tinysa_identity / tinysa_sweep_get / tinysa_sweep_set / tinysa_scan /
    tinysa_scanraw / tinysa_marker / tinysa_pause / tinysa_resume /
    tinysa_freq（定点测电平）/ tinysa_settings / tinysa_capture（截图 BMP）/
    tinysa_raw_command（任意命令透传）
  - 协议实测要点：COM44 为 STM32 USB CDC（VID 0483）传输不限于波特率；
    scanraw 线上字节序为 LSB 在前；capture 帧后跟 "ch> " 提示符
- 修复 YAML 自定义 handler 加载 bug（`commands/__init__.py`）：原 `rsplit(".", 1)`
  不支持 `module.path:func` 写法导致所有 handler 型命令（dslogic/temi880/
  keysight_ps/modbus_chamber/tinysa）执行时报
  `module 'instrument_mcp.commands' has no attribute 'xxx:func'`；现优先按冒号切分
- 新增 `diagnostics/` 诊断子包及 keysight_ps 诊断命令（66300系列通用，实物验证
  66321B GPIB0::5 2026-08）：
  - ps_health_check: 一键只读健康检查，返回综合 verdict。覆盖项: *TST?自检 +
    SYST:ERR?错误队列排空 + *ESR?标准事件解码 + STAT:QUES:COND?/EVEN?保护状态
    解码 + OUTP/VOLT/CURR 设定值 + OVP/OCP保护配置 + MEAS:VOLT?/CURR?测量有效性
    判定(检测 9.91e+37 无效占位符)。verdict: USABLE / WARNING / NEEDS_SERVICE
    (自检非零或错误队列含硬件级错误码1-5/10-15/80，按手册第41页需返修)
  - ps_clear_protection: 清除保护锁存(*CLS + OUTP:PROT:CLE)，可选关闭OVP，
    读回 QUES 确认是否清除成功；若故障源仍存在则清不掉(手册第111页)
  - Questionable状态位映射依据手册 Table 8-5(bit weight权威确认):
    bit0=OV过压, bit1=OCP过流, bit3=FP前面板, bit4=OT过温, bit5=SD/OS传感线开路,
    bit8=UNR2, bit9=RI远程禁止, bit10=UNR, bit12=OC2, bit14=MeasOvld
- SerialModbusInstrument.write_registers(): FC=16 连续写多个保持寄存器（≤100），
  用于程式段表等批量写入
- 温箱程式（程序控制）命令集（modbus_chamber，实物验证 COM30 2026-08）：
  - mc_prog_status: 读取程式运行状态（运行标志/程式号/段号/剩余时间/当前段
    设定/循环次数），返回 JSON
  - mc_prog_read_seg / mc_prog_read_table: 读取指定程式单段或完整段表
    （温度/湿度/时间/循环/TS1..4），返回 JSON
  - mc_prog_write_seg: 修改已有段的设定（温度/湿度/时间/TS），写后读回验证
  - mc_prog_set_cycle: 设置某段的循环次数（每段独立，可写）
  - mc_prog_run: 启动程式运行（写0x0064选程式号+写0x0065=1启动）
  - mc_prog_clear: 清空段表
  - 寄存器映射（实测确认, 与伟硕手册完全不同）：
    程式号选择=0x0064; 运行控制=0x0065(1=运行 4=停止, 程式/定值同值);
    运行态 reg25=程式号 reg26=段号 reg27/28=剩余时/分 reg32=循环(只读镜像)
    reg35/36=段温度 reg37=湿度 reg39=段时间;
    段表分块存储(每块100段, 8块): reg1301=温度 reg1401=湿度 reg1501=时间
    reg1601=循环 reg1701=TS1 reg1801=TS2 reg1901=TS3 reg2001=TS4; 空段=0xB1E0
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
- 温箱程式控制硬件限制（COM30 实测，多轮监测验证）：
  - 段数寄存器(reg41)固件写保护：FC=06/16 回显正常但不持久化，无法 Modbus 新增段
  - 无 Modbus 可访问的触发寄存器（5 轮 40ms 高频监测 reg36-125/reg1200-1312
    均无瞬态；D1003 TRIGGER 为 TEMI880/PC-Link 协议概念，本机未实现）
  - 新增段/删除段/复制程式须在面板操作
  - 已有段的全部参数（温度/湿度/时间/循环/TS1..4）可正常读写并面板同步，
    程式可远程启动（写0x0064选程式号+0x0065=1）
  - 工程化建议：面板建段数充足的模板程式，之后用 Modbus 远程改写段内容
