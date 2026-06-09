# 待测试命令清单

## CMW500（已测试部分命令）

### ✅ 已验证命令
| 命令 | 状态 | 备注 |
|------|------|------|
| `*IDN?` | ✅ | 正常 |
| `*RST` | ✅ | 正常 |
| `*OPC?` | ✅ | 正常 |
| `*CLS` | ✅ | 正常 |
| `*TST?` | ✅ | 正常 |
| `*OPT?` | ✅ | 正常 |
| `SYST:ERR?` | ✅ | 正常 |
| `SOURce:LTE:SIGN:CELL:STATe ON/OFF` | ✅ | 正常 |
| `SOURce:LTE:SIGN:CELL:STATe:ALL?` | ✅ | 返回 `ON,PEND` 或 `OFF,ADJ` |
| `CONFigure:LTE:SIGN:PCC:BAND OB1` | ✅ | 正常 |
| `CONFigure:LTE:SIGN:RFSettings:PCC:CHANnel:DL/UL` | ✅ | 正常 |
| `CONFigure:LTE:SIGN:RFSettings:PCC:FREQuency:DL/UL?` | ✅ | 返回频率值 |
| `CONFigure:LTE:SIGN:RFSettings:EATTenuation:INPut` | ✅ | 正常 |
| `CONFigure:LTE:SIGNaling:UL:PCC:PUSCh:TPC:SET` | ✅ | 正常 |
| `FETCh:LTE:SIGN:PSWitched:STATe?` | ✅ | 返回 `OFF`（无 UE） |
| `CONFigure:LTE:SIGN:CELL:BANDwidth:PCC:DL B100` | ✅ | B100=20MHz |

### ⏳ 待测试命令（需要 UE 连接或特定场景）
| 命令 | 场景要求 |
|------|----------|
| `CALL:LTE:SIGN:PSWitched:ACTion CONNect` | 需要 UE 连接 |
| `CALL:LTE:SIGN:PSWitched:ACTion DISConnect` | 需要 UE 已连接 |
| `FETCh:LTE:SIGN:PSWitched:STATe?` | 有 UE 时返回 CONNECTED |
| `ABORT:LTE:MEASurement:MEValuation` | 需要测量进行中 |
| `INITiate:IMMediate` | 需要测量配置 |
| `CONFigure:LTE:MEASurement:MEValuation:RBALlocation:NRB` | 测量模式 |
| `ROUTe:LTE:MEAS:SCENario` | 需要多端口配置 |
| `ROUTe:LTE:SIGN:SCENario` | 需要多端口配置 |
| `MMEMory:CATalog?` | 文件系统操作 |
| `MMEMory:RCL` | 需要预存状态文件 |

### ❓ 需要确认的命令
- WCDMA 应用命令（`WCDMa` 子系统）
- GSM 应用命令
- 5G NR 应用命令（如果固件支持）
- 功率读取命令（`READ:LTE:MEAS:*`）在 UE 连接后的返回值

---

## MXA（全部待测试）

### ⏳ 待验证命令
| 命令 | 用途 | 预期行为 |
|------|------|----------|
| `*IDN?` | 读取标识 | 返回 Keysight 型号信息 |
| `*RST` | 预设 | 恢复默认状态 |
| `*CLS` | 清除状态 | 清除错误队列和状态寄存器 |
| `*OPC?` | 操作完成 | 返回 1 |
| `FREQ:CENT {hz}` | 设置中心频率 | 频率改变 |
| `FREQ:SPAN {hz}` | 设置扫宽 | 扫宽改变 |
| `BAND:RES {hz}` | 设置 RBW | RBW 改变 |
| `BAND:VID {hz}` | 设置 VBW | VBW 改变 |
| `DISP:WIND:TRAC:Y:RLEV {dbm}` | 设置参考电平 | 参考电平改变 |
| `POW:ATT {db}` | 设置衰减 | 衰减改变 |
| `CALC:MARK:MAX` | 峰值搜索 | 标记移动到峰值 |
| `CALC:MARK:Y?` | 读取标记幅度 | 返回 dBm 值 |
| `CALC:MARK:X?` | 读取标记频率 | 返回 Hz 值 |
| `TRAC:DATA? TRACE1` | 读取迹线 | 返回数据点数组 |
| `INIT:CONT OFF` | 单次扫描 | 停止连续扫描 |
| `INIT:CONT ON` | 连续扫描 | 开始连续扫描 |
| `INIT:IMM` | 立即扫描 | 触发一次扫描 |
| `DET:TRAC1 {type}` | 设置检波器 | 检波器改变 |
| `AVER:COUN {n}` | 设置平均次数 | 平均次数改变 |
| `TRAC1:MODE {mode}` | 设置迹线模式 | 迹线模式改变 |
| `SYST:ERR?` | 读取错误 | 返回错误信息 |

### ❓ 需要确认的命令
- `CALC:MARK:MAX` 后是否需要 `*WAI` 或 `*OPC?`
- `TRAC:DATA? TRACE1` 返回的数据格式（ASCII/二进制）
- `INIT:IMM` + `*WAI` 的组合使用
- 多标记操作（`CALC:MARK1`, `CALC:MARK2` 等）
- 频谱分析模式 vs 信号分析模式命令差异

---

## 测试计划

### 第一阶段：MXA 基础命令验证
1. 连接 MXA，读取 IDN
2. 设置频率/扫宽/RBW/VBW
3. 执行峰值搜索，读取标记
4. 切换单次/连续扫描
5. 读取迹线数据
6. 检查错误处理

### 第二阶段：CMW500 完整场景验证
1. 连接 CMW500，激活 LTE 应用
2. 配置小区参数（频段、信道、带宽）
3. 打开小区，等待就绪
4. 连接 UE（如有）
5. 执行测量，读取功率/EVM/ACLR
6. 切换 WCDMA/GSM 应用测试

### 第三阶段：混合场景
1. MXA + CMW500 同时连接
2. CMW500 发射信号，MXA 测量频谱
3. 自动化测试流程验证
