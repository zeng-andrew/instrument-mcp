# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

_Nothing yet._

## [0.3.0] - 2026-09-18

_Feature release: CMW500 LTE 深度接入（信令 + Meas 测量/CQI 调度共 97 条工具）与 MCP 全链路打通_

### Added
- CMW500 LTE Signaling 能力扩至 84 条（2026-09-17/18 固件 3.7.110 真机逐条验证）：
  频段/信道切换（Blind Handover/Redirection 机制实测）、连接生命周期
  （CONNect/DISConnect/DETach 与 Event Log 映射）、专用承载预配、信号路径
  （RF COM）路由、TPC 上行功率控制、UE 能力/频段查询、吞吐测量等
- CMW500 参数关联 handler 三个：cmw_lte_snapshot（一键面板快照）、
  cmw_set_dl_rb（带宽优先联动，自动规避 -203）、cmw_set_ul_rb（自动开
  QAM 支持开关，规避 -221）
- CMW500 LTE Meas 测量应用与 CQI 动态调度接入（cmw.yaml 共 97 条，2026-09-18 实测）：
  - 场景路由：CSPath（信令+测量共用信号路径，master 用信令应用全名）/SALone
  - MEValuation 状态机（INIT/STOP/STATe）、信道类型/触发源配置
  - cmw_meas_tx_report：调制质量 29 字段（EVM/幅相误差/频偏/TX 功率/IQ 失衡）
    + ACLR + 频谱平坦度 + 带内发射格式化报告，NAV/NCAP 占位容错
  - cmw_meas_run_once：一键单发 UE 上行测量（实测样例：EVM RMS≈2%、
    频偏 1.6Hz、ACLR 47~55dB）
  - cmw_cqi_set / cmw_cqi_stats：CQI 调度切换与远程观察窗口（CQI 中位值
    统计 + CQI→MCS 映射表）；cmw_init_ebler
- 新增 MCP 层集成验证 `tests/cmw_mcp_integration_test.py`：经
  FastMCP.call_tool 真实客户端路径驱动真机（注册完整性/VISA 连接/
  Meas/CQI 工具/场景往返），首跑即发现下述三个服务器层问题
- 冒烟回归 `tests/cmw_handler_smoke_test.py` 扩至 7 个零扰动用例，
  `--live` 追加真实单发测量；SCPI 探针 `tests/cmw500_probe.py` 新增
  meas/cqi 内置批次

### Fixed
- INSTRUMENT_REGISTRY 缺 cmw 条目：显式 instrument_type="cmw" 与 auto
  识别均被拒——CMW500 从未真正通过 MCP server 连接过（集成验证发现）
- TCPIP SOCKET 资源未设读写终止符：NI-VISA raw socket 无协议层 END，
  viRead 即使数据已在缓冲也挂到超时；仅对 SOCKET 地址启用 `\n` 终止符，
  不影响 VXI-11/HiSLIP 仪器
- SCPI 模板在空 params_json 时不格式化，{placeholder} 原样发给仪器
  （影响所有带默认参数的 YAML 命令，写入却报 [PASS]）；现一律 format
  并合并 YAML 默认值，显式 None 回落默认值，必填参数缺失显式报错
- cmw_meas_tx_report/run_once 增加可靠性指示器检查：实测无上行信号时
  单发约 1s 即 RDY 输出全 INV 数据（可靠性=26），不会等触发——报告对
  可靠性非 0 显式告警
- 修正上轮错误记录：INST:SEL 只能选测量类应用（信令应用写 SIG 报 -200）；
  LTE:SIGN 与 LTE:MEAS 两棵树可同时寻址、无需切应用；删除直写不合法的
  场景路由占位命令 cmw_set_meas_port/cmw_set_sign_port
- cmw500_probe.py 批量探测响应错位：每条指令前排空 socket 残留、
  超时 1.5s→4s（场景切换等重写命令阻塞 2s+）

### Docs
- 新增 docs/cmw500_lte_signaling.md：LTE 信令接入手册（面板项→SCPI 速查、
  连接生命周期、频段切换机制、参数关联规则）
- 新增 docs/cmw500_lte_meas.md：测量应用与 CQI 调度手册（双树架构、
  CSPath 联动流程、结果字段格式、CQI 掉链风险与"先切后连"重试流程、
  GUI 观察流程）
- CLAUDE.md 新增 CMW500 章节（连接方式/核心工具/已验证怪癖）

