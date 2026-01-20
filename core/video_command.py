import asyncio
from typing import Dict, Any, Tuple, Optional

from .api_clients import ApiClient
from .image_utils import ImageProcessor
from .image_uploader import TempImageUploader
from .video_send_cilent import VideoSendCilent

from src.plugin_system.base.base_command import BaseCommand
from src.common.logger import get_logger


logger = get_logger("video_command")

class VideoGenerationCommand(BaseCommand):
    """生成视频命令，直接通过 /video <描述> 实现视频生成"""

    # 类级别的配置覆盖
    _config_overrides = {}
    # 全局并发限制
    _video_semaphore: Optional[asyncio.Semaphore] = None

    # Command基本信息
    command_name = "video_command"
    command_description = "生成视频命令：/video(-l|-p) <描述>"
    command_pattern = r"(?:.*，说：\s*)?/(?P<command>video|video-l|video-p)\s+(?P<description>.+)$"

    def get_config(self, key: str, default=None):
        """覆盖get_config方法以支持动态配置"""
        # 检查是否有配置覆盖
        if key in self._config_overrides:
            return self._config_overrides[key]
        # 否则使用父类的get_config
        return super().get_config(key, default)
    
    def _get_video_semaphore(self) -> asyncio.Semaphore:
        # 第一次初始化
        if (self.__class__._video_semaphore is None):
            max_requests = self.get_config("components.max_requests", 3)
            max_requests = abs(max_requests) + int(max_requests==0)
            self.__class__._config_overrides["components.max_requests"] = max_requests
            logger.info(f"{self.log_prefix} 初始化视频并发限制: {max_requests}")
            self.__class__._video_semaphore = asyncio.Semaphore(max_requests)

        return self.__class__._video_semaphore


    async def execute(self) -> Tuple[bool, Optional[str], bool]:
        semaphore = self._get_video_semaphore()
        # 并发受限
        if semaphore.locked():
            await self.send_text(f"当前视频生成任务较多，请稍后再试（最多同时处理 {self.get_config("components.max_requests", 3)} 个）")
            return False, "并发受限", True
        # 并发控制入口
        async with semaphore:
            return await self._execute_inner()

    async def _execute_inner(self) -> Tuple[bool, Optional[str], bool]:
        logger.info(f"{self.log_prefix} 执行 /video(-l|-p) 生成视频命令")

        # 读取模型ID
        model_id = self.get_config("components.command_model", "model1")
        model_config = self._get_model_config(model_id)
        if not model_config:
            await self.send_text(f"模型 '{model_id}' 不存在，请检查插件配置")
            return False, "模型配置不存在", True

        # 根据命令格式调整生成视频分辨率比例
        command = self.matched_groups.get("command", "").strip()
        model_config = self.get_video_size(command=command, model_config=model_config)
        
        # 提取消息中提示词
        description = self.matched_groups.get("description", "").strip()
        if not description:
            await self.send_text("请提供视频描述，格式：/video(-l|-p) <描述>")
            return False, "缺少描述参数", True

        # 启用图片工具类,获取最近图片
        image_processor = ImageProcessor(self)
        input_image_base64 = await image_processor.get_recent_image()

        input_image_url = None
        # 检查是否启用调试信息
        enable_upload_image = self.get_config("image_uploader.enabled", False)
        try:
            # 如果有图片且开启对象储存服务
            if input_image_base64 and enable_upload_image:
                # 实例化上传器,并转为url
                storage_uploader = TempImageUploader(
                    provider=self.get_config("image_uploader.provider","cos"),
                    access_key_id=self.get_config("image_uploader.access_key_id","access_key_id"),
                    secret_access_key=self.get_config("image_uploader.secret_access_key","secret_access_key"),
                    bucket_name=self.get_config("image_uploader.bucket_name","bucket_name"),
                    region=self.get_config("image_uploader.region","region"),
                    endpoint=self.get_config("image_uploader.endpoint","endpoint"),
                )
                input_image_url = storage_uploader.upload_base64_image(input_image_base64)
        except Exception as e:
            logger.error(f"{self.log_prefix} 图片上传错误: {e}")

        final_input_image = None
        if input_image_url:
            final_input_image = input_image_url
        else:
            final_input_image = input_image_base64

        support_option = model_config.get("support_option", "3")
        if final_input_image and support_option == "1":
            await self.send_text("当前模型不支持图生视频功能")
            return False, "当前模型不支持图生视频功能", True
        if not final_input_image and support_option == "2":
            await self.send_text("当前模型不支持文生视频功能")
            return False, "当前模型不支持文生视频功能", True

        if final_input_image:
            await self.send_text("正在进行图片生成视频，请耐心等待几分钟...")
        else:
            await self.send_text("正在进行文字生成视频，请耐心等待几分钟...")

        # 检查是否启用调试信息
        enable_debug = self.get_config("components.enable_debug_info", False)
        # 显示开始信息
        if enable_debug:
            await self.send_text(f"正在使用模型 {model_id} 进行生成视频，请稍候...")

        try:
            api_client = ApiClient(self)
            success, result = await api_client.generate_video(
                prompt=description,
                model_config=model_config,
                input_image=final_input_image
            )

            if success:
                # 处理结果  URL
                try:
                    encoded_success, encoded_result = await asyncio.to_thread(
                        self._download_and_encode_base64, result
                    )
                    if encoded_success:
                        send_success, send_result = await self._send_video(encoded_result)
                        if send_success:
                            await self.send_text("视频生成完成！")
                            return True, "视频生成成功", True
                        else:
                            await self.send_text(f"视频已生成但发送失败了，失败原因：{send_result}")
                            return False, "视频发送失败", True
                    else:
                        await self.send_text(f"视频请求或转码失败：{encoded_result}")
                        return False, f"视频请求或转码失败: {encoded_result}", True
                except Exception as e:
                    logger.error(f"{self.log_prefix} 视频处理失败: {e!r}")
                    await self.send_text("视频处理失败")
                    return False, "视频处理失败", True
            else:
                await self.send_text(f"{result}")
                return False, f"视频生成失败: {result}", True

        except Exception as e:
            logger.error(f"{self.log_prefix} 视频生成命令执行异常: {e!r}", exc_info=True)
            await self.send_text(f"{e}")#执行失败，请重试或检查日志
            return False, "命令执行异常", True

    def _get_model_config(self, model_id: str) -> Optional[Dict[str, Any]]:
        """获取模型配置"""
        try:
            model_config = self.get_config(f"models.{model_id}")
            if model_config and isinstance(model_config, dict):
                return model_config
            else:
                logger.warning(f"{self.log_prefix} 模型 {model_id} 配置不存在或格式错误")
                return None
        except Exception as e:
            logger.error(f"{self.log_prefix} 获取模型配置失败: {e!r}")
            return None
        
    @staticmethod
    def get_video_size(command: str, model_config: Dict[str, Any]):
        """生成视频的比例"""
        api_format = model_config.get("format", "openai")
        model = model_config.get("model", "sora2")
        # openai格式
        if api_format == "openai":
            resolution = model_config.get("resolution", "720p")
            if command == "video":
                size = None
            elif command == "video-l":
                size =  "1792x1024" if resolution=="1080p" else "1280x720"
            elif command == "video-p":
                size = "1024x1792" if resolution=="1080p" else "720x1280"
            model_config["size"] = size
        # SiliconFlow格式
        if api_format == "siliconflow":
            if command == "video":
                size = None
            elif command == "video-l":
                size = "1280x720"
            elif command == "video-p":
                size = "720x1280"
            model_config["size"] = size
        # Doubao格式
        if api_format == "doubao":
            if command == "video":
                ratio = "adaptive"
            elif command == "video-l":
                ratio = "16:9"
            elif command == "video-p":
                ratio = "9:16"
            model_config["ratio"] = ratio
        # 向量引擎统一视频格式
        elif api_format == "vectorengine":
            if command == "video":
                aspect_ratio = None
                orientation = None
            elif command == "video-l":
                aspect_ratio = "16:9" if ("veo3" in model) else "3:2"
                orientation = "landscape" if ("sora-2" in model) else None
            elif command == "video-p":
                aspect_ratio = "9:16" if ("veo3" in model) else "2:3"
                orientation = "portrait" if ("sora-2" in model) else None
            if "veo" in model:
                model_config["resolution"] = None
            model_config["aspect_ratio"] = None if ("veo2" in model or "sora" in model) else aspect_ratio
            model_config["orientation"] = orientation

        return model_config
        
    def _download_and_encode_base64(self, video_url: str) -> Tuple[bool, str]:
        """请求视频并转码为 base64（带重试与代理容错）"""
        import base64
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        try:
            session = requests.Session()

            retry = Retry(
                total=5,                    # 总重试次数（强烈建议 3~5）
                connect=3,                  # 连接失败重试
                read=3,                     # 读取失败重试
                backoff_factor=1.5,         # 退避：1.5, 3, 6, 12...
                status_forcelist=[500, 502, 503, 504],
                allowed_methods=["GET", "HEAD"],
                raise_on_status=False       # 不因 5xx 直接抛异常
            )

            adapter = HTTPAdapter(
                max_retries=retry,
                pool_connections=10,
                pool_maxsize=10
            )

            session.mount("http://", adapter)
            session.mount("https://", adapter)

            request_kwargs = {
                "url": video_url,
                "timeout": (10, 150),  # (连接超时, 读取超时)
                "stream": False        # 一次性读取（base64 必须）
            }

            proxy_enabled = self.get_config("proxy.enabled", False)
            if proxy_enabled:
                proxy_url = self.get_config("proxy.url", "http://127.0.0.1:7890")
                request_kwargs["proxies"] = {
                    "http": proxy_url,
                    "https": proxy_url
                }
                logger.debug(f"{self.log_prefix} 下载视频使用代理: {proxy_url}")

            response = session.get(**request_kwargs)

            if response.status_code == 200 and response.content:
                video_base64 = base64.b64encode(response.content).decode("ascii")
                return True, f"base64://{video_base64}"

            logger.error(
                f"{self.log_prefix} 视频请求失败 "
                f"status={response.status_code} body={response.text[:200]}"
            )
            return False, f"HTTP {response.status_code}"

        except requests.exceptions.ConnectTimeout as e:
            logger.error(f"{self.log_prefix} 连接超时: {e}")
            return False, "连接超时"

        except requests.exceptions.ReadTimeout as e:
            logger.error(f"{self.log_prefix} 读取超时: {e}")
            return False, "读取超时"

        except requests.exceptions.ConnectionError as e:
            logger.error(f"{self.log_prefix} 网络连接失败: {e}")
            return False, "网络连接失败"

        except Exception as e:
            logger.exception(f"{self.log_prefix} 视频转码异常")
            return False, str(e)
    
    async def _send_video(self, encoded_video: str) -> Tuple[bool, str]:
        """发送视频消息"""

        # 视频发送端
        video_send_client = VideoSendCilent(self.get_config("napcat.HOST", "127.0.0.1"), self.get_config("napcat.PORT", 5700), self.message)

        # 发送视频，然后删除
        try:
            send_ok, send_result = await video_send_client.try_send(encoded_video)
            return send_ok, send_result
        
        except Exception as e:
            logger.error(f"{self.log_prefix} 视频发送失败: {e}")
            return False, str(e)

