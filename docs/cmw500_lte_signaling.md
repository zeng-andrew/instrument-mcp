# CMW500 LTE Signaling 接入手册与参数关联

> 2026-09-17 实测沉淀。整机固件 3.7.110，LTE Signaling 应用软件版本 3.7.30.17。
> 指令集权威参考：R&S 官方 Python 驱动 `RsCmwLteSig`（PyPI，指令树与固件一致），
> 本文档所有 SCPI 均在真机逐条验证过（含读改写回）。

## 连接

| 方式 | 地址 |
|---|---|
| 裸 socket | `172.22.1.3:5025`（每条命令 `\n` 结尾，查询应答以 `\n` 结尾） |
| VISA（MCP server 用） | `TCPIP0::172.22.1.3::5025::SOCKET` |

无效指令头的**查询**不会返回任何应答行（只能靠超时判定），错误进 `SYST:ERR?` 队列；
调试时建议每条写命令后跟 `SYST:ERR?`。

## 面板项 → SCPI 速查（均实测）

### 小区与射频（Cell / RF Settings）

| 面板项 | SCPI | 备注 |
|---|---|---|
| 小区开关 | `SOUR:LTE:SIGN:CELL:STATe ON\|OFF`（查询 `:STATe:ALL?` 返回 `OFF,ADJ` 等） | |
| UE 连接/断开 | `CALL:LTE:SIGN:PSWitched:ACTion CONNect\|DISConnect` | 状态查询用 `FETC:...:STATe?`（`CALL:...STATe?` 不存在） |
| 双工模式 | `CONF:LTE:SIGN:DMODe FDD\|TDD` | 切 TDD 频段前必须先设 TDD |
| 频段 | `CONF:LTE:SIGN:PCC:BAND OB1\|OB3\|...` | |
| DL/UL 信道 | `CONF:LTE:SIGN:RFSettings:PCC:CHANnel:DL/UL <EARFCN>` | 连接中改信道会触发 Redirection |
| DL/UL 带宽 | `CONF:LTE:SIGN:CELL:BANDwidth:PCC:DL/UL B100\|B050\|B025\|B015\|B006` | 仪器返回零填充格式（B050）；查询 `...?` |
| 物理小区号 | `CONF:LTE:SIGN:CELL:PCID <0..503>` | `[:PCC]` 可省略 |
| 下行小区功率 | `CONF:LTE:SIGN:DL:PCC:RSEPre:LEVel <dBm>` | **没有 CELL:POWer**，下行功率就是 RS EPRE |
| UE 期望功率 | `CONF:LTE:SIGN:RFSettings:PCC:ENPower <dBm>` | 上行 |
| 用户裕量 | `CONF:LTE:SIGN:RFSettings:PCC:UMARgin <dB>` | 是 UMARgin，**不是 UMARker** |
| 输入/输出衰减 | `CONF:LTE:SIGN:RFSettings:PCC:EATTenuation:INPut / :OUTPut1 <dB>` | OUTPut 带序号后缀 |

### 调度与 RB（Connection 页，RB Downlink / Uplink 所在）

调度类型：`CONF:LTE:SIGN:CONNection:PCC:STYPe RMC|UDCHannels|UDTTibased|CQI|SPS`
（查询返回缩写 RMC / UDCH…）。**当前调度类型决定哪组参数生效**：
RMC → `RMC:UL/DL`；UDCHannels → `UDCHannels:UL/DL`；UDTTibased → 逐子帧表（需 KS510）。

| 面板项 | SCPI | 参数格式 |
|---|---|---|
| RB Uplink (RMC) | `CONF:LTE:SIGN:CONNection:PCC:RMC:UL <RB>,<MOD>,<TBS>` | `N50,QPSK,T6`；TBS 可 `KEEP` |
| RB Downlink (RMC) | `CONF:LTE:SIGN:CONNection:PCC:RMC:DL <RB>,<MOD>,<TBS>` | 同上（DL 支持到 Q256，且带流后缀 `DL1/DL2`） |
| DL RB 位置 | `CONF:LTE:SIGN:CONNection:PCC:RMC:RBPosition:DL LOW\|HIGH\|P5\|P10\|P23\|P35\|P48` | |
| 用户自定义 UL/DL | `CONF:LTE:SIGN:CONNection:PCC:UDCHannels:UL/DL <RB数>,<起始RB>,<MOD>,<TBS>` | 数值型 `25,0,QPSK,5`，任意 RB 数 |
| UL 高阶调制开关 | `CONF:LTE:SIGN:CELL:PCC:ULSupport:QAM64\|QAM256:ENABle ON\|OFF` | 默认 OFF |
| 上行 TPC | `CONF:LTE:SIGN:UL:PCC:PUSCh:TPC:SET CLOop\|OPEn\|MAXP...` | |