### Known Issues
- CMW500 CQI 调度：连接中（CEST）切换实测 7s 后 UL out of Sync 掉链，
  且模组 >60min 未自主重附（对比干净 Detach 后 4.6min；小区重启也唤不醒，
  疑深度 PSM，需人工重启模组）。推荐无连接时先配置再连接（流程见文档）

## [0.2.0] - 2026-09-16

_Feature release: 米家智能插座 MCP 集成_


### Fixed
- `mc_run` / `mc_stop` / `mc_read_status` 运行标志判断 `== 1` 改为 `!= 0`
  （实物验证 reg0x000A 停止=0、运行≠0）。
  修复前 `mc_run` 即使启动成功也报 `[FAIL]`、`mc_read_status` 运行中报
  `running=false`。
- 纠正寄存器映射注释中关于"写 0x0065=2 启动程式"的错误表述：本控制器
  程式与定值运行均写 1，区别在于是否先写 0x0064 选定程式号。
- 修正运行标志结论（COM43 差分扫描实测 2026-08）：reg0x000A 实为
  0=停止 1=定值运行 2=程式运行（此前误记"定值/程式运行均为 2 无区别"），
  `mc_read_status` / `mc_prog_status` 据此输出 run_state 区分运行态。
- 修复 `mc_prog_run` 在面板处于定值模式时静默变成定值运行且误报 PASS
  的问题：启动前先写 reg0x0068=0 切程式模式，启动后以 reg10=2 验证
  确为程式运行，否则明确报 FAIL。

### Changed
- CH340 error 31 规避从「修改 `.venv` 的 `serialwin32.py` + 运行补丁脚本」
  改为 `SerialModbusInstrument` 导入时运行时 monkeypatch（`_patch_ch340_error31()`）。
  修复随代码走，重建虚拟环境后无需任何额外操作，不再依赖 venv 文件补丁。
  实测 SetCommState 原样写回 10/10 次持续误报 error 31（端口实际可用），
  故靠 open() 重试无法自愈，必须在 pyserial 配置阶段忽略该错误。
- `open()` 移除冗余的 baud-kick（4800/2400 唤醒）：该逻辑原本为 error31
  设计，现 error31 已由运行时补丁在上游解决，首次打开即成功，baud-kick
  沦为永不触发的死代码（实测有无 baud-kick 成功率/耗时无差异）。重试次数
  从 6 次减为 3 次，仅保留对端口被占用等其他打开失败的退避。
- SerialModbusInstrument 接收路径改为后台线程 + 环形缓冲区：驱动层 RX 缓冲
  持续腾空，transact 按「站号+功能码+CRC」滑窗取帧，帧前噪声丢弃并记日志、
  帧后多余数据保留，避免残余字节导致帧错位与 CH340 断连
- 修复接收线程 read(256) 阻塞导致短帧被扣住整个 timeout（单次读 ~700ms -> ~200ms）
- `_find_frame` 新增 FC=0x10(写多寄存器) 响应帧识别
- 同步到 Loom 框架：ChamberHelper 新增程式控制方法（get_program_status/
  get_program_seg/get_program_table/write_program_seg/set_program_cycle/
  run_program）+ FC=16 协议支持 + 单元测试 16 项全部通过

### Added
- 米家智能插座2 接入 MCP server（instrument_type `mi_plug`，LAN miIO/MIoT，
  python-miio/requests 转为主依赖）：新增 miplug_info/status/on/off/toggle、
  miplug_loop_start/loop_stop/loop_info（设备端循环定时）、
  miplug_get_property/set_property（任意 siid/piid 属性读写）命令
- 新增米家云端扫码提取 token（miplug_token_qr_start / miplug_token_qr_finish，
  `src/instrument_mcp/mi_cloud.py`）：免密码/免邮箱 2FA，成功后自动写入
  gitignored 的 mi_plug_config.json，适配企业网云端 TLS 被拦截的环境
