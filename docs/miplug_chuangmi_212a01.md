# 米家智能插座2 (chuangmi.plug.212a01) 局域网控制

2026-08-31 实测验证通过。局域网 miIO 协议 + token 加密直控，**不依赖小米云端**
（本机网络对小米云端的拦截不影响本地控制）。

## 设备档案

| 项 | 值 |
|---|---|
| 名称 | 米家智能插座2 test1（米家 App 里显示） |
| 米家设备 ID | 394133597 |
| MAC | 54:48:E6:D8:57:40 |
| IP | 10.1.200.146（DHCP，变了用 `--ip` 覆盖） |
| TOKEN | 见 `mi_plug_config.json`（gitignored；入库模板 `mi_plug_config.example.json`） |
| MODEL | `chuangmi.plug.212a01`（实测握手返回一致） |
| 固件 | 2.1.8_0028（验证期间设备自动 OTA 升到 2.1.8_0032，全部行为复测无变化） |
| 额定 | 10 A / 2500 W（有过功率保护，阈值实测 2500 W） |
| MIoT spec | <https://home.miot-spec.com/spec?type=urn%3Amiot-spec-v2%3Adevice%3Aoutlet%3A0000A002%3Achuangmi-212a01%3A2> |

token/IP 明文存放在仓库根目录 `mi_plug_config.json`，已被 `.gitignore` 排除
（入库的只有占位模板 `mi_plug_config.example.json`）。TOKEN 长期有效，只有插座
**恢复出厂或换账号重新绑定**才失效；重新提取见下节。

## 获取设备 token（一次性，实操记录）

局域网直控的前提是拿到设备的 32 位 token。token 由小米云端保管，**需登录
米家账号从云端提取**（前提：插座已在米家 App 里绑定到该账号）。

### 方式一（推荐）：MCP 扫码提取 —— 最简单稳定

MCP 服务器内置扫码提取工具（`src/instrument_mcp/mi_cloud.py`），**扫码登录
是最简单稳定的方式**：不需要账号密码、不触发邮箱 2FA，米家 App 扫码确认
即可：

```
miplug_token_qr_start()                                  # 生成登录二维码（返回图片路径）
# 用米家 App 扫码并在 App 内确认登录
miplug_token_qr_finish(login_id="<start 返回的 id>")     # 列出全部设备的 IP/token
# 设备多时用 device_keyword 过滤并自动写入配置：
miplug_token_qr_finish(login_id="...", device_keyword="chuangmi.plug")
```

`save_config=True`（默认）会把匹配设备的 IP/token 自动写入
`mi_plug_config.json`。二维码图片存为 cwd 下 `mi_cloud_login_qr.png`。

### 方式二：外部工具 Xiaomi-cloud-tokens-extractor

[外部工具](https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor)
（PiotrMachowski，入口 `token_extractor.py`，不在本仓库内，需临时 clone）的
实操记录：

```bash
uv add -r requirements.txt            # requests / pycryptodome / colorama 等
uv run token_extractor.py             # 交互式：输账号(邮箱/账号ID)+密码，区域选 cn
uv run token_extractor.py -ni -u <账号> -p <密码> -s cn    # 免交互
uv run token_extractor.py -o output.json                   # 结果另存 JSON
```

输出即账号下设备清单（NAME / ID / MAC / IP / TOKEN / MODEL），把 `ip` 和
`token` 填进 `mi_plug_config.json` 即完成接入。也支持在用户名提示处输 `q`
改用**米家 App 扫码登录**。

**实际踩过的两个坑：**

1. **公司网络拦截云端登录**：登录报 `ConnectionResetError WinError 10054`
   （TLS 握手被重置）。诊断结论：深信服 aTrust 零信任客户端 + 出口防火墙按
   IP 段拦截 `account.xiaomi.com`（DNS 正常、TCP 可通、TLS 被掐断）。
   **解决：切手机热点完成提取**；回公司网后局域网控制不受影响（拦截只作用
   于云端链路）。
