# CMW500 LTE Meas 测量应用与 CQI 动态调度接入手册

> 2026-09-18 实测沉淀。整机固件 3.7.110，LTE 测量/信令应用 3.7.30.17。
> 与 `docs/cmw500_lte_signaling.md`（信令树）配套，本文覆盖 LTE:MEAS 指令树、
> 信令+测量联动、CQI 动态调度与 GUI 观察流程。
> 指令集参考：R&S 官方驱动 `RsCmwLteMeas` / `RsCmwLteSig`（PyPI；驱动 4.0 比本机
> 固件新，个别指令如 `TRIG:...:CATalog?` 在本固件不存在，以下全部以真机为准）。

## 应用架构：两棵树 + 双应用实例

CMW500 上 LTE 信令（LTE:SIGN 树）与 LTE 测量（LTE:MEAS 树）是两个独立应用实例：

- 本机实例：信令应用显示名 **SIG**、测量应用 **MEAS**、另有 IQ 分析 **IQSETUPTEST**
  （`INST:SEL?` 查当前聚焦；应用目录 `INST:CATalog?` 返回空串，枚举不可用）
- **`INST:SEL` 只能选测量类应用**：`INST:SEL MEAS` / `INST:SEL IQSETUPTEST` 可用；
  信令应用各种形式（SIG / LTE Sig1 / 带引号）均被拒（-200 或 -158/-103）——信令应用
  只能 GUI 选中，且掉链后仪器会自动把焦点切回信令应用（此时 `INST:SEL?` 又变 SIG）
- **关键：两棵指令树不需要切应用即可同时寻址**。选着 MEAS 时
  `CALL:LTE:SIGN:PSWitched:ACTion CONNect` 照常生效（实测 1s 内 ATT→CEST）。
  信令+测量联动 = CSPath 场景 + 两棵树各发各的，全程无需 INST:SEL

## 连接与场景路由（信令+测量联动核心）

```
ROUTe:LTE:MEAS:SCENario?                       -> SAL（独立）| CSP（共用信令路径）
ROUTe:LTE:MEAS:SCENario:CSPath "LTE Sig1"      切 CSP；master=信令应用全名（引号包裹）
ROUTe:LTE:MEAS:SCENario:CSPath?                -> "LTE Sig1","PCC"
ROUTe:LTE:MEAS:SCENario:SALone RF1C,RX1        切独立场景并指定 RF 输入
```

- master 必须是**信令应用完整应用名**（本机 `LTE Sig1`）：写别名 `SIG` 报
  -222 Data out of range；名字带空格所以必须加引号
- `ROUT:LTE:MEAS:SCENario <枚举>` **直写不合法**（-113），必须走 SALone/CSPath 子命令
- 切换耗时 2s+（串行发命令时 err 回读要给足超时），切换对信令连接**无扰动**（实测）
- CSP 下 Meas 的频率/电平自动跟随信令（RFSettings:ENPower 保持 0 不影响测量）

标准联动流程（全流程 2026-09-18 实测通过）：

```
1. 信令开小区、UE 附着（见 cmw500_lte_signaling.md）
2. ROUT:LTE:MEAS:SCENario:CSPath "LTE Sig1"      Meas 挂到信令路径
3. CALL:LTE:SIGN:PSWitched:ACTion CONNect        UE 到 CEST（连续 UL PUSCH 出现）
4. INIT:LTE:MEAS:MEValuation                     单发测量，~1s 完成
5. FETCh:LTE:MEAS:MEValuation:*                  读结果（RDY 后可反复读）
```

## MEValuation（多评估）主测量

状态机：`OFF --INIT--> RUN --完成--> RDY`（`FETC:LTE:MEAS:MEValuation:STATe:ALL?`
返回 `状态,调整标志,触发标志`，如 `RDY,ADJ,INV`）；单发（REPetition=SING）约 1s。

配置要点（均实测）：

| 项 | SCPI | 默认/实测值 |
|---|---|---|
| 信道类型 | `CONF:LTE:MEAS:MEValuation:CTYPe` | PUSC(h)（RMC/CQI 调度的 UL 都是 PUSCH） |
| 触发源 | `TRIG:LTE:MEAS:MEValuation:SOURce "IF Power"` | "IF Power"（带空格，引号包裹） |
| 触发间隔 | `TRIG:LTE:MEAS:MEValuation:MGAP` | 2 |
| 重复模式 | `CONF:LTE:MEAS:MEValuation:REPetition` | SING |
| 中止/停止 | `ABORt` / `STOP:LTE:MEAS:MEValuation` | 均实测可用 |