- 温箱面板模式远程控制（COM43 寄存器差分扫描发现并实测验证 2026-08）：
  reg0x0068(104)=面板模式寄存器（0=程式 1=定值），停止时可写、写入后
  面板立即同步切换（双向面板核对验证）。
  - 新增 `mc_set_mode`：切换程式/定值模式（运行中拒绝切换）
  - `mc_run` 增强为"先写 0x0068=1 切定值模式再启动"，以 reg10=1 验证
  - `mc_prog_run` 增强为三步序列：0x0068=0 切程式模式 → 0x0064=程式号
    → 0x0065=1 启动，以 reg10=2 验证（定值模式出发亦可靠启动程式）
  - `mc_read_status` 新增 run_state（停止/定值运行/程式运行）与
    panel_mode（程式/定值）字段
  - 新增 `tests/mode_scan.py` 差分扫描工具（dump/diff/stop/start/
    progstart/read/write，快照存 tests/dumps/），复现本次实验流程
  - 同步到 Loom 框架（ChamberHelper）：run_program 启动序列加"写
    reg0x0068=0 切程式模式"并以 reg10=2 验证；run 先切定值模式；
    新增 set_mode（运行中拒绝切换）；幂等/冲突判定以程式运行标志==2
    为准（定值运行时程式号寄存器是残留值，不可作幂等依据）；假从机
    responder 按真固件行为建模（0x0065=1 按 0x0068 模式置位标志），
    单元测试 35 项全部通过
- tinySA Ultra mode 解锁流程（COM44 实测验证 2026-08）：默认 `config.ultra=false`
  时 `sweep` 上限被钳制在 900 MHz（固件源码 sa_core.c `update_min_max_freq()`），
  `ultra on` + `saveconfig` 解锁至 ~12 GHz（harmonic=3，`harmonic×MAX_LO_FREQ−IF`），
  重启持久（实测确认）；菜单方式需解锁码 4321。Ultra 模式扫描较慢（镜像/杂散
  消除，3-12G 101 点 ≈16s）、输入端口 LO 泄漏增大。谐波测试流程见
  docs/tinysa_zs407.md 第 6/7 节
- tinySA 测量/校准类专用工具（COM44 实物验证 2026-08，tinysa.yaml/tinysa_handler.py）：
  - tinysa_data: 读取轨迹（0=当前/1=已存储/2=测量，dBm 每行一个，点数=扫描点数）
  - tinysa_frequencies: 上次扫描频点列表；tinysa_status: 设备状态(Resumed/Paused)
  - tinysa_trigger: auto/normal/single/触发电平(dBm)；tinysa_trace: 单位/量程/
    参考电平设置与读取（格式 `{id}: {unit} {reflevel} {scale}`）+ store/clear/subtract
  - tinysa_hop: Ultra 多点定点测量（第3参<450为点数否则步进Hz，输出 N+1 行含两端点）
  - tinysa_vbat: 电池电压；tinysa_caloutput: 校准信号输出(off/1..30 MHz)
  - tinysa_leveloffset: 电平校准表读写（22 项）；tinysa_correction: 频率-电平
    校正表读写（low/lna/ultra/... 12 张表，每表 20 点）
  - 实测要点: trigger/caloutput 无参只回用法（无当前值回读）；frequencies 点数=
    当前扫描点数（非全频点表）
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

### Docs
- 新增 docs/agilent_66319d_coupling_off.md：66319D 双通道独立供电实测与耦合
  NONE/ALL 关闭流程（`INST:COUP:OUTP:STAT`、持久化 `*SAV 0` +
  `OUTP:PON:STAT RCL0` 及副作用、命令速查、本机实测记录）；README 与实验
  脚本（tests/ps_66319d_dual_channel_test.py，8/8 通过）同步
- docs/miplug_chuangmi_212a01.md：补充 MCP 集成命令、云端 QR 取 token 流程
  与实测属性坑位

### Known Issues
- Requires external VISA backend (NI-VISA or Keysight IO Libraries)
- Some CMW500 routing scenarios may need manual configuration
- 温箱程式控制硬件限制（COM30 实测，多轮监测验证）：
  - 段数寄存器(reg41)固件写保护：FC=06/16 回显正常但不持久化，无法 Modbus 新增段
  - 无 Modbus 可访问的触发寄存器（5 轮 40ms 高频监测 reg36-125/reg1200-1312
    均无瞬态；D1003 TRIGGER 为 TEMI880/PC-Link 协议概念，本机未实现）
  - 新增段/删除段/复制程式须在面板操作
  - 已有段的全部参数（温度/湿度/时间/循环/TS1..4）可正常读写并面板同步，
    程式可远程启动（写0x0068=0切程式模式+0x0064选程式号+0x0065=1，
    reg10=2 验证）
  - 工程化建议：面板建段数充足的模板程式，之后用 Modbus 远程改写段内容
