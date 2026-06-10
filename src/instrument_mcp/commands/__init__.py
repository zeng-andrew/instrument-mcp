"""命令配置加载器。

从 YAML 动态生成 MCP Tools，支持按仪器型号自适应。
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

from pydantic import BaseModel, Field, create_model

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# 已注册的 tools 缓存（用于 list / invoke）
_TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {}

# 型号 -> 配置 映射（用于自适应）
_MODEL_CONFIG_MAP: Dict[str, Dict[str, Any]] = {}


def _load_yaml(path: Path) -> dict:
    if yaml is None:
        raise RuntimeError("pyyaml not installed")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _build_pydantic_model(cmd_name: str, params: list) -> type:
    """根据 YAML 参数定义动态生成 Pydantic 模型。"""
    fields: Dict[str, Any] = {}
    type_map = {"string": str, "number": float, "integer": int, "boolean": bool}

    for p in params:
        pytype = type_map.get(p["type"], str)
        default = p.get("default", ...)
        if default is not ...:
            fields[p["name"]] = (
                Optional[pytype],
                Field(default=default, description=p.get("description", "")),
            )
        else:
            fields[p["name"]] = (pytype, Field(description=p.get("description", "")))

    return create_model(f"{cmd_name}_params", **fields, __base__=BaseModel)


def _execute_scpi(inst, scpi: Any, kwargs: dict, entry: dict) -> str:
    """执行 SCPI 模板，返回结果字符串。"""
    if isinstance(scpi, str):
        formatted = scpi.format(**kwargs) if kwargs else scpi
        inst.write(formatted)
        entry["status"] = "ok"
        return f"[PASS] {formatted}"

    elif isinstance(scpi, dict):
        if "write" in scpi:
            w_cmd = scpi["write"].format(**kwargs) if kwargs else scpi["write"]
            inst.write(w_cmd)
        if "query" in scpi:
            q_cmd = scpi["query"].format(**kwargs) if kwargs else scpi["query"]
            resp = inst.query(q_cmd)
            entry["status"] = "ok"
            return f"[PASS] {resp}"
        entry["status"] = "ok"
        return "[PASS] OK"

    elif isinstance(scpi, list):
        for template in scpi:
            cmd = template.format(**kwargs) if kwargs else template
            inst.write(cmd)
        entry["status"] = "ok"
        return "[PASS] OK"

    raise ValueError(f"Unsupported scpi_template type: {type(scpi)}")


def _make_tool_handler(
    cmd_name: str,
    cmd_def: dict,
    inst_type: str,
    sessions: Dict[str, Any],
    command_history: list,
):
    """生成 tool 的执行函数。"""
    scpi = cmd_def.get("scpi_template")
    handler = cmd_def.get("handler")  # 自定义 Python handler 路径（预留）

    async def _handler(alias: str = "default", params_json: str = "{}") -> str:
        if alias not in sessions:
            return f"[FAIL] 未找到别名 '{alias}'"

        inst = sessions[alias]
        kwargs = {}
        if params_json:
            try:
                kwargs = json.loads(params_json)
            except Exception as e:
                return f"[FAIL] params_json 解析错误: {e}"

        # 记录调用历史
        entry = {
            "alias": alias,
            "command": cmd_name,
            "params": kwargs,
            "status": "running",
        }
        command_history.append(entry)

        try:
            # SCPI 模板驱动
            if scpi:
                return _execute_scpi(inst, scpi, kwargs, entry)

            # 自定义 handler（预留扩展点）
            if handler:
                module_path, func_name = handler.rsplit(".", 1)
                mod = __import__(module_path, fromlist=[func_name])
                func = getattr(mod, func_name)
                result = func(inst, **kwargs)
                entry["status"] = "ok"
                return result

            entry["status"] = "no_action"
            return "[FAIL] 命令未配置 scpi_template 或 handler"

        except Exception as e:
            entry["status"] = f"error: {e}"
            # 尝试读取仪器错误队列获取更详细信息
            try:
                err = inst.query("SYST:ERR?")
                entry["instrument_error"] = err
                return f"[FAIL] {e} | 仪器错误: {err}"
            except Exception:
                return f"[FAIL] {e}"

    return _handler


def _index_model_keywords(config: dict) -> None:
    """建立型号关键字到配置的索引。"""
    inst_type = config.get("instrument_type", "unknown")
    for kw in config.get("model_keywords", []):
        _MODEL_CONFIG_MAP[kw.upper()] = config
    # 也按 instrument_type 索引
    _MODEL_CONFIG_MAP[inst_type.upper()] = config


def discover_instrument_model(idn: str) -> Optional[str]:
    """根据 *IDN? 响应识别仪器型号，返回 instrument_type。

    IDN 格式示例: "Keysight Technologies,N9020A,MY12345678,A.12.34"
    """
    idn_upper = idn.upper()
    for keyword, config in _MODEL_CONFIG_MAP.items():
        if keyword in idn_upper:
            return config.get("instrument_type")
    return None


def get_commands_for_model(instrument_type: str) -> list:
    """获取指定仪器类型的所有命令定义。"""
    result = []
    for name, meta in _TOOL_REGISTRY.items():
        if meta["instrument_type"] == instrument_type or meta["instrument_type"] == "generic":
            result.append({"name": name, **meta})
    return result


def register_commands_from_yaml(
    mcp: FastMCP,
    sessions: Dict[str, Any],
    command_history: list,
    yaml_path: Optional[Path] = None,
):
    """从 YAML 文件注册所有命令为 MCP Tools。

    Args:
        mcp: FastMCP 实例
        sessions: 仪器会话字典（server 维护）
        command_history: 命令执行历史列表
        yaml_path: YAML 文件路径，None 则加载内置 commands/*.yaml 和用户自定义命令
    """
    configs = []

    if yaml_path:
        # 加载指定文件
        configs.append(_load_yaml(yaml_path))
    else:
        # 加载内置命令
        pkg_path = Path(__file__).parent
        for f in sorted(pkg_path.glob("*.yaml")):
            configs.append(_load_yaml(f))

        # 加载项目级自定义命令（从当前工作目录的 .instrument_mcp/）
        import os
        cwd = Path(os.getcwd())
        project_custom_dir = cwd / ".instrument_mcp"
        if project_custom_dir.exists():
            logger.info(f"Loading project custom commands from: {project_custom_dir}")
            for f in sorted(project_custom_dir.glob("*.yaml")):
                try:
                    configs.append(_load_yaml(f))
                    logger.info(f"Loaded project custom command: {f.name}")
                except Exception as e:
                    logger.warning(f"Failed to load {f}: {e}")

        # 加载用户级自定义命令（从环境变量）
        custom_dir = os.environ.get("INSTRUMENT_MCP_COMMANDS")
        if custom_dir:
            custom_path = Path(custom_dir)
            if custom_path.exists():
                logger.info(f"Loading user custom commands from: {custom_path}")
                for f in sorted(custom_path.glob("*.yaml")):
                    try:
                        configs.append(_load_yaml(f))
                        logger.info(f"Loaded user custom command: {f.name}")
                    except Exception as e:
                        logger.warning(f"Failed to load {f}: {e}")
            else:
                logger.warning(f"Custom commands directory not found: {custom_path}")

    for config in configs:
        inst_type = config.get("instrument_type", "unknown")
        # 建立型号索引
        _index_model_keywords(config)

        for cmd in config.get("commands", []):
            cmd_name = cmd["name"]

            # 生成 Pydantic 模型用于参数校验
            params = cmd.get("params", [])
            _build_pydantic_model(cmd_name, params)

            # 构建 handler
            handler = _make_tool_handler(
                cmd_name, cmd, inst_type, sessions, command_history
            )

            # 注册到 MCP
            annotations = cmd.get("annotations", {})
            description = f"[{inst_type.upper()}] {cmd['description']}"

            mcp.tool(name=cmd_name, description=description, annotations=annotations)(
                handler
            )

            # 缓存元数据
            _TOOL_REGISTRY[cmd_name] = {
                "instrument_type": inst_type,
                "description": cmd["description"],
                "params": params,
                "annotations": annotations,
                "scpi_template": cmd.get("scpi_template"),
            }

            logger.info(f"Registered tool: {cmd_name} ({inst_type})")


def get_tool_registry() -> Dict[str, Dict[str, Any]]:
    return dict(_TOOL_REGISTRY)


def get_model_config_map() -> Dict[str, Dict[str, Any]]:
    return dict(_MODEL_CONFIG_MAP)