2. **邮箱 2FA 报 "Invalid login or password"**：务必用**最新一封**邮件的
   验证码；2FA 每日限额仅 3~5 次，别反复试；或改用上面的扫码登录绕开
   邮箱 2FA。

## 控制原理

- 协议：miIO 局域网协议（UDP），所有报文用 32 位 token 的 AES-CBC 加密，
  `info` 握手能返回设备信息即证明 token 正确。
- 库：python-miio 0.5.12 的 `MiotDevice`，走 MIoT spec 的 `(siid, piid)` 寻址：
  `get_property_by(siid, piid)` / `set_property_by(siid, piid, value)`。
- 该型号在 python-miio 里**没有官方 mapping**，每次运行会打印一条
  `Neither the class nor the parameter defines the mapping`——无害，
  `get/set_property_by` 不依赖 mapping，忽略即可。

## 已验证的操作（全部实测）

| 操作 | 结果 |
|---|---|
| `info` 握手 | 返回 model `chuangmi.plug.212a01`、fw `2.1.8_0028`、MAC 一致 → token 正确 |
| 11 个属性批量读 | 全部成功（见下表实测值） |
| `off`（写 siid=2/piid=1=False） | 回读 False，继电器断开 |
| `on`（写 True） | 回读 True，恢复开启 |
| 循环任务（siid=4） | on=10/off=10 + enable → 按 10s 节拍连续翻转 5 个周期；enable=False 停止并保持当前状态 |
| 循环相位语义 | 从当前状态相位开始：开→on_s 后关；关→off_s 后开（两种起点实测：11.5s/8.6s 首翻，误差 <1.6s） |

## 属性表（MIoT spec v2 + 实测）

读写列：R=已实测可读，W=已实测可写，(r)/(w)=spec 声明但未实测。

| siid | piid | 属性 | 格式 | 读写 | 实测值（2026-08-31） |
|---|---|---|---|---|---|
| 2 | 1 | **开关** | bool | R/W | True（通断验证通过） |
| 2 | 6 | 插座温度 | uint8 °C | R | 41 °C |
| 2 | 7 | 已工作时间 | uint32 min | R | 260 min |
| 3 | 1 | 指示灯开关 | bool | R/(w) | True（写可关夜间指示灯，未实测） |
| 5 | 6 | 电功率 | uint32，**0.01 W** | R | 0 W（空载） |
| 5 | 3 | 电压 | uint16 | R | 220 → 单位就是 V |
| 5 | 2 | 电流 | uint16 | R | 0.01 A/LSB，**底噪 ~5 LSB（0.05 A）已证实**：继电器断开功率已归零后仍读 5；≤0.1 A 负载下不可用（见采样能力一节） |
| 5 | 1 | 累计耗电量 | uint32 | R | raw 100，spec 未标单位（常见 0.001 kWh），待标定 |
| 5 | 7 | 过功率阈值 | uint32 | R | 2500 → 2500 W，即额定值 |
| 7 | 1 | 功率保护开关 | bool | R/(w) | True |
| 4 | 1 | 循环-开启时长 | uint32 s | R/W | 写入生效（实测） |
| 4 | 2 | 循环-关闭时长 | uint32 s | R/W | 写入生效（实测） |
| 4 | 4 | 循环任务使能 | bool | R/W | True=启动循环，False=停止并保持当前状态（实测） |
| 4 | 3 | 按键倒计时 | uint32 s | R/(w) | 20；本地写入只存值，**不触发动作** |
| 4 | 5 | 倒计时已启动 | bool | R | 本地路径下恒 False |
| 4 | 6 | 开关状态自动翻转 | bool | (w) | 写 True 无可观察效果 |
| 1 | 1-4 | 制造商/型号/序列号/固件 | string | (r) | 未实测（`info` 已给固件） |
| 6 | — | 继电器翻转事件 | — | 仅 notify | — |
| 8 | — | 过功率保护推送 | — | 仅 notify | — |

## 用法

### 方式一：MCP 服务器（instrument-mcp）