结果查询（`:CURRent|:AVERage|:MAXimum|...` 统计档后缀，逗号分隔字段，实测响应）：

```
FETCh:LTE:MEAS:MEValuation:MODulation:AVERage?   29 字段（调制质量主结果）
FETCh:LTE:MEAS:MEValuation:ACLR:AVERage?          8 字段
FETCh:LTE:MEAS:MEValuation:ESFLatness:AVERage?   10 字段
FETCh:LTE:MEAS:MEValuation:IEMission:CC1:MARGin:AVERage?   3 字段
FETCh:LTE:MEAS:MEValuation:SEMask:*?              列表型（边沿多档裕量，建议原样透传）
```

字段顺序（与 RsCmwLteMeas ResultData 一致，handler `cmw_meas_tx_report` 已封装解析）：

- **MODulation 29 字段**：可靠性、超限率%、EVM RMS/峰值（低/高窗）、幅度误差 RMS/峰值、
  相位误差 RMS/峰值、IQ 偏移 dBc、**频偏 Hz**、时偏 Ts、**TX 功率 dBm**、峰值功率 dBm、
  PSD、EVM/幅误/相误 DMRS、IQ 增益失衡、IQ 正交误差、EVM_SRS
- **ACLR 8 字段**：可靠性、UTRA2/UTRA1/EUTRA 邻道（低侧）dB、**载波信道功率 dBm**、
  EUTRA/UTRA1/UTRA2 邻道（高侧）dB
- **占位值**：测量未跑出数据时字段为 `NAV`；本固件上 MODulation 的幅度/相位误差字段与
  独立的 `MERRor:*`/`PERRor:*` 查询实测返回 `NCAP`（QPSK+该固件组合，逐符号误差
  统计不可用）；DMRS/SRS 相关字段在无对应信号时 NCAP/INV——解析须容错

实测样例（Cat.1 模组，UL RMC N1/QPSK，TPC 顶格发射）：

```
TX功率 23.25 dBm / 峰值 27.47 dBm；EVM RMS 1.9~2.3%、EVM DMRS 1.5%
频偏 1.6 Hz；IQ 偏移 -50 dBc
ACLR: EUTRA 邻道 47.6~47.8 dB、UTRA 邻道 49~55 dB（信道功率 23.15 dBm）
带内发射余量 +19.5 dB；频谱平坦度波纹 0.81 dB —— 全部远优于 3GPP 限值
```

ATT（ECM-Idle）空闲态 UE 上行只有零星突发，IF Power 触发可能一直等不到 →
测量超时。**先 CONNect 到 CEST 让 RMC 上行连续调度**，测量才稳定可复现。

## CQI 动态调度（含踩坑实录）

```
CONF:LTE:SIGN:CONNection:PCC:STYPe CQI,<mode>    mode: TTIBased|FWB|FPMI|FCPRi|FCRI|FPRI
CONF:LTE:SIGN:CONNection:PCC:FWBCqi:DL <RB>,<起始RB>,DET|UDEF    DL 配置（mode≠TTIBased 时）
SENSe:LTE:SIGN:CONNection:PCC:FWBCqi:DL:MCSTable:DETermined?    读自动 CQI→MCS 映射表
INITiate:LTE:SIGN:EBLer                          启动 eBLER 测量（CQI 统计视图的数据源）
FETCh:LTE:SIGN:EBLer:PCC:CQIReporting:STReam1?   CQI 上报统计（7 字段）
CONF:LTE:SIGN:CQIReporting:PCC:CINDex            CQI 上报周期配置索引（本机 0=最密）
```

- `STYPe?` 在 CQI 模式下返回 `CQI,FWB`；FWB = DL 调制编码方案跟随 UE 宽带 CQI 上报
- **CQI→MCS 映射表（DETermined）** 本机实测：`0,0,2,4,6,8,11,13,16,18,21,23,25,27,27`
  （首项可靠性，其后 CQI 低→高对应 MCS，上限 27）
- **CQIReporting:STReam1 7 字段**：可靠性、**CQI 中位值**、中位±1 报告数、占比%、
  DL BLER%、累计报告数、已调度子帧数。eBLER 测量 OFF 时不累积（全 NAV）

### ⚠ 连接中切换 CQI 会导致掉链（实测）

2026-09-18 Event Log 实录（Cat.1 模组，CEST 连接中发 `STYPe CQI,FWB`）：

