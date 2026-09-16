"""小米云端扫码登录 + 米家设备 token 提取。

扫码登录是最简单稳定的 token 提取方式：不需要账号密码、不触发邮箱
2FA、只需米家 App 扫码确认。流程（longPolling/loginUrl 拿二维码 ->
长轮询 lp 链接 -> 换 serviceToken -> 拉设备列表）参考
Xiaomi-cloud-tokens-extractor（PiotrMachowski, MIT）：
https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor

注意：登录链路需访问 account.xiaomi.com / sts.api.io.mi.com /
api.io.mi.com。公司网络可能拦截（TLS 被重置），届时切手机热点再操作；
提取到的 token 长期有效，之后局域网控制不再依赖云端。
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

LOGIN_BASE = "https://account.xiaomi.com"
STS_CALLBACK = "https://sts.api.io.mi.com/sts"


def _to_json(text: str) -> dict:
    """小米接口的 JSON 响应带 &&&START&&& 前缀。"""
    return json.loads(text.replace("&&&START&&&", ""))


class MiCloudQRLogin:
    """一次扫码登录会话：start() 生成二维码，poll() 等待扫码确认并完成登录。"""

    def __init__(self, server: str = "cn", qr_dir: Optional[str] = None):
        self.server = server
        self._session = requests.session()
        self.qr_image_url: Optional[str] = None
        self.login_url: Optional[str] = None   # 二维码内容，无法看图时的备用链接
        self.qr_path: Optional[Path] = None    # 保存到本地的二维码图片
        self._lp_url: Optional[str] = None     # 长轮询地址
        self._qr_timeout: int = 180            # 二维码有效期（秒，服务端返回）
        self._qr_dir = Path(qr_dir) if qr_dir else Path(os.getcwd())
        self.user_id: Optional[int] = None
        self._ssecurity: Optional[str] = None
        self._service_token: Optional[str] = None

    @property
    def qr_timeout(self) -> int:
        return self._qr_timeout

    def start(self) -> Path:
        """发起扫码登录：获取并保存二维码图片，返回图片路径。"""
        resp = self._session.get(
            f"{LOGIN_BASE}/longPolling/loginUrl",
            params={
                "_qrsize": "480",
                "qs": "%3Fsid%3Dxiaomiio%26_json%3Dtrue",
                "callback": STS_CALLBACK,
                "_hasLogo": "false",
                "sid": "xiaomiio",
                "serviceParam": "",
                "_locale": "zh_CN",
                "_dc": str(int(time.time() * 1000)),
            },
            timeout=15,
        )
        data = _to_json(resp.text)
        for key in ("qr", "loginUrl", "lp"):
            if key not in data:
                raise RuntimeError(f"获取登录二维码失败，响应缺少 {key}: {resp.text[:200]}")
        self.qr_image_url = data["qr"]
        self.login_url = data["loginUrl"]
        self._lp_url = data["lp"]
        self._qr_timeout = int(data.get("timeout", 180))

        img = self._session.get(self.qr_image_url, timeout=15)
        img.raise_for_status()
        ctype = img.headers.get("Content-Type", "")
        ext = ".gif" if "gif" in ctype else ".png"
        self.qr_path = self._qr_dir / f"mi_cloud_login_qr{ext}"
        self.qr_path.write_bytes(img.content)
        logger.info(f"二维码已保存: {self.qr_path}")
        return self.qr_path

    def poll(self, timeout_s: int = 120) -> None:
        """长轮询等待扫码确认，完成后换取 serviceToken。超时抛 RuntimeError。"""
        if not self._lp_url:
            raise RuntimeError("尚未调用 start()")
        deadline = time.time() + min(timeout_s, self._qr_timeout)
        last_err = None
        while time.time() < deadline:
            try:
                resp = self._session.get(self._lp_url, timeout=10)
            except requests.exceptions.Timeout:
                continue  # 长轮询自然超时，继续等
            except requests.exceptions.RequestException as e:
                last_err = e
                time.sleep(1)
                continue
            if resp.status_code != 200:
                last_err = RuntimeError(f"长轮询返回 {resp.status_code}")
                time.sleep(1)
                continue

            data = _to_json(resp.text)
            self.user_id = data["userId"]
            self._ssecurity = data["ssecurity"]
            location = data["location"]

            # 跟随 location 换取 serviceToken（种进 cookie）
            r = self._session.get(
                location,
                headers={"content-type": "application/x-www-form-urlencoded"},
                timeout=15,
            )
            token = r.cookies.get("serviceToken")
            if not token:
                raise RuntimeError("扫码成功但未能换取 serviceToken")
            self._service_token = token
            logger.info(f"扫码登录成功，userId={self.user_id}")
            return
        raise RuntimeError(
            f"等待扫码超时（{min(timeout_s, self._qr_timeout)}s）"
            + (f"，最后错误: {last_err}" if last_err else "，二维码可能已过期，请重新发起")
        )

    def list_devices(self) -> list:
        """拉取账号下全部米家设备（含 token/localip），复用 micloud 的签名实现。"""
        if not (self.user_id and self._ssecurity and self._service_token):
            raise RuntimeError("尚未完成登录")
        from micloud.micloud import MiCloud

        mc = MiCloud()
        mc.user_id = self.user_id
        mc.ssecurity = self._ssecurity
        mc.service_token = self._service_token
        devices = mc.get_devices(country=self.server)
        if devices is None:
            raise RuntimeError(f"从 {self.server} 区拉取设备列表失败")
        return devices


def update_config(ip: str, token: str, config_path: Optional[Path] = None) -> Path:
    """把 ip/token 写入 mi_plug_config.json（保留其他已有字段）。"""
    path = config_path or Path(os.getcwd()) / "mi_plug_config.json"
    cfg = {}
    if path.is_file():
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"读取现有 {path} 失败，将重建: {e}")
    cfg["ip"] = ip
    cfg["token"] = token
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