插座已作为 `instrument_type: mi_plug` 接入 MCP（驱动 `MiPlugInstrument`，
命令定义 `src/instrument_mcp/commands/mi_plug.yaml`）。IP/token 默认读 cwd 下
`mi_plug_config.json`，也可用 `connect` 的 `token` 参数覆盖：

```
connect(address="10.1.200.146", instrument_type="mi_plug", alias="plug")
miplug_status(alias="plug")
miplug_on(alias="plug") / miplug_off(alias="plug") / miplug_toggle(alias="plug")
miplug_loop_start(alias="plug", params_json='{"on_s": 3600, "off_s": 3600}')
miplug_loop_stop(alias="plug") / miplug_loop_info(alias="plug")
miplug_get_property(alias="plug", params_json='{"siid": 2, "piid": 1}')
miplug_set_property(alias="plug", params_json='{"siid": 3, "piid": 1, "value": "false"}')
```

### 方式二：独立 CLI

仓库根目录的 `mi_plug.py`（用 uv 临时注入依赖，不改 `.venv`/`uv.lock`）：

```bash
uv run --with python-miio mi_plug.py info      # 握手 + 设备信息（验证 token）
uv run --with python-miio mi_plug.py status    # 读全部关键属性
uv run --with python-miio mi_plug.py on        # 打开（写后 0.5s 回读校验）
uv run --with python-miio mi_plug.py off       # 关闭
uv run --with python-miio mi_plug.py toggle    # 翻转
uv run --with python-miio mi_plug.py loop 3600 3600  # 设备端循环定时: 开1h/关1h
uv run --with python-miio mi_plug.py loopstop        # 停止循环（保持当前状态）
uv run --with python-miio mi_plug.py loopinfo        # 读定时配置
uv run --with python-miio mi_plug.py get 2 1   # 读任意属性
uv run --with python-miio mi_plug.py set 3 1 false   # 写任意属性（此处=关指示灯）
```

或直接用 Python API（三行核心）：

```python
import json
from miio import MiotDevice
cfg = json.load(open("mi_plug_config.json", encoding="utf-8"))
d = MiotDevice(cfg["ip"], cfg["token"])
d.set_property_by(2, 1, True)      # 开；False=关
print(d.get_property_by(2, 1))     # 读（list[CIResult]，取 [0].value）
```

## 定时能力（2026-08-31 实测）

**可用：设备端循环任务（siid=4）。** 写 on/off 时长 + `enable=True` 后由插座自己
执行，断开电脑/云端照跑；`enable=False` 停止并保持当时状态。实测时间线
（on=8/off=8，从关状态启动）：8.6s→开、17.2s→关、23.7s→开，节拍准确。

相位跟随启动时刻的开关状态，因此一次性定时可用「一长一短」近似拼出来
（时长上限 86500s ≈ 24h）：

| 想要 | 做法 | 副作用 |
|---|---|---|
| N 秒后关闭 | 当前开时 `loop N 86500` | ~24h 后会再翻回开，到时 `loopstop` |
| N 秒后开启 | 当前关时 `loop 86500 N` | ~24h 后会再翻回关，到时 `loopstop` |
| 周期开关 | `loop <on_s> <off_s>` | 无（这正是设计用途） |

**不可用：countdown（4/3）本地写不触发——官方 spec + 社区资料 + 实测三重确认。**

- 官方 spec 对 siid=4 只声明属性、**没有任何 action**，也未描述倒计时启动方式
  （countdown 的 unit 实为 seconds，原始 JSON 可查）；
