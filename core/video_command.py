import asyncio
import base64
import time
from collections import deque
from typing import Dict, Any, Tuple, Optional

import aiohttp

from .api_clients import ApiClient
from .image_utils import ImageProcessor
from .image_uploader import TempImageUploader
from .video_watch import VideoWatcher

from src.plugin_system.base.base_command import BaseCommand
from src.common.logger import get_logger

logger = get_logger("video_command")


class VideoGenerationCommand(BaseCommand):
    """生成视频命令：/video, /video-l, /video-p。"""

    _config_overrides: Dict[str, Any] = {}
    _video_semaphore: Optional[asyncio.Semaphore] = None
    _user_semaphores: Dict[str, asyncio.Semaphore] = {}
    _user_requests: Dict[str, deque] = {}

    command_name = "video_command"
    command_description = "生成视频：/video(-l|-p) <描述>"
    command_pattern = r"(?:.*，说：\s*)??/(?P<command>video|video-l|video-p)\s+(?P<description>.+)$"

    def get_config(self, key: str, default=None):
        if key in self._config_overrides:
            return self._config_overrides[key]
        return super().get_config(key, default)

    def _get_user_id(self) -> Optional[str]:
        try:
            return str(self.message.message_info.user_info.user_id)
        except Exception:
            return None

    def _get_video_semaphore(self) -> asyncio.Semaphore:
        if self.__class__._video_semaphore is None:
            max_requests = self.get_config("components.max_requests", 3)
            max_requests = abs(int(max_requests)) + int(max_requests == 0)
            # 记录规范化后的并发上限，避免配置为 0
            self.__class__._config_overrides["components.max_requests"] = max_requests
            self.__class__._video_semaphore = asyncio.Semaphore(max_requests)
        return self.__class__._video_semaphore

    def _get_user_semaphore(self, user_id: str) -> asyncio.Semaphore:
        per_user = self.get_config("components.max_requests_per_user", 1)
        per_user = abs(int(per_user)) + int(per_user == 0)
        is_admin = user_id in self.get_config("components.admin_users", [])
        if user_id not in self._user_semaphores and not is_admin:
            self._user_semaphores[user_id] = asyncio.Semaphore(per_user)
        return self._user_semaphores[user_id] if not is_admin else None
    
    def _rate_limited(self, user_id: str) -> bool:
        window = int(self.get_config("components.rate_limit_window_seconds", 120))
        limit = int(self.get_config("components.max_requests_per_window", 3))
        now = time.time()
        q = self._user_requests.setdefault(user_id, deque())
        while q and (now - q[0]) > window:
            q.popleft()
        if len(q) >= limit:
            return True
        q.append(now)
        return False

    async def execute(self) -> Tuple[bool, Optional[str], bool]:
        """执行视频生成·并发限制"""
        user_id = self._get_user_id()
        is_admin = user_id in self.get_config("components.admin_users", [])
        if user_id and not is_admin:
            if self._rate_limited(user_id):
                await self.send_text("请求过于频繁，请稍后再试。")
                return False, "rate_limited", True
            
        semaphore = self._get_video_semaphore()
        user_semaphore = self._get_user_semaphore(user_id) if user_id and not is_admin else None

        have_waited = False
        if semaphore.locked() or user_semaphore and user_semaphore.locked():
            await self.send_text("视频生成任务排队中...")
            have_waited = True
        
        async with semaphore:
            if user_semaphore:
                async with user_semaphore:
                    return await self._execute_inner(have_waited)
            return await self._execute_inner(have_waited)

    async def _execute_inner(self, have_waited: bool) -> Tuple[bool, Optional[str], bool]:
        logger.info(f"{self.log_prefix} 执行 /video 命令")

        model_id = self.get_config("components.command_model", "model1")
        model_config = self._get_model_config(model_id)
        if not model_config:
            await self.send_text(f"模型 '{model_id}' 不存在。")
            return False, "model_not_found", True

        # 根据命令调整比例
        command = self.matched_groups.get("command", "").strip()
        model_config = self.get_video_size(command=command, model_config=model_config)

        description = self.matched_groups.get("description", "").strip()
        if not description:
            await self.send_text("请提供视频描述：/video(-l|-p) <描述>")
            return False, "missing_prompt", True

        ok, reason = self._validate_request(description)
        if not ok:
            await self.send_text(reason)
            return False, "invalid_request", True

        # 获取最近图片（用于图生视频）
        image_processor = ImageProcessor(self)
        input_image_base64 = await image_processor.get_recent_image()

        input_image_url = None
        enable_upload_image = self.get_config("image_uploader.enabled", False)
        if input_image_base64 and enable_upload_image:
            try:
                storage_uploader = TempImageUploader(
                    provider=self.get_config("image_uploader.provider", "cos"),
                    access_key_id=self.get_config("image_uploader.access_key_id", "access_key_id"),
                    secret_access_key=self.get_config("image_uploader.secret_access_key", "secret_access_key"),
                    bucket_name=self.get_config("image_uploader.bucket_name", "bucket_name"),
                    region=self.get_config("image_uploader.region", "region"),
                    endpoint=self.get_config("image_uploader.endpoint", "endpoint"),
                )
                input_image_url = storage_uploader.upload_base64_image(input_image_base64)
            except Exception as e:
                logger.error(f"{self.log_prefix} 图片上传失败: {e}")

        final_input_image = input_image_url or input_image_base64

        support_option = model_config.get("support_option", "3")
        if final_input_image and support_option == "1":
            await self.send_text("当前模型不支持图生视频。")
            return False, "image_not_supported", True
        if not final_input_image and support_option == "2":
            await self.send_text("当前模型不支持文生视频。")
            return False, "text_not_supported", True

        await asyncio.sleep(int(have_waited))
        await self.send_text("已开始生成视频，请稍候...", have_waited, self.message_recv_to_db_message())

        enable_debug = self.get_config("components.enable_debug_info", False)
        if enable_debug:
            await self.send_text(f"正在使用模型：{model_id}")

        try:
            api_client = ApiClient(self)
            success, result = await api_client.generate_video(
                prompt=description,
                model_config=model_config,
                input_image=final_input_image,
                model_id=model_id,
            )

            if not success:
                await self.send_text(str(result))
                return False, f"generate_failed: {result}", True

            video_ref = result
            allow_url_send = self.get_config("video.allow_url_send", True)
            fallback_download = self.get_config("video.url_send_fallback_to_download", True)

            # 麦麦看视频
            enable_watch_video = self.get_config("video_watch.enabled", False)
            video_description = "[视频]"
            watcher = VideoWatcher(self) if enable_watch_video else None

            # 优先尝试 URL 直发
            if self._is_url(video_ref) and allow_url_send:
                if watcher:
                    logger.info(f"{self.log_prefix} 为视频生成新描述...")
                    watch_ok, watch_text = await watcher.describe_video(video_ref)
                    if watch_ok and watch_text:
                        # 让麦麦“看懂”视频内容，便于后续记忆与上下文理解
                        logger.info(f"{self.log_prefix} 视频描述生成：{watch_text}")
                        video_description = f"[视频：{watch_text}]"
                    else:
                        logger.warning(f"{self.log_prefix} 麦麦看视频失败: {watch_text}")
                        if self.get_config("components.enable_debug_info", False):
                            await self.send_text(f"麦麦看视频失败：{watch_text}")

                send_ok = await self.send_custom("videourl", video_ref, video_description)
                if send_ok:
                    await self._change_database_message(video_description)
                    await self.send_text("视频已生成并发送")
                    return True, "ok", True
                if not fallback_download:
                    await self.send_text(f"视频发送失败，请自行下载观看：{video_ref}")
                    return False, "send_failed", True

            # URL 直发失败时回退到下载+base64（受大小限制）
            encoded_success, encoded_result = await self._download_and_encode_base64(video_ref)
            if not encoded_success:
                await self.send_text(f"下载/编码失败：{encoded_result}")
                return False, "encode_failed", True

            # 已拿到 base64 后再“看视频”，避免重复下载
            if watcher:
                logger.info(f"{self.log_prefix} 为视频生成新描述...")
                watch_ok, watch_text = await watcher.describe_video_base64(encoded_result)
                if watch_ok and watch_text:
                    # 让麦麦“看懂”视频内容，便于后续记忆与上下文理解
                    logger.info(f"{self.log_prefix} 视频描述生成：{watch_text}")
                    video_description = f"[视频：{watch_text}]"
                else:
                    logger.warning(f"{self.log_prefix} 麦麦看视频失败: {watch_text}")
                    if self.get_config("components.enable_debug_info", False):
                        await self.send_text(f"麦麦看视频失败：{watch_text}")

            send_ok = await self.send_custom("video", encoded_result, video_description)
            if send_ok:
                await self._change_database_message(video_description)
                await self.send_text("视频已生成并发送")
                return True, "ok", True
            await self.send_text(f"视频发送失败，请自行下载观看：{video_ref}")
            return False, "send_failed", True

        except Exception as e:
            logger.error(f"{self.log_prefix} 执行异常: {e!r}", exc_info=True)
            await self.send_text("发生异常，请稍后重试。")
            return False, "execute_error", True

    def _validate_request(self, description: str) -> Tuple[bool, str]:
        max_prompt = int(self.get_config("video.max_prompt_length", 800))
        if len(description) > max_prompt:
            return False, f"描述过长（最多 {max_prompt} 字）。"
        return True, ""

    def _get_model_config(self, model_id: str) -> Optional[Dict[str, Any]]:
        try:
            model_config = self.get_config(f"models.{model_id}")
            if model_config and isinstance(model_config, dict):
                return model_config
            logger.warning(f"{self.log_prefix} 模型 {model_id} 缺失或配置无效")
            return None
        except Exception as e:
            logger.error(f"{self.log_prefix} 获取模型配置失败: {e!r}")
            return None

    @staticmethod
    def get_video_size(command: str, model_config: Dict[str, Any]):
        api_format = model_config.get("format", "openai")
        model = model_config.get("model", "sora2")

        if api_format == "openai":
            resolution = model_config.get("resolution", "720p")
            if command == "video":
                size = None
            elif command == "video-l":
                size = "1792x1024" if resolution == "1080p" else "1280x720"
            else:
                size = "1024x1792" if resolution == "1080p" else "720x1280"
            model_config["size"] = size

        if api_format == "siliconflow":
            if command == "video":
                size = None
            elif command == "video-l":
                size = "1280x720"
            else:
                size = "720x1280"
            model_config["size"] = size

        if api_format == "doubao":
            if command == "video":
                ratio = "adaptive"
            elif command == "video-l":
                ratio = "16:9"
            else:
                ratio = "9:16"
            model_config["ratio"] = ratio

        if api_format == "vectorengine":
            if command == "video":
                aspect_ratio = None
                orientation = None
            elif command == "video-l":
                aspect_ratio = "16:9" if ("veo3" in model) else "3:2"
                orientation = "landscape" if ("sora-2" in model) else None
            else:
                aspect_ratio = "9:16" if ("veo3" in model) else "2:3"
                orientation = "portrait" if ("sora-2" in model) else None
            if "veo" in model:
                model_config["resolution"] = None
            model_config["aspect_ratio"] = None if ("veo2" in model or "sora" in model) else aspect_ratio
            model_config["orientation"] = orientation

        return model_config

    def _is_url(self, value: str) -> bool:
        return isinstance(value, str) and value.startswith(("http://", "https://"))
    
    async def _change_database_message(self, video_description: str):
        """修改数据库中视频消息的processed_plain_text字段内容"""
        from src.plugin_system import database_api
        from src.common.database.database_model import Messages
        messages = await database_api.db_query(
            Messages,
            query_type="get",
            filters={"chat_id": self.message.chat_stream.stream_id},
            limit=1,
            order_by=["-time"]
        )
        video_message_id = messages[0]["message_id"]
        record = await database_api.db_save(
            Messages,
            {"processed_plain_text": video_description},
            key_field="message_id",
            key_value=video_message_id
        )
        logger.debug(f"修改消息：{record}")


    async def _download_and_encode_base64(self, video_url: str) -> Tuple[bool, str]:
        # 已是 base64 或非法 URL 直接返回
        if not self._is_url(video_url):
            if isinstance(video_url, str) and video_url.startswith("base64://"):
                return True, video_url
            return False, "无效的视频地址"

        max_mb = int(self.get_config("video.max_video_mb_for_base64", 20))
        max_bytes = max_mb * 1024 * 1024

        proxy_enabled = self.get_config("proxy.enabled", False)
        proxy_url = self.get_config("proxy.url", "http://127.0.0.1:7890")
        proxy = proxy_url if proxy_enabled else None

        timeout = aiohttp.ClientTimeout(total=240)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(video_url, proxy=proxy) as resp:
                    if resp.status != 200:
                        return False, f"下载失败：HTTP {resp.status}"
                    content_length = resp.headers.get("Content-Length")
                    if content_length and int(content_length) > max_bytes:
                        return False, f"视频过大（超过 {max_mb}MB）"

                    data = await resp.read()
                    if len(data) > max_bytes:
                        return False, f"视频过大（超过 {max_mb}MB）"

                    video_base64 = base64.b64encode(data).decode("ascii")
                    return True, video_base64
        except asyncio.TimeoutError:
            return False, "下载超时"
        except Exception as e:
            logger.error(f"{self.log_prefix} 下载失败: {e}")
            return False, "下载失败"

    def message_recv_to_db_message(self):  # -> DatabaseMessages:
        """将 MessageRecv 对象转换为 DatabaseMessages 对象"""
        from json import dumps
        from src.common.data_models.database_data_model import DatabaseMessages

        message_obj = self.message
        msg_info = message_obj.message_info
        user_info = msg_info.user_info
        group_info = msg_info.group_info

        chat_stream = getattr(message_obj, "chat_stream", None)
        chat_id = getattr(chat_stream, "stream_id", "") if chat_stream else ""
        chat_info_platform = getattr(chat_stream, "platform", None) or (msg_info.platform or "")
        chat_info_create_time = getattr(chat_stream, "create_time", 0.0) if chat_stream else 0.0
        chat_info_last_active_time = getattr(chat_stream, "last_active_time", 0.0) if chat_stream else 0.0

        chat_info_user = getattr(chat_stream, "user_info", None) if chat_stream else user_info
        effective_group_info = getattr(chat_stream, "group_info", None) if chat_stream else group_info

        def _dump(val):
            return dumps(val, ensure_ascii=False) if val is not None and not isinstance(val, str) else val

        reply = getattr(message_obj, "reply", None)
        reply_to = getattr(message_obj, "reply_to", None) or (
            getattr(getattr(reply, "message_info", None), "message_id", None) if reply else None
        )

        return DatabaseMessages(
            message_id=msg_info.message_id or "",
            time=msg_info.time or 0.0,
            chat_id=chat_id,
            reply_to=reply_to,
            interest_value=getattr(message_obj, "interest_value", None),
            key_words=_dump(getattr(message_obj, "key_words", None)),
            key_words_lite=_dump(getattr(message_obj, "key_words_lite", None)),
            is_mentioned=getattr(message_obj, "is_mentioned", None),
            is_at=getattr(message_obj, "is_at", None),
            reply_probability_boost=getattr(message_obj, "reply_probability_boost", None),
            processed_plain_text=message_obj.processed_plain_text,
            display_message=getattr(message_obj, "display_message", None),
            priority_mode=getattr(message_obj, "priority_mode", None),
            priority_info=_dump(getattr(message_obj, "priority_info", None)),
            additional_config=_dump(getattr(msg_info, "additional_config", None)),
            is_emoji=getattr(message_obj, "is_emoji", False),
            is_picid=getattr(message_obj, "is_picid", False),
            is_command=getattr(message_obj, "is_command", False),
            intercept_message_level=getattr(message_obj, "intercept_message_level", 0),
            is_notify=getattr(message_obj, "is_notify", False),
            selected_expressions=_dump(getattr(message_obj, "selected_expressions", None)),
            user_id=user_info.user_id or "",
            user_nickname=user_info.user_nickname or "",
            user_cardname=user_info.user_cardname,
            user_platform=user_info.platform or "",
            chat_info_group_id=effective_group_info.group_id if effective_group_info else None,
            chat_info_group_name=effective_group_info.group_name if effective_group_info else None,
            chat_info_group_platform=(
                getattr(effective_group_info, "group_platform", None) if effective_group_info else None
            ) or (getattr(effective_group_info, "platform", None) if effective_group_info else None),
            chat_info_user_id=chat_info_user.user_id or "",
            chat_info_user_nickname=chat_info_user.user_nickname or "",
            chat_info_user_cardname=chat_info_user.user_cardname,
            chat_info_user_platform=chat_info_user.platform or "",
            chat_info_stream_id=chat_id,
            chat_info_platform=chat_info_platform or "",
            chat_info_create_time=chat_info_create_time,
            chat_info_last_active_time=chat_info_last_active_time,
        )