### 信号路径（RF COM 选择）

场景 `SCEL`（='1CC - 1x1'）下，收发实际走哪个 RF COM 由 flexible 场景参数决定：

```
ROUTe:LTE:SIGN:SCENario:SCELl:FLEXible <单元>,<RX连接器>,<RX模块>,<TX连接器>,<TX模块>
例: ROUT:LTE:SIGN:SCENario:SCELl:FLEXible SUW1,RF1C,RX1,RF1C,TX1
查询: ROUT:LTE:SIGN:SCENario:SCELl:FLEXible?   ->  SUW1,RF1C,RX1,RF1C,TX1
```

- 切换要求小区 OFF，切完重开小区；应用配置（频段/带宽/RMC）不丢
- 实测：RF2C → RF1C 一次切换成功（2026-09-18）
- **排障口诀**：模组插在 RF1 COM、盒子却发在 RF2 COM 时，现象是小区正常广播但
  Event Log 里零接入（连 RACH 都没有）。先查 `cmw_query_signal_path` 再查模组。

### Event Log

| 操作 | SCPI |
|---|---|
| 读最新一条 | `SENS:LTE:SIGN:ELOG:LAST? [HRES]` |
| 读全部（旧→新） | `SENS:LTE:SIGN:ELOG:ALL? [HRES]` |
| 清空 | `CLEan:LTE:SIGN:ELOG` |

每条 `{时间戳, 类别(INFO/WARN/ERR...), 事件文本}`；`ELOG:ALL` 响应开头多一个
`",CONT,"` 前导标记（continuation），解析时跳过；`HRES` = 毫秒级时间戳。
另有一组 `SENS:LTE:SIGN:EELOG:*` 是独立通道（本机为空，勿混淆）。

一次真实注册的完整事件序列（2026-09-18 实测，Cat.1 模组 + 物联网卡）：

```
"11:13:05.781",INFO,"State 'Cell On', 1CC 1x1"
"11:18:55.921",INFO,"State 'Cell Off'"        <- GUI 手动重启小区
"11:18:57.828",INFO,"State 'Cell On', 1CC 1x1"
"11:21:02.359",INFO,"RRC Connection Established"
"11:21:03.140",INFO,"EPS Default Bearer Established, Id 5"
"11:21:03.578",INFO,"EPS Default Bearer Established, Id 6"
"11:21:03.578",INFO,"State 'Attached'"
```

配套状态查询 `FETC:LTE:SIGN:PSWitched:STATe?` 取值（2026-09-18 全部实测）：
稳态 `OFF`（小区未开）/ `ON`（小区已开无 UE 或已退网）/ `ATT`（已附着，ECM-Idle）/
`CEST`（连接已建立，RRC Connected）；状态跳变瞬间短暂出现 `CONN`（建立中）/
`DISC`（断开中）。注意：模组进入休眠（IDLE/PSM）不会使该值退出 ATT；模组休眠
会导致搜网极慢（本次从开小区到附着约 8 分钟，重启小区后 2 分钟附着），排障时
先排除休眠。

### 连接生命周期动作 → Event Log 映射（实测）

| 动作（CALL:LTE:SIGN:PSWitched:ACTion） | 前提 | Event Log 新增 | 状态变化 |
|---|---|---|---|
| UE 自主注册 | 模组醒来搜网 | RRC Connection Established → EPS Default Bearer Established, Id 5/6 → State 'Attached' | ON → ATT |
| `CONNect` | ATT | EPS Dedicated Bearer Established, Id 7 → State 'Connection Established' | ATT → CONN → CEST |
| `DISConnect` | CEST | EPS Dedicated Bearer Released, Id 7 → State 'Attached'（**无** RRC Connection Released 条目） | CEST → DISC → ATT |
| `DETach` | ATT/CEST | Network Originated Detach → RRC Connection Released → State 'Cell On', 1CC 1x1（状态归位标记） | → ON |