- openHAB Mi IO 绑定为该设备 imilab-timer 只暴露 on/off-duration、countdown、
  task-switch 四个通道，无隐藏 action；HA 社区证实该倒计时由**双击物理按键**
  启动，4/3 只是其时长配置，4/5 是"是否在跑"的回读（参考：
  <https://v33.openhab.org/addons/bindings/miio/>、
  [HA 社区帖子](https://community.home-assistant.io/t/flashing-and-configuring-mi-smart-power-plug-2-zncz07cm-aka-chuangmi-plug-212a01-to-support-esphome/928310)）；
- 本机实测：只写 4/3、先写 4/6 再写 4/3 两种顺序都试过——值能存入（回读
  一致）但开关不动、4/5 恒 False、写 0 被设备拒收；
- 本机双击物理按键实测（4/3=20s 已配置，0.7s 分辨率全程监听 240s）：**无任何
  反应**——4/5 恒 False、开关无翻转、也无"立即翻+定时翻回"。该功能疑似还需
  在米家 App 侧启用/下发配置，或仅存在于特定固件。

**米家 App 倒计时实测（2026-08-31，决定性一轮）——设备固件完全不参与：**

插座开、App 设 1min 倒计时，2s 分辨率全程监听 7 个量：

- 设置时刻与倒计时期间：`4/3`（仍为旧值 20，**未被写成 60**）、`4/5`、`4/4`
  零变化——设备侧定时属性全程无感；
- 到期：开关 True→False、功率归零，与设置时刻的间隔和 1 分钟倒计时吻合
  （注意：米家定时仅精确到分钟、到期对齐分钟边界，见下方官方 FAQ）——动作
  与一条普通开关写命令无异；
- 结论：**App 倒计时 = 外部定时器（云端/手机）到期下发普通开关命令**。
  本地等价替代就是 `loop`（一长一短近似一次性）或宿主 `sleep`+`on/off`，
  且不依赖云与手机在线。

**官方文档佐证**（[小米 IoT 平台《定时和倒计时》](https://iot.mi.com/v2/new/doc/plugin/other/timer)，
2026-03 更新）——与上述实测完全一致：

- 原文："**服务端生成定时场景存储到云端，在定时时间到来时，向设备发送 RPC
  指令控制设备**"；且官方倒计时示例的执行动作就是
  `set_properties [{did, siid:2, piid:1, value}]`——**与我们 LAN 直控写的是
  同一个属性、同一条命令**；
- 原文："定时分为**云端定时和本地定时**，本地定时需要由开发者自行进行开发"
  ——siid=4 循环任务就是 chuangmi 自研的本地定时实现。这解释了全部现象：
  loop 任务能在设备端脱离云执行，而 spec 里的倒计时属性在 LAN 侧没有触发
  入口（4/3 是"按键倒计时"功能的配置，厂商未给云外触发路径）；
- FAQ："**目前米家的定时仅精确到分钟**"（如 15:25:46 设"一分钟后"倒计时，
  15:26:00 即结束）——云端定时到期时刻对齐分钟边界；
- 云端定时只支持 Wi-Fi / BLE Mesh 设备（BLE 设备收不到云端下行指令）。

绝对时刻定时（"每天 8:00 开"）属于米家云端/APP 功能，不在设备 spec 里。
宿主机 `sleep` + `on/off` 是最干净的本地一次性定时兜底。

## 电流/功率采样能力（2026-08-31 实测）

测试脚本 `tests/mi_plug_sampling_test.py`（`uv run --with python-miio`，子命令
`rtt` / `refresh` / `step`），带 ~10 W 负载实测：

**1. LAN 轮询上限 ≈ 10 Hz（瓶颈是网络往返，不是属性数量）**

| 项目 | 实测 |
|---|---|
| 单属性读 RTT（get_property_by x60） | min 73 / median 102 / p95 116 ms |
| 批量读 RTT（get_properties 一次读 P+I+U x60） | min 80 / median 102 / p95 119 ms |
| 连发压力（100 次批量读） | 9.6 Hz 吞吐，**0 失败** |
| 120s 持续轮询 | 8.4 Hz 稳定，1013 样本 0 失败 |

协议原生 `get_properties` 支持一次 UDP 往返读多个属性（RTT 与单读相同），
python-miio 0.5.12 没封装，需 `d.send("get_properties", [{"did": "5-6", "siid": 5,
"piid": 6}, ...])`——**did 是客户端自选关联 ID，必须字符串**（数字 did 报
`need did`）。RTT 下限即本机↔插座 Wi-Fi 往返（ping 66~167 ms）。

**2. 设备端电表刷新 ≈ 每 5 s 一个新值 → 有效采样率只有 ~0.2 Hz**

- 120s 被动监听：功率值相邻变化间隔 median **5.02 s**（min 2.8 / p90 7.5 s），
  电流、电压 120s 内零变化；
- 阶跃测试（继电器 off/on x2）：功率变化时刻全部对齐 **~5 s 节拍**
  （mod 5 ≈ 4.3±0.3 s），证实固件约每 5 s 发布一次新值；
- 所以 >0.2 Hz 的轮询只是在复读同一个缓存值；并发多 socket 也没有意义。

**3. 功率还是多秒滑动平均，不是瞬时值（阶跃响应 x2 取最差值）**

| 阶跃 | 响应 |
|---|---|
| OFF 后功率 ≤70% 基线 | +1.7~2.2 s |
| OFF 后功率归零 | +6.6~6.8 s |
| ON 后功率到 ~50% | +3.8~4.5 s |
| ON 后功率到满值 | +6.9~9.5 s |

即报告值相当于对真实功率做了 ~5-10 s 窗口的平均。想做"平均功率/占空比
监测"没问题（窗口取 ≥30 s）；**想做快沿、瞬态、波形类采样不行**。

**4. 各量的分辨率与底噪**

| 量 | 单位 | 实测行为 |
|---|---|---|
| 功率 (5,6) | 0.01 W | 名义分辨率高，但被 5 s 平滑拖累；满量程 2500 W |
| 电流 (5,2) | 0.01 A | **底噪 ~5 LSB（0.05 A）**：P=0 时仍读 5；10 W 负载全程恒 5，≤0.1 A 负载下基本不可用 |
| 电压 (5,3) | 1 V | 120s 恒 220，分辨率 1 V，只能看市电粗略值 |
| 累计电量 (5,1) | 未标定 | 疑似 0.001 kWh，10 W 负载 120s 变化低于 1 LSB，本测无法分辨 |

**结论**：这颗插座当"慢速功率趋势表"用——LAN 想读多快都行（≤10 Hz），
但**新数据 ~5 s 一格、且是 ~5-10 s 平均值**；电流通道底噪 0.05 A 只适合
≥0.1 A 量级负载。仪器级电流采样请走台架（电流钳/源表），插座只做功耗
粗测与远程断电。

## 坑与排障

- **`miiocli` 在 Python 3.13 上崩**：`TypeError: argument of type 'bool' is not
  iterable`（0.5.12 CLI 兼容 bug，与设备无关）→ 一律用 Python API 或本脚本。
- **0.5.12 的 `DeviceInfo` 没有 `.mac` 属性** → 从 `info.data["mac"]` 取
  （脚本已处理）。导入时还有一条 `partial` 的 FutureWarning，同样无害。
- **IP 漂移**：DHCP 重新分配后命令超时 → 米家 App 查新 IP，`--ip` 覆盖。
- **token 失效**（恢复出厂/换绑）：按「获取设备 token」一节重跑提取流程。
- 命令整体超时/无响应：先 `ping 10.1.200.146` 确认与插座同网段（本机 Wi-Fi）。

## 待办

- [x] 电流单位初步标定：2026-08-31 带载 11.85 W/220 V ≈ 0.054 A ↔ raw 5 → **0.01 A**（空载时同样读 5，疑似 0.05 A 底噪；累计耗电量单位仍未定，疑似 0.001 kWh）。
- [x] 双击物理按键已实测（2026-08-31，0.7s 分辨率监听 240s）：无任何反应。
  若仍想启用该功能，下一步是在米家 App 的设备设置里找「按键/倒计时」开关后
  重测；不影响已验证的 loop 定时方案。
- [x] 采样能力实测（2026-08-31，`tests/mi_plug_sampling_test.py`）：LAN 轮询上限
  ~10 Hz；设备端 ~5 s 发布一个新值（有效 ~0.2 Hz）且为多秒滑动平均（阶跃归零
  ~7 s）；电流底噪 0.05 A。详见「电流/功率采样能力」一节。
- [ ] 若要纳入 instrument-mcp：仿照 tinysa 模式加 `MiotPlugInstrument` +
  yaml 命令（on/off/status/功率读取/循环定时）。