```
14:29:32  Wideband CQI Reports ON        <- RRC 重配，切换本身成功（CEST 保持）
14:29:39  WARN "UL out of Sync"          <- 7 秒后上行失步
14:29:39  State 'Cell On', 1CC 1x1       <- 连接释放，UE 掉网（PS: CEST→ON）
```

掉链后的重附行为与干净 Detach **完全不同**（同日对比实测）：

| 掉链方式 | 自主重附耗时 |
|---|---|
| 网络 Detach（上轮实测） | 4.6 min（休眠周期性搜网） |
| **CQI 重配致 UL out of Sync（本次）** | **>60 min 未归**，期间小区重启（OFF→ON）也未唤醒 |

推测该 RRC 重配把模组推进了深度 PSM 或卡死了协议栈，**大概率需要人工断电重启
模组**。CQI 首切本身已验证成功（CEST 保持、`Wideband CQI Reports ON`、eBLER
窗口 2s 收到 50 条 CQI 上报、中位值 15），待模组恢复后按"先切后连"路径重试：

```
1. 确认 PS=ATT/ON 且模组可用（cmw_wait_ue_state）
2. 无连接时 CONF:LTE:SIGN:CONNection:PCC:STYPe CQI,FWB（cmw_cqi_set）
3. INIT:LTE:SIGN:EBLer
4. CALL:LTE:SIGN:PSWitched:ACTion CONNect（让 CQI 配置随 RRC 建立下发）
5. 观察 PS 是否稳定 CEST（若再掉链 = 该模组不支持 CQI 调度，也是结论）
6. 稳定后改 RSEPre 看 CQI 中位值移动（cmw_cqi_stats），恢复时 STYPe RMC
```

恢复静态调度：`STYPe RMC`。MCP 侧 `cmw_cqi_set` 已内置此警告与恢复指引。

### 远程验证"CQI 驱动 MCS"的观察路径

1. `INIT:LTE:SIGN:EBLer` 后读 `CQIReporting:STReam1?` → CQI 中位值（如强信号时 15）
2. 改下行小区功率 `CONF:LTE:SIGN:DL:PCC:RSEPre:LEVel`（如 -75→-85）→ 中位值应下移
3. 对照 `MCSTable:DETermined?` 映射表 → 该 CQI 档位对应的 DL MCS 即调度器所用值
4. GUI 同屏观察见下节

## SYST:DISP:UPD ON + GUI 观察自动化

```
SYSTem:DISPlay:UPDate ON     GUI 实时跟随远程修改（SYST:DISP:UPD? 查询返回 1/0）
```

- 人机协同调试标准姿势：先 `SYST:DISP:UPD ON`，之后所有 SCPI 修改同步在 GUI 上，
  看面板变化 = 看远程效果（CQI 模式下 Connection 页 RB/调制指示、Meas 应用的
  MultiEval 图形都会动）
- 上位机自动化采集 + 人工 GUI 盯屏互不干扰：SCPI socket 与 GUI 操作可并行，
  脚本改参数（如 RSEPre 阶梯）→ GUI 实时显示 CQI/MCS 跟随 → 异常随时人切手动
- 注意 GUI 聚焦应用会被事件自动切换（掉链切回信令应用），INST:SEL? 可确认当前焦点

## MCP 工具（本轮新增，cmw.yaml 共 97 条）

- `cmw_meas_query_scenario` / `cmw_meas_set_scenario_cspath` / `cmw_meas_set_scenario_salone` —— 场景路由
- `cmw_meas_query_state` / `cmw_meas_init` / `cmw_meas_stop` —— 状态机控制
- `cmw_meas_set_ctype` / `cmw_meas_query_ctype` / `cmw_meas_set_trigger_source` —— 测量配置
- `cmw_meas_tx_report` —— 全套结果格式化报告（只读，NAV/NCAP 容错）
- `cmw_meas_run_once` —— 一键单发测量+报告（校验 CSP、轮询 RDY）
- `cmw_cqi_set` —— CQI 调度切换+DL 配置（含掉链风险提示）
- `cmw_cqi_stats` —— CQI 统计 + MCS 映射表（只读观察窗口）
- `cmw_init_ebler` —— eBLER 测量启动（CQI 统计数据源）
- 修正：`cmw_select_app`（信令应用不可远程选）、删除坏占位 `cmw_set_meas_port`/`cmw_set_sign_port`

回归测试：`uv run python tests/cmw_handler_smoke_test.py`（用例 1~7 零扰动；
追加 `--live` 跑用例 8 真实单发测量，需 UE 在 CEST）。
SCPI 探针新增批次：`--batch meas` / `--batch cqi`。
