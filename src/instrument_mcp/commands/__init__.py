"""命令配置加载器。

从 YAML 动态生成 MCP Tools，支持按仪器型号自适应。
"""

import json
import logging
import pkgutil
from pathlib import Path
from typing import Any, Dict, Callable, Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

from pydantic import BaseModel, Field, create_model

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# 已注册的 tools 缓存（用于 list / invoke）
_TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {}


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
        command_history.append(
            {
                "alias": alias,
                "command": cmd_name,
                "params": kwargs,
                "status": "running",
            }
        )
        entry = command_history[-1]

        try:
            # SCPI 模板驱动
            if scpi:
                if isinstance(scpi, str):
                    # 纯写命令
                    formatted = scpi.format(**kwargs) if kwargs else scpi
                    inst.write(formatted)
                    entry["status"] = "ok"
                    return f"[PASS] {formatted}"

                elif isinstance(scpi, dict):
                    # write + query 组合
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
                    # 多条命令顺序执行
                    for template in scpi:
                        cmd = template.format(**kwargs) if kwargs else template
                        inst.write(cmd)
                    entry["status"] = "ok"
                    return "[PASS] OK"

            # 自定义 handler（预留扩展点）
            if handler:
                # 例如 handler: "instrument_mcp.custom_handlers.mxa_special"
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
            return f"[FAIL] {e}"

    return _handler


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
        yaml_path: YAML 文件路径，None 则加载内置 commands/*.yaml
    """
    if yaml_path:
        configs = [_load_yaml(yaml_path)]
    else:
        # 加载包内所有 yaml
        configs = []
        pkg_path = Path(__file__).parent
        for f in pkg_path.glob("*.yaml"):
            configs.append(_load_yaml(f))

    for config in configs:
        inst_type = config.get("instrument_type", "unknown")
        for cmd in config.get("commands", []):
            cmd_name = cmd["name"]

            # 生成 Pydantic 模型用于参数校验
            params = cmd.get("params", [])
            _build_pydantic_model(cmd_name, params)  # 校验用，暂不绑定

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