class VideoConfigCommand(BaseCommand):
    """视频生成配置管理命令"""

    # Command基本信息
    command_name = "video_config_command"
    command_description = "视频生成配置管理：/video <操作> [参数]"
    command_pattern = r"(?:.*，说：\s*)?/video\s+(?P<action>list|models|config|set|reset|help)(?:\s+(?P<params>.*))?$"

    def get_config(self, key: str, default=None):
        """使用与VideoGenerationCommand相同的配置覆盖"""
        # 检查VideoGenerationCommand的配置覆盖
        if key in VideoGenerationCommand._config_overrides:
            return VideoGenerationCommand._config_overrides[key]
        # 否则使用父类的get_config
        return super().get_config(key, default)

    async def execute(self) -> Tuple[bool, Optional[str], bool]:
        """执行配置管理命令"""
        logger.info(f"{self.log_prefix} 执行视频生成配置管理命令")

        # 获取匹配的参数
        action = self.matched_groups.get("action", "").strip()
        params = self.matched_groups.get("params", "") or ""
        params = params.strip()

        # 检查用户权限
        has_permission = self._check_permission()

        # 对于需要管理员权限的操作进行权限检查
        if not has_permission and action not in ["list", "models", "help"]:
            await self.send_text("你无权使用此命令", storage_message=False)
            return False, "没有权限", True

        if action == "list" or action == "models":
            return await self._list_models()
        elif action == "set":
            return await self._set_model(params)
        elif action == "config":
            return await self._show_current_config()
        elif action == "reset":
            return await self._reset_config()
        elif action == "help":
            return await self._show_help()
        else:
            await self.send_text(
                "配置管理命令使用方法：\n"
                "/video list - 列出所有可用模型\n"
                "/video config - 显示当前配置\n"
                "/video set <模型ID> - 设置图生图命令模型\n"
                "/video reset - 重置为默认配置\n"
                "/video help - 提供视频生成帮助"
            )
            return False, "无效的操作参数", True

    async def _list_models(self) -> Tuple[bool, Optional[str], bool]:
        """列出所有可用的模型"""
        try:
            models_config = self.get_config("models", {})
            if not models_config:
                await self.send_text("未找到任何模型配置")
                return False, "无模型配置", True

            # 获取当前模型
            current_command_model = self.get_config("components.command_model", "model1")

            message_lines = ["📋 可用模型列表：\n"]

            for model_id, config in models_config.items():
                if isinstance(config, dict):
                    model_name = config.get("name", "未知")
                    model = config.get("model", "未知")

                    # 标记当前使用的模型
                    default_mark = " ✅[当前使用]" if model_id == current_command_model else ""

                    message_lines.append(
                        f"• {model_id}{default_mark}\n"
                        f"  模型名称: {model_name}\n"
                        f"  模型: {model}\n"
                    )

            message = "\n".join(message_lines)
            await self.send_text(message)
            return True, "模型列表查询成功", True

        except Exception as e:
            logger.error(f"{self.log_prefix} 列出模型失败: {e!r}")
            await self.send_text(f"获取模型列表失败：{str(e)[:100]}")
            return False, f"列出模型失败: {str(e)}", True

    async def _set_model(self, model_id: str) -> Tuple[bool, Optional[str], bool]:
        """设置视频生成命令使用的模型"""
        try:
            if not model_id:
                await self.send_text("请指定模型ID，格式：/video set <模型ID>")
                return False, "缺少模型ID参数", True

            # 检查模型是否存在
            model_config = self.get_config(f"models.{model_id}")
            if not model_config:
                await self.send_text(f"模型 '{model_id}' 不存在，请使用 /video list 查看可用模型")
                return False, f"模型 '{model_id}' 不存在", True

            # 获取当前配置
            current_command_model = self.get_config("components.command_model", "model1")
            model = model_config.get("model", "未知") if isinstance(model_config, dict) else "未知"

            if current_command_model == model_id:
                await self.send_text(f"✅ 当前生成视频命令已经在使用模型 '{model_id}' ({model})")
                return True, "模型已是当前使用的模型", True

            # 尝试动态修改配置
            try:
                # 通过插件实例修改配置
                success = await self._update_command_model_config(model_id)

                if success:
                    await self.send_text(f"✅ 已切换到模型: {model_id}")
                    return True, f"模型切换成功: {model_id}", True
                else:
                    await self.send_text(f"⚠️ 切换失败，请手动修改配置文件")
                    return False, "动态配置更新失败", True

            except Exception as e:
                logger.error(f"{self.log_prefix} 动态更新配置失败: {e!r}")
                await self.send_text(f"⚠️ 配置更新失败：{str(e)[:50]}")
                return False, f"配置更新异常: {str(e)}", True

        except Exception as e:
            logger.error(f"{self.log_prefix} 设置模型失败: {e!r}")
            await self.send_text(f"设置失败：{str(e)[:100]}")
            return False, f"设置模型失败: {str(e)}", True

    async def _update_command_model_config(self, model_id: str) -> bool:
        """动态更新命令模型配置"""
        try:
            # 使用类级别的配置覆盖机制（这会影响所有VideoGenerationCommand实例）
            VideoGenerationCommand._config_overrides["components.command_model"] = model_id

            logger.info(f"{self.log_prefix} 已设置配置覆盖: components.command_model = {model_id}")
            return True

        except Exception as e:
            logger.error(f"{self.log_prefix} 更新配置时异常: {e!r}")
            return False

    async def _reset_config(self) -> Tuple[bool, Optional[str], bool]:
        """重置配置为默认值"""
        try:
            # 清除所有配置覆盖
            VideoGenerationCommand._config_overrides.clear()

            # 获取默认配置
            default_model = super().get_config("components.command_model", "model1")

            await self.send_text(
                f"✅ 配置已重置为默认值！\n\n"
                f"🔄 生成视频命令模型: {default_model}\n"
                f"💡 所有运行时配置覆盖已清除\n\n"
                f"使用 /video config 查看当前配置"
            )

            logger.info(f"{self.log_prefix} 配置已重置，清除了所有覆盖")
            return True, "配置重置成功", True

        except Exception as e:
            logger.error(f"{self.log_prefix} 重置配置失败: {e!r}")
            await self.send_text(f"重置失败：{str(e)[:100]}")
            return False, f"重置配置失败: {str(e)}", True

    async def _show_current_config(self) -> Tuple[bool, Optional[str], bool]:
        """显示当前配置信息"""
        try:
            # 获取当前配置
            command_model = self.get_config("components.command_model", "model1")

            # 检查是否有配置覆盖
            original_command_model = super().get_config("components.command_model", "model1")
            has_override = command_model != original_command_model

            # 获取默认模型详细信息
            command_config = self.get_config(f"models.{command_model}", {})

            # 构建配置信息
            message_lines = [
                "⚙️ 当前视频生成配置：\n",
                f"🔧 视频生成命令模型: {command_model}" + (" 🔥[运行时]" if has_override else ""),
                f"   • 名称: {command_config.get('name', '未知') if isinstance(command_config, dict) else '未知'}",
                f"   • 模型: {command_config.get('model', '未知') if isinstance(command_config, dict) else '未知'}",
            ]

            if has_override:
                message_lines.extend([
                    f"   • 原始配置: {original_command_model}",
                    f"   ⚡ 当前使用运行时覆盖配置"
                ])

            # 管理员命令提示
            message_lines.extend([
                "\n📖 管理员命令：",
                "• /video list - 查看所有模型",
                "• /vdieo set <模型ID> - 设置视频生成模型",
                "• /video reset - 重置为默认配置",
            ])

            message = "\n".join(message_lines)
            await self.send_text(message)
            return True, "配置信息查询成功", True

        except Exception as e:
            logger.error(f"{self.log_prefix} 显示配置失败: {e!r}")
            await self.send_text(f"获取配置失败：{str(e)[:100]}")
            return False, f"显示配置失败: {str(e)}", True
        
    async def _show_help(self) -> Tuple[bool, Optional[str], bool]:
        """显示帮助信息"""
        try:
            # 检查用户权限
            has_permission = self._check_permission()

            if has_permission:
                # 管理员帮助信息
                help_text = """
🎨 视频生成系统帮助

📋 基本命令：
• /video <描述> - 生成默认比例视频
• /video-l <描述> - 生成横屏比例视频
• /video-p <描述> - 生成竖屏比例视频
• /video list - 查看所有模型

⚙️ 管理员命令：
• /video config - 查看当前配置
• /video set <模型ID> - 设置图生图模型
• /video reset - 重置为默认配置

💡 使用流程：
1. 使用 /video <描述> 进行视频生成，可引用图片使用图片生成视频功能
2. 等待处理完成
                """
            else:
                # 普通用户帮助信息
                help_text = """
🎨 视频生成系统帮助

📋 可用命令：
• /video <描述> - 生成默认比例视频
• /video-l <描述> - 生成横屏比例视频
• /video-p <描述> - 生成竖屏比例视频
• /video list - 查看所有模型

💡 使用流程：
1. 使用 /video <描述> 进行视频生成，可引用图片使用图片生成视频功能
2. 等待处理完成
                """

            await self.send_text(help_text.strip())
            return True, "帮助信息显示成功", True

        except Exception as e:
            logger.error(f"{self.log_prefix} 显示帮助失败: {e!r}")
            await self.send_text(f"显示帮助信息失败：{str(e)[:100]}")
            return False, f"显示帮助失败: {str(e)}", True

    def _check_permission(self) -> bool:
        """检查用户权限"""
        try:
            admin_users = self.get_config("components.admin_users", [])
            user_id = str(self.message.message_info.user_info.user_id) if self.message and self.message.message_info and self.message.message_info.user_info else None
            return user_id in admin_users
        except Exception:
            return False
        