要点：
- ATT 与 CEST 是两个状态：模组附着后通常会自己进 ECM-Idle（ATT）；
  `DISConnect` 在 ATT 下是空操作，这是"发了没反应"的常见原因
- `CONNect` 总会建立一条专用承载（不经 PREPare 也可，用默认/上次配置；
  本次 Id 7，目录显示格式 `"7 (->5, Default)"`）；随 DISConnect 自动释放，
  释放后 `CATalog:LTE:SIGN:CONNection:DEDBearer?` 回到 `NAV`
- DISConnect 的日志是"专载释放 + State 'Attached'"；`RRC Connection Released`
  条目只在 DETach 流程里出现
- PREPare:LTE:SIGN:CONNection:DEDBearer 的 bearer_id 参数若把查询返回的引号
  一起带上会报 -103 Invalid separator（引号只能有一层）
- `DETach` 后 UE 完全退网，重附着必须 UE 侧发起。休眠类模组会自己周期性搜网回来：
  本次实测 Detach 后 **4 分 36 秒**自动重新附着（盯 4 分钟就放弃会误判为"不回来"）
- 默认承载查询：`CATalog:LTE:SIGN:CONNection:DEFBearer?`（含 APN，如
  `"5 (cmw500.rohde-schwarz.com)"`）

UE 注册后 `SENS:LTE:SIGN:UECapability:RF:SUPPorted?` 返回真数据（未注册时全 OFF）：
逐项对应 E-UTRA 频段，第 i 项 = Band i（首项为占位）。本次模组实测支持
B1/B2/B3/B4/B5/B7/B8/B28/B66。

### 频段/信道切换：Blind Handover 与 Redirection（连接中实测）

连接保持（CEST）下改频段/信道，**不断网**，两种机制按场景自动选择：

| 操作 | 机制 | Event Log | 耗时 |
|---|---|---|---|
| 同频段改 DL 信道（550→300） | **Blind Handover** | `Blind Handover` → `Blind Handover Successful` | ~0.5 s |
| 跨频段（OB1→OB8，改 DL 信道） | **Redirection** + 补一次盲切 | `Redirection Start` → `RRC Connection Released` → `RRC Connection Established` → `Tracking Area Update Received` → `Redirection Successful` → `Blind Handover` → `Blind Handover Successful` | ~1.6 s (+0.4 s) |

- UL 信道自动跟随：DL 550→300 时 UL 18550→18300；OB8 DL 3500 时 UL 21500（实测）
- 跨频段先 `PCC:BAND` 再 `RFSettings:PCC:CHANnel:DL` 会产生两组条目：Band 写入即触发
  Redirection（先落到该频段默认信道），紧随的信道写入再 Blind Handover 到精确频点
- 前提：UE 支持目标频段（`SENSe:LTE:SIGN:UECapability:RF:SUPPorted?` 查）
- UE 的 IP：`SENSe:LTE:SIGN:UESinfo:UEADdress:IPV4?`（每条默认承载一个 IP，
  实测两条承载分到 192.168.48.129/130）

### 吞吐测量（信令应用内置）

```
CONF:LTE:SIGN:THRoughput:UPDate 200;WINDow 2000;REPetition SINGleshot;TOUT 10
    UPDate/WINDow 单位=子帧（200=200ms）。默认 UPDate 可能高达 10000（10s/样点），
    会把 FETC 查询阻塞 10s+，先调小再 INIT
INIT:LTE:SIGN:THRoughput      启动（FETC:...:THRoughput:STATe? → RUN）
FETCh:LTE:SIGN:THRoughput?    读各载波/流吞吐（逗号分隔多字段）
ABORt:LTE:SIGN:THRoughput     中止
```

**读数全 0 是正常现象**：该测量按 PDU 计数——模组不上传数据、下行 RMC 用 MAC
padding 填充时就是 0，不代表测量故障。要非零读数需要 UE 侧真实收发数据。

### TPC 上行功率控制（实测）