class VideoConfigCommand(BaseCommand):
    """视频配置管理命令。"""

    command_name = "video_config_command"
    command_description = "视频配置：/video <操作> [参数]"
    command_pattern = r"(?:.*,\s*)?/video\s+(?P<action>list|models|config|set|reset|help)(?:\s+(?P<params>.*))?$"

    def get_config(self, key: str, default=None):
        if key in VideoGenerationCommand._config_overrides:
            return VideoGenerationCommand._config_overrides[key]
        return super().get_config(key, default)

    async def execute(self) -> Tuple[bool, Optional[str], bool]:
        action = self.matched_groups.get("action", "").strip()
        params = (self.matched_groups.get("params", "") or "").strip()

        has_permission = self._check_permission()
        if not has_permission and action not in ["list", "models", "help"]:
            await self.send_text("无权限使用该命令", storage_message=False)
            return False, "no_permission", True

        if action in ("list", "models"):
            return await self._list_models()
        if action == "set":
            return await self._set_model(params)
        if action == "config":
            return await self._show_current_config()
        if action == "reset":
            return await self._reset_config()
        if action == "help":
            return await self._show_help()

        await self.send_text("无效操作，请使用 /video help 查看帮助")
        return False, "invalid_action", True

    async def _list_models(self) -> Tuple[bool, Optional[str], bool]:
        try:
            models_config = self.get_config("models", {})
            if not models_config:
                await self.send_text("未配置任何模型。")
                return False, "no_models", True

            current_model = self.get_config("components.command_model", "model1")
            lines = ["可用模型列表："]
            for model_id, config in models_config.items():
                if not isinstance(config, dict):
                    continue
                model_name = config.get("name", "unknown")
                model = config.get("model", "unknown")
                mark = "✅[当前使用]" if model_id == current_model else ""
                lines.append(f"- {model_id}{mark}\n  名称: {model_name}\n  模型: {model}")

            await self.send_text("\n".join(lines))
            return True, "ok", True
        except Exception as e:
            logger.error(f"{self.log_prefix} 列出模型失败: {e!r}")
            await self.send_text("列出模型失败。")
            return False, "list_failed", True

    async def _set_model(self, model_id: str) -> Tuple[bool, Optional[str], bool]:
        try:
            if not model_id:
                await self.send_text("用法：/video set <模型ID>")
                return False, "missing_model_id", True

            model_config = self.get_config(f"models.{model_id}")
            if not model_config:
                await self.send_text("模型不存在，请使用 /video list 查看可用模型。")
                return False, "model_not_found", True

            current = self.get_config("components.command_model", "model1")
            if current == model_id:
                await self.send_text("当前已在使用该模型。")
                return True, "ok", True

            if await self._update_command_model_config(model_id):
                await self.send_text(f"已切换到模型：{model_id}")
                return True, "ok", True

            await self.send_text("切换失败，请手动修改配置文件。")
            return False, "switch_failed", True
        except Exception as e:
            logger.error(f"{self.log_prefix} 设置模型失败: {e!r}")
            await self.send_text("设置模型失败。")
            return False, "set_failed", True

    async def _update_command_model_config(self, model_id: str) -> bool:
        try:
            VideoGenerationCommand._config_overrides["components.command_model"] = model_id
            return True
        except Exception as e:
            logger.error(f"{self.log_prefix} 更新配置失败: {e!r}")
            return False

    async def _reset_config(self) -> Tuple[bool, Optional[str], bool]:
        try:
            VideoGenerationCommand._config_overrides.clear()
            default_model = super().get_config("components.command_model", "model1")
            await self.send_text(f"配置已重置，默认模型：{default_model}")
            return True, "ok", True
        except Exception as e:
            logger.error(f"{self.log_prefix} 重置配置失败: {e!r}")
            await self.send_text("重置失败。")
            return False, "reset_failed", True

    async def _show_current_config(self) -> Tuple[bool, Optional[str], bool]:
        try:
            command_model = self.get_config("components.command_model", "model1")
            original = super().get_config("components.command_model", "model1")
            has_override = command_model != original
            command_config = self.get_config(f"models.{command_model}", {})

            lines = [
                "当前配置：",
                f"- 模型：{command_model}" + ("（运行时覆盖）" if has_override else ""),
                f"- 名称：{command_config.get('name', 'unknown') if isinstance(command_config, dict) else 'unknown'}",
                f"- 模型ID：{command_config.get('model', 'unknown') if isinstance(command_config, dict) else 'unknown'}",
            ]
            if has_override:
                lines.append(f"- 原始配置：{original}")

            lines.extend(
                [
                    "",
                    "管理员命令：",
                    "/video list",
                    "/video set <模型ID>",
                    "/video reset",
                ]
            )
            await self.send_text("\n".join(lines))
            return True, "ok", True
        except Exception as e:
            logger.error(f"{self.log_prefix} 显示配置失败: {e!r}")
            await self.send_text("显示配置失败。")
            return False, "show_failed", True

    async def _show_help(self) -> Tuple[bool, Optional[str], bool]:
        try:
            help_text = "\n".join(
                [
                    "视频生成帮助",
                    "/video <描述> - 默认比例",
                    "/video-l <描述> - 横屏",
                    "/video-p <描述> - 竖屏",
                    "/video list - 查看模型列表",
                    "/video help - 查看帮助",
                ]
            )
            await self.send_text(help_text)
            return True, "ok", True
        except Exception as e:
            logger.error(f"{self.log_prefix} 帮助信息失败: {e!r}")
            await self.send_text("帮助信息获取失败。")
            return False, "help_failed", True

    def _check_permission(self) -> bool:
        try:
            admin_users = self.get_config("components.admin_users", [])
            user_id = (
                str(self.message.message_info.user_info.user_id)
                if self.message and self.message.message_info and self.message.message_info.user_info
                else None
            )
            return user_id in admin_users
        except Exception:
            return False
