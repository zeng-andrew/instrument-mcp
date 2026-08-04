"""仪器诊断子包。

存放各型号仪器的健康检查 / 故障排查逻辑，由 commands/*.yaml 通过
``handler`` 字段引用。诊断函数签名约定与 commands/*_handler.py 一致：
``def func(inst, ...) -> str``，inst 为已连接的仪器会话对象。
"""