| TPC:SET 模式 | 行为 |
|---|---|
| `MAXP` | TPC 命令 UE 到最大功率（本机 ENPower 跟随值 ≈ +29 dBm） |
| `CLOop` | 闭环：UE 功率被拉向 `TPC:CLTPower` 目标（-50~33 dBm） |
| `SINGle` | 手动 ±1 dB 步进图案（`TPC:SINGle <1..35>,<UP|DOWN>` 后 `TPC:PEXecute` 执行） |
| `RPControl` | 3GPP 相对功率控制测试图案（RUA/RDA/RUB…） |

闭环实测（Cat.1 模组）：目标 -20 dBm → 15 s 后 ENPower 从 +29 降到 **-12.9**；
目标改 -10 → 升到 **-5.0**；恢复 MAXP → 回 +29。ENPower 在 ENPMode=ULPC 下
跟随 UE 实际功率，可当"UE 功率表"用。

### 远程应用切换与调试辅助

- 应用实例名：`INSTrument:SELect?`（本机 LTE Signaling 实例名为 **SIG**）；
  `INSTrument:SELect <名字>` 切换，切换后全部指令作用于新应用
- `INSTrument:CATalog?` 本机返回空串，枚举不可用（按名字切换不受影响）
- `SYSTem:DISPlay:UPDate ON` 让 GUI 实时跟随远程修改，人机协同调试时打开
- 模组休眠搜网可达数分钟（Detach 后 4.6 min 自动重附），等待注册用
  `cmw_wait_ue_state(target=ATT, timeout_s=300)` 轮询工具，不要反复人工查

## 参数关联规则（核心沉淀，handler 已自动化）

### 1. 带宽 → DL RMC 可写值（先改带宽，再改 DL RB）

DL RMC 的 RB 数受当前 Cell Bandwidth 限制，越界写入报
**`-203 "Command protected; option missing"`——文案有误导性，实为带宽约束，并非缺选件**。

| 带宽 | 实测可写的 DL RB |
|---|---|
| B100（20MHz） | 仅 N50（N25/N100/N6 均实测 -203） |
| B050（10MHz） | N25 实测可写 |
| B025/B015/B006 | N25/N15/N6（按满带宽 RMC 推断，未实测，遇 -203 请回填本表） |

规则：先 `CELL:BANDwidth:PCC:DL`（建议 UL 同步改）→ 再 `RMC:DL`。
→ MCP 工具 `cmw_set_dl_rb` 已封装此关联（`auto_bandwidth=true` 自动切带宽）。

### 2. ULSupport 开关 → UL 高阶调制

`RMC:UL` 写 Q64/Q256 前必须 `CELL:PCC:ULSupport:QAM64|QAM256:ENABle ON`，
否则报 `-221 Settings conflict`（本机默认 OFF）。
→ MCP 工具 `cmw_set_ul_rb` 已封装此关联（`auto_qam=true` 自动开启）。

### 3. 改带宽会自动钳位其他参数

改带宽后，`RMC:UL` 与 `UDCHannels:*` 的值被仪器**自动钳位**到新带宽下的合法值
（实测 B100→B050 时 `50,0,QPSK,5/6` 双双变为 `25,0,QPSK,5`）。属正常行为，
改完带宽后如需保留原 UL 配置要重新下发。

### 4. 其他注意事项

- 修改调度/RB/带宽配置建议在**小区 OFF** 时进行（连接中改会触发重配置或被拒）。
- UL RMC 的 RB 数**不受**带宽限制（B100 下 N1/N25/N50 均实测可写）。
- `KEEP` 作 TBS 参数时仪器自动选兼容值，**写后务必读回确认**实际生效值。
- `CONNection` 组命令头里的 `[:PCC]` 段可省略（`CONF:LTE:SIGN:CONNection:RMC:UL?` 等价），
  但 `CELL:BAND` 这类**没有** PCC 变体的不能加。
- 别发 `*RST`（会重置整个多应用配置）；复位 LTE Signaling 用前面板或重调参数。

## MCP 工具

`src/instrument_mcp/commands/cmw.yaml`（66 条）+ `cmw_handler.py`（参数关联处理器）。
连通后可直接调用；带关联校验的三个工具：

- `cmw_lte_snapshot` —— 一键面板快照（只读）
- `cmw_set_dl_rb` —— 带宽优先联动版 DL RB 设置
- `cmw_set_ul_rb` —— QAM 开关联动版 UL RB 设置

回归测试：`uv run python tests/cmw_handler_smoke_test.py`
（设计为零扰动：只做同值写入与拒绝分支验证）。
