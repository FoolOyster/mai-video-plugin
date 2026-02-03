from typing import List, Tuple, Type

from src.plugin_system import ConfigField
from src.plugin_system import BasePlugin, register_plugin, ComponentInfo

from .core.video_command import VideoGenerationCommand, VideoConfigCommand


@register_plugin
class VideoPlugin(BasePlugin):
    """视频生成插件。"""

    plugin_name = "mai_video_plugin"
    plugin_version = "0.2.2"
    plugin_author = "FoolOyster"
    enable_plugin = True
    dependencies = []
    python_dependencies = []
    config_file_name = "config.toml"

    config_section_descriptions = {
        "plugin": "插件启用配置",
        "components": "命令行为与限制",
        "logging": "日志配置",
        "proxy": "HTTP 代理配置",
        "api": "API 请求与轮询配置",
        "video": "视频生成限制",
        "circuit_breaker": "熔断器配置",
        "image_uploader": "对象存储（图片临时上传）",
        "video_watch": "麦麦看视频，让麦麦知道自己生成了什么视频（测试功能）",
        "models": "视频生成模型配置",
    }

    config_schema: dict = {
        "plugin": {
            "name": ConfigField(
                type=str,
                default="mai_video_plugin",
                description="视频生成插件",
                required=True,
            ),
            "config_version": ConfigField(
                type=str, default="0.2.0", description="配置版本"
            ),
            "enabled": ConfigField(
                type=bool, default=False, description="是否启用插件"
            ),
        },
        "components": {
            "enable_debug_info": ConfigField(
                type=bool, default=False, description="是否向用户显示调试信息"
            ),
            "command_model": ConfigField(
                type=str, default="model1", description="/video 使用的默认模型ID"
            ),
            "max_requests": ConfigField(
                type=int,
                default=3,
                description="全局最大视频生成任务数",
            ),
            "max_requests_per_user": ConfigField(
                type=int,
                default=1,
                description="单用户最大视频生成任务数",
            ),
            "rate_limit_window_seconds": ConfigField(
                type=int,
                default=120,
                description="单用户请求间隔限制（秒）",
            ),
            "max_requests_per_window": ConfigField(
                type=int,
                default=3,
                description="单用户限制时间内最大请求数限制",
            ),
            "admin_users": ConfigField(
                type=list,
                default=[],
                description="管理员用户ID列表（字符串）",
            ),
        },
        "logging": {
            "level": ConfigField(
                type=str,
                default="INFO",
                description="日志级别",
                choices=["DEBUG", "INFO", "WARNING", "ERROR"],
            ),
            "prefix": ConfigField(
                type=str, default="[mai_video_plugin]", description="日志前缀"
            ),
        },
        "proxy": {
            "enabled": ConfigField(
                type=bool, default=False, description="是否启用代理"
            ),
            "url": ConfigField(
                type=str,
                default="http://127.0.0.1:7890",
                description="代理地址（http/https/socks5）",
            ),
            "timeout": ConfigField(
                type=int, default=60, description="代理超时（秒）"
            ),
        },
        "api": {
            "request_timeout_seconds": ConfigField(
                type=int, default=120, description="API 请求超时"
            ),
            "submit_max_retries": ConfigField(
                type=int, default=2, description="提交请求最大重试次数"
            ),
            "submit_backoff_seconds": ConfigField(
                type=int, default=2, description="退避基准秒数"
            ),
            "poll_interval_seconds": ConfigField(
                type=int, default=5, description="轮询间隔"
            ),
            "poll_timeout_seconds": ConfigField(
                type=int, default=900, description="轮询超时上限"
            ),
            "poll_max_attempts": ConfigField(
                type=int, default=0, description="轮询最大次数（0=不限）"
            ),
        },
        "video": {
            "max_prompt_length": ConfigField(
                type=int, default=800, description="描述最大长度"
            ),
            "max_image_mb": ConfigField(
                type=int, default=8, description="输入图片最大大小（MB）"
            ),
            "max_video_mb_for_base64": ConfigField(
                type=int,
                default=24,
                description="base64 编码视频最大大小（MB）",
            ),
            "allow_url_send": ConfigField(
                type=bool,
                default=True,
                description="允许 视频URL 直发，关闭时自动改成base64格式发送（发送内存占用较大）",
            ),
            "url_send_fallback_to_download": ConfigField(
                type=bool,
                default=True,
                description="URL 直发失败时回退到下载base64然后发送（allow_url_send开启下有效）",
            ),
        },
        "circuit_breaker": {
            "enabled": ConfigField(
                type=bool, default=True, description="是否启用熔断"
            ),
            "failure_threshold": ConfigField(
                type=int, default=5, description="触发熔断的失败次数"
            ),
            "recovery_seconds": ConfigField(
                type=int, default=120, description="熔断恢复时间（秒）"
            ),
            "half_open_max_success": ConfigField(
                type=int, default=2, description="半开状态成功次数"
            ),
        },
        "image_uploader": {
            "enabled": ConfigField(
                type=bool,
                default=False,
                description="是否启用对象存储临时上传图片",
            ),
            "provider": ConfigField(
                type=str,
                default="cos",
                description="对象存储服务商",
                choices=["cos", "oss", "r2"],
            ),
            "access_key_id": ConfigField(
                type=str, default="access_key_id", description="Access Key ID"
            ),
            "secret_access_key": ConfigField(
                type=str, default="secret_access_key", description="Secret Access Key"
            ),
            "region": ConfigField(type=str, default="region", description="区域"),
            "bucket_name": ConfigField(
                type=str, default="bucket_name", description="桶名称"
            ),
            "endpoint": ConfigField(
                type=str, default="endpoint", description="Endpoint（可选）"
            ),
        },
        "video_watch": {
            "enabled": ConfigField(
                type=bool,
                default=False,
                description="是否启用麦麦看视频，关闭则麦麦不知道自己生成的视频内容",
            ),
            "visual_style": ConfigField(
                type=str,
                default="请用中文描述这个视频的内容。请留意其主题，直观感受，输出为一段平文本，最多50字",
                description="麦麦识别视频规则",
            ),
            "model_identifier": ConfigField(
                type=str, default="gemini-3-flash-preview", description="看视频模型（请选择能识别视频的模型，目前只支持gemini的模型）",
            ),
            "client_type": ConfigField(
                type=str, 
                default="gemini", 
                description="客户端类型",
                choices=["gemini"],
            ),
            "base_url": ConfigField(
                type=str, default="https://generativelanguage.googleapis.com/v1beta", description="基础URL"
            ),
            "api_key": ConfigField(
                type=str, default="sk-...", description="API Key"
            ),
        },
        "models": {},
        "models.model1": {
            "name": ConfigField(
                type=str, default="OpenAI Sora", description="模型显示名称"
            ),
            "base_url": ConfigField(
                type=str,
                default="https://api.openai.com/v1",
                description="API 基础地址",
                required=True,
            ),
            "api_key": ConfigField(
                type=str,
                default="xxxxxxxxxxxxxxxxxxxxxx",
                description="API 密钥",
                required=True,
            ),
            "format": ConfigField(
                type=str,
                default="openai",
                description="API 格式",
                choices=["openai", "siliconflow", "doubao", "vectorengine"],
            ),
            "model": ConfigField(type=str, default="sora-2", description="模型名称"),
            "support_option": ConfigField(
                type=str,
                default="3",
                description="1=仅文生，2=仅图生，3=文生+图生",
                choices=["1", "2", "3"],
            ),
            "seconds": ConfigField(
                type=str, default="10", description="视频时长（秒）"
            ),
            "resolution": ConfigField(
                type=str,
                default="720p",
                description="分辨率",
                choices=["480p", "720p", "1080p"],
            ),
            "watermark": ConfigField(
                type=bool, default=False, description="是否添加水印"
            ),
        },
    }

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        components = []
        components.append((VideoConfigCommand.get_command_info(), VideoConfigCommand))
        components.append((VideoGenerationCommand.get_command_info(), VideoGenerationCommand))
        return components
