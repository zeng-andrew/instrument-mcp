# Agilent 66319D 关闭耦合模式完整流程

> 依据: `Ag_66319B_UserGuide_en.pdf`（第 8 章语言字典 INST:COUP:OUTP:STAT / OUTP:PON:STATe，
> 第 2 章 Save & Recall 面板说明）。
> 本机实测: 2026-09-16, GPIB2::5::INSTR, 66319D MY43000582 固件 A.03.01，
> 实验脚本 `tests/ps_66319d_dual_channel_test.py`（8/8 通过）。

## 一、耦合模式是什么

耦合（Coupling）= 输出 1 和输出 2 联动：
- **ALL**（出厂默认）：两路输出绑在一起，任意一路的开关/保护动作同时作用于另一路
- **NONE**：两路输出完全独立，可分别控制

本机出厂/历史配置即为 `ALL`——不解除耦合时"关 CH1 会连累 CH2"，实测确认。

---

## 二、前面板操作（临时关闭）

前提：仪器处于 **Meter 模式**（按 Meter 键确认）。

1. 按 **Output** 键，进入输出参数列表
2. 按 **▼/▲** 滚动，直到屏幕显示 `COUPLING`
3. 用数字键输入 `1`（或旋钮）选择 **NONE**
4. 按 **Enter** 确认，屏幕显示 `COUPLING NONE` 即生效

**关键限制**：仅作用于本次开机，**断电重启后失效**。

---

## 三、命令操作（临时关闭）

```
INST:COUP:OUTP:STAT NONE     @ 关闭耦合
INST:COUP:OUTP:STAT?         @ 查询当前耦合状态（返回 NONE / ALL）
```

同样**易失**，断电不保留。

注意：66319D **不支持 `INST:NSEL` 通道选择**（发下去报 `-113 Undefined header`），
两路输出用后缀区分：`OUTP1/OUTP2`、`VOLT/VOLT2`、`CURR/CURR2`、
`MEAS:VOLT?/MEAS:VOLT2?`。未耦合时裸 `OUTP` 命令只作用于主输出（output 1）。

---

## 四、永久生效流程（面板 / 命令二选一）

### 方式 A：前面板

1. 按第二步操作把 COUPLING 设为 NONE
2. 进入 Save/Recall 菜单，把当前状态保存到寄存器（如寄存器 0）：
   Save → Enter Number → 0 → Enter（即 `*SAV 0`）
3. 在系统菜单中把 `PON:STATE` 设为 `RCL0`（面板菜单项名）

### 方式 B：SCPI 命令

```
INST:COUP:OUTP:STAT NONE     @ ① 设为 NONE
*SAV 0                       @ ② 存入非易失寄存器 0（共支持 0~3）
OUTP:PON:STAT RCL0           @ ③ 设定开机自动恢复寄存器 0（存 NVRAM）
```

**三步缺一不可**：① 改值、② 存档、③ 指定开机加载源。

> ⚠️ 命令名注意：手册第 8 章的 SCPI 命令是 `OUTPut:PON:STATe`（例 `OUTP:PON:STAT RCL0`，
> 查询 `OUTP:PON:STAT?`）。`PON:STATE` 只是前面板 System 菜单里的菜单项名，
> 当 SCPI 发 `PON:STATE RCL0` 会报 `-113 Undefined header`。

**两个副作用（执行 ②③ 前需确认）：**
- `*SAV 0` 覆盖寄存器 0 原有保存状态（读不回原内容）
- `RCL0` 生效后上电不再走安全的 `*RST` 默认态（0V、输出关），而是**召回保存时刻的
  完整状态**——若在两路带电 3.8V 时保存，以后每次上电两路直接带电输出。
  建议先把输出设为目标初始态（如 3.8V 但输出 OFF）再保存。

---

## 五、相关命令速查

| 命令 | 作用 | 是否非易失 |
|---|---|---|
| `INST:COUP:OUTP:STAT NONE/ALL` | 设置耦合状态 | 易失 |
| `*SAV 0~3` | 当前状态存入寄存器 | 写入 EEPROM |
| `*RCL 0~3` | 从寄存器恢复状态 | — |
| `OUTP:PON:STAT RCL0` | 开机自动恢复指定寄存器 | 非易失 |
| `*RST` | 恢复出厂默认（耦合回到 ALL） | — |

---

## 六、验证是否成功

- 面板：屏幕显示 `COUPLING NONE`
- 命令：`INST:COUP:OUTP:STAT?` 返回 `NONE`
- 行为验证：分别发 `OUTP1 ON` / `OUTP2 ON`，两路输出互不影响即解耦成功

---

## 附：本机实测记录（2026-09-16）

- 原始耦合状态 `ALL`；实测 `OUTP1 OFF` 时两路同关（联动实锤）。
- `INST:COUP:OUTP:STAT NONE` 后实测 `OUTP1 OFF`：CH1 OUTP=0（实测 3.801V→0.121V，
  ~0.12V 为关断放电残余，判关断以 `OUTP?` 状态位为准，勿用 <0.05V 电压阈值）；
  CH2 OUTP2=1、3.800V 零漂移——独立供电成立。
- 未耦合时裸 `OUTP OFF` 只关主输出，不会一关全关。
- 耦合切回 `ALL` 复测联动行为一致；实验后已恢复原始状态。
- 完整实验脚本: `tests/ps_66319d_dual_channel_test.py`（场景 A/B/C 全覆盖，8/8 通过）。
