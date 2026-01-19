import asyncio
import aiohttp
import re
from typing import Tuple

from src.common.logger import get_logger

logger = get_logger("video_send_client")

class VideoSendCilent:

    def __init__(self, host, port, message):
        self.http_url = f"http://{host}:{port}"
        self.message = message
        self.delay = 2    # 发送视频时延时重试
        self.max_retry = 2  # 发送视频时最大重试

    def _is_private_message(self) -> bool:
        """检测消息是否为私聊消息"""
        try:
            if self.message.message_info.group_info:
                return False
            else:
                return True
        except Exception as e:
            logger.error(f"未知聊天: {e}")
            return None  # 默认返回群聊
            
    def _get_user_id(self) -> str | None:
        """从消息中获取用户ID"""
        try:
            user_info = self.message.message_info.user_info
            return user_info.user_id
        except Exception as e:
            logger.error(f"获取私聊ID失败: {e}")
            return None
        
    def _get_group_id(self) -> str | None:
        """从消息中获取群ID"""
        try:
            group_info = self.message.message_info.group_info
            return group_info.group_id
        except Exception as e:
            logger.error(f"获取群聊ID失败: {e}")
            return None
    
    def _analyze_napcat_response(self, data: dict) -> Tuple[bool, str]:
        """
        返回:
        (success, reason)
        """
        try:
            # 标准成功
            if data.get("status") == "ok":
                return True, "ok"

            retcode = data.get("retcode")
            msg = (data.get("message") or "").lower()

            # ===== 风控类（禁止重试）=====
            if "风控" in msg or "risk" in msg:
                return False, "risk_control"

            if retcode in (100, 120, 121):
                return False, "risk_control"

            # ===== 参数 / 文件错误（禁止重试）=====
            if "file" in msg or "video" in msg:
                return False, "file_error"
            
            # ===== 磁盘空间不足（禁止重试）=====
            if "ENOSPC" in msg or "no space left on device" in msg:
                return False, "no_space"

            # ===== 可恢复错误 =====
            logger.warning(f"可重试错误：{data}")
            return False, "retryable"

        except Exception as e:
            logger.warning(f"FUCKING QQ : {e}")
            return False, "retryable"

    def convert_windows_to_wsl_path(windows_path: str) -> str:
        """将Windows路径转换为WSL路径
        
        例如：E:\path\to\file.mp4 -> /mnt/e/path/to/file.mp4
        """
        try:
            import subprocess
            # 尝试使用wslpath命令转换路径（从Windows调用WSL）
            try:
                # 在Windows上调用wsl wslpath命令
                result = subprocess.run(['wsl', 'wslpath', '-u', windows_path], 
                                    capture_output=True, text=False, check=True)
                wsl_path = result.stdout.decode('utf-8', errors='replace').strip()
                if wsl_path:
                    return wsl_path
            except (subprocess.SubprocessError, FileNotFoundError):
                pass
                
            # 如果wslpath命令失败，手动转换路径
            # 移除盘符中的冒号，将反斜杠转换为正斜杠
            if re.match(r'^[a-zA-Z]:', windows_path):
                drive = windows_path[0].lower()
                path = windows_path[2:].replace('\\', '/')
                return f"/mnt/{drive}/{path}"
            return windows_path
        except Exception:
            # 转换失败时返回原路径
            return windows_path

    def convert_to_wsl_path(self, path: str) -> str:
        """将Windows路径转换为WSL路径，Linux路径保持不变"""
        # 检查是否是Windows路径（包含盘符或反斜杠）
        if re.match(r'^[a-zA-Z]:[\\/]', path) or '\\' in path:
            # 调用你的现有转换逻辑
            return self.convert_windows_to_wsl_path(path)
        else:
            # Linux/WSL路径，直接返回
            return path

    async def _send_private_video(self, encoded_video: str, user_id: str) -> Tuple[bool, str]:
        """通过API发送私聊视频
        
        Args:
            encoded_video: base64编码视频
            user_id: 目标用户ID
        """
        
        # 获取配置的端口
        api_url = f"{self.http_url}/send_private_msg"
        
        # 构造请求数据
        payload = {
            "user_id": user_id,
            "message": [
                {
                    "type": "video",
                    "data": {
                        "file": encoded_video
                    }
                }
            ]
        }
        
        logger.debug(f"私聊视频发送api_url: {api_url}")
        logger.debug(f"请求data: {payload}")
            
        delay_time = self.delay
        for attempt in range(1, self.max_retry + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(api_url, json=payload, timeout=480) as resp:
                        text = await resp.text()

                        if resp.status != 200:
                            logger.error(f"HTTP {resp.status}: {text}")
                            return False, f"本地网络异常HTTP {resp.status}: {text}"

                        data = await resp.json()
                        success, reason = self._analyze_napcat_response(data)

                        if success:
                            logger.info("NapCat 视频发送成功")
                            return True, None

                        if reason == "risk_control":
                            logger.error("⚠️ QQ 风控触发，停止重试")
                            return False, "⚠️ QQ 风控触发"
                        
                        if reason == "file_error":
                            logger.error("⚠️ 参数/文件错误，停止重试")
                            return False, "⚠️ 参数/文件错误"
                        
                        if reason == "no_space":
                            logger.error("⚠️ 磁盘空间不足，停止重试")
                            return False, "⚠️ 磁盘空间不足"

                        logger.warning(
                            f"NapCat 返回失败(reason={reason})，第 {attempt}/{self.max_retry} 次重试"
                        )

            except asyncio.TimeoutError:
                logger.warning(f"NapCat 请求超时，第 {attempt}/{self.max_retry} 次重试")
            except Exception as e:
                logger.warning(f"NapCat 异常({e})，第 {attempt}/{self.max_retry} 次重试")

            if attempt < self.max_retry:
                await asyncio.sleep(delay_time)
                delay_time *= 2

        logger.error("NapCat 多次重试仍失败")
        return False, "NapCat 多次重试仍失败"

    async def _send_group_video(self, encoded_video: str, group_id: str) -> Tuple[bool, str]:
        """通过API发送群视频
        
        Args:
            encoded_video: base64编码视频
            group_id: 目标群ID
        """
        
        # 获取配置的端口
        api_url = f"{self.http_url}/send_group_msg"
        
        # 构造请求数据
        payload = {
            "group_id": group_id,
            "message": [
                {
                    "type": "video",
                    "data": {
                        "file": encoded_video
                    }
                }
            ]
        }
        
        logger.debug(f"群聊视频发送api_url: {api_url}")
        logger.debug(f"请求data: {payload}")

        delay_time = self.delay
        for attempt in range(1, self.max_retry + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(api_url, json=payload, timeout=480) as resp:
                        text = await resp.text()

                        if resp.status != 200:
                            logger.error(f"HTTP {resp.status}: {text}")
                            return False, f"本地网络异常HTTP {resp.status}: {text}"

                        data = await resp.json()
                        success, reason = self._analyze_napcat_response(data)

                        if success:
                            logger.info("NapCat 视频发送成功")
                            return True, None

                        if reason == "risk_control":
                            logger.error("⚠️ QQ 风控触发，停止重试")
                            return False, "⚠️ QQ 风控触发"
                        
                        if reason == "file_error":
                            logger.error("⚠️ 参数/文件错误，停止重试")
                            return False, "⚠️ 参数/文件错误"
                        
                        if reason == "no_space":
                            logger.error("⚠️ 磁盘空间不足，停止重试")
                            return False, "⚠️ 磁盘空间不足"

                        logger.warning(
                            f"NapCat 返回失败(reason={reason})，第 {attempt}/{self.max_retry} 次重试"
                        )

            except asyncio.TimeoutError:
                logger.warning(f"NapCat 请求超时，第 {attempt}/{self.max_retry} 次重试")
            except Exception as e:
                logger.warning(f"NapCat 异常({e})，第 {attempt}/{self.max_retry} 次重试")

            if attempt < self.max_retry:
                await asyncio.sleep(delay_time)
                delay_time *= 2

        logger.error("NapCat 多次重试仍失败")
        return False, "NapCat 多次重试仍失败"
        
    # 发送处理后的视频文件
    async def try_send(self, encoded_video: str) -> Tuple[bool, str]:
        """发送视频消息"""
        
        # 检查是否为私聊消息
        is_private = self._is_private_message()
        
        if is_private:
            # 私聊消息，使用专用API发送
            user_id = self._get_user_id()
            if user_id:
                logger.debug(f"检测到环境为私聊，私聊ID: {user_id}")
                return await self._send_private_video(encoded_video, user_id)
            else:
                logger.error("无法获取私聊ID")
                return False, "无法获取私聊ID"
        else:
            # 群聊消息，使用群视频API
            group_id = self._get_group_id()
            if group_id:
                logger.debug(f"检测到环境为群聊，群聊ID: {group_id}")
                return await self._send_group_video(encoded_video, group_id)
            else:
                logger.error("无法获取群聊ID")
                return False, "无法获取群聊ID"