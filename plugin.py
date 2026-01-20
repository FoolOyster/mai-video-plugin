from typing import List, Tuple, Type

from src.plugin_system import ConfigField
from src.plugin_system import BasePlugin, register_plugin, ComponentInfo

from .core.video_command import VideoGenerationCommand, VideoConfigCommand

@register_plugin # 注册插件
class VideoPlugin(BasePlugin):
    """视频生成插件"""

    # 插件基本信息
    plugin_name = "mai_video_plugin"
    plugin_version = "0.1.0"  # 插件版本号
    plugin_author = "FoolOyster"  # 插件作者
    enable_plugin = True  # 启用插件
    dependencies = []  # 插件依赖列表（目前为空）
    python_dependencies = []  # Python依赖列表（目前为空）
    config_file_name = "config.toml"  # 配置文件名

    # 配置节描述
    config_section_descriptions = {
        "plugin": "插件启用配置",
        "components": "组件启用配置",
        "napcat": "Napcat HTTP服务器（正向），用于发送视频",
        "logging": "日志配置",
        "image_uploader": "对象存储(COS / OSS / R2)，将消息中的图片上传到对象储存提供访问图片链接",
        "models": "多模型配置，每个模型都有独立的参数设置"
    }

    # 使用ConfigField定义详细的配置Schema
    config_schema: dict = {
        "plugin": {
            "name": ConfigField(type=str, default="mai_video_plugin", description="基于 Sora API （不定期更新其他API接口）的视频生成插件，支持文生视频与图生视频", required=True),
            "config_version": ConfigField(type=str, default="1.0.0", description="插件配置版本号"),
            "enabled": ConfigField(type=bool, default=False, description="是否启用插件，开启后可使用视频功能")
        },
        "components": {
            "enable_debug_info": ConfigField(type=bool, default=False, description="是否启用调试信息显示，关闭后仅显示图片结果和错误信息"),
            "command_model": ConfigField(type=str, default="model1", description="Command组件使用的模型ID"),
            "max_requests": ConfigField(type=int, default=3, description="同一时间最多可有的视频任务数量（务必是正整数），数量越大内存要求越高"),
            "admin_users": ConfigField(
                type=list,
                default=[],
                description="有权限使用配置管理命令的管理员用户列表，请填写字符串形式的用户ID"
            )
        },
        "napcat": {
            "HOST": ConfigField(type=str, default="127.0.0.1", description="Napcat HTTP服务器（正向）地址"),
            "PORT": ConfigField(type=int, default=5700, description="Napcat HTTP服务器（正向）端口")
        },
        "logging": {
            "level": ConfigField(type=str, default="INFO", description="日志记录级别，DEBUG显示详细信息", choices=["DEBUG", "INFO", "WARNING", "ERROR"]),
            "prefix": ConfigField(type=str, default="[unified_video_Plugin]", description="日志前缀标识")
        },
        "proxy": {
            "enabled": ConfigField(type=bool, default=False, description="是否启用代理。开启后所有API请求将通过代理服务器"),
            "url": ConfigField(type=str, default="http://127.0.0.1:7890", description="代理服务器地址，格式：http://host:port。支持HTTP/HTTPS/SOCKS5代理"),
            "timeout": ConfigField(type=int, default=60, description="代理连接超时时间（秒），建议30-120秒")
        },
        "image_uploader": {
            "enabled": ConfigField(type=bool, default=False, description="是否启用对象储存服务。关闭后使用图生视频功能时向模型提供base64格式图片而不是图片url链接。"),
            "provider": ConfigField(type=str, default="cos", description="对象储存服务提供商，cos：腾讯云，oss：阿里云，r2：Cloudflare R2",choices=["cos", "oss", "r2"]),
            "access_key_id": ConfigField(type=str, default="access_key_id", description="存储桶权限用户access_key_id"),
            "secret_access_key": ConfigField(type=str, default="secret_access_key", description="存储桶权限用户secret_access_key"),
            "region": ConfigField(type=str, default="region", description="存储桶所属地域"),
            "bucket_name": ConfigField(type=str, default="bucket_name", description="存储桶名称"),
            "endpoint": ConfigField(type=str, default="endpoint", description="存储桶Endpoint（地域节点），腾讯云可以为空"),
        },
        "models": {},
        # 基础模型配置
        "models.model1": {
            "name": ConfigField(type=str, default="OpenAI-Sora2模型", description="模型显示名称，在模型列表中展示"),
            "base_url": ConfigField(
                type=str,
                default="https://api.openai.com/v1",
                description="API服务地址。示例: OpenAI=https://api.openai.com/v1, 硅基=https://api.siliconflow.cn/v1, 豆包=https://ark.cn-beijing.volces.com/api/v3, 向量引擎=https://api.vectorengine.ai/v1 (必需)",
                required=True
            ),
            "api_key": ConfigField(
                type=str,
                default="xxxxxxxxxxxxxxxxxxxxxx",
                description="API密钥",
                required=True
            ),
            "format": ConfigField(
                type=str,
                default="openai",
                description="API格式。openai=通用格式（sora2，veo），siliconflow=硅基格式，doubao=豆包格式，vectorengine=向量引擎统一视频格式",
                choices=["openai", "siliconflow", "doubao", "vectorengine"]
            ),
            "model": ConfigField(
                type=str,
                default="sora-2",
                description="模型名称"
            ),
            "support_option": ConfigField(
                type=str,
                default="3",
                description="模型支持的功能，1：仅支持文生视频，2：仅支持图生视频，3：同时支持文生视频和图生视频",
                choices=["1", "2", "3"]
            ),
            "seconds": ConfigField(
                type=str,
                default="10",
                description="视频生成时长，默认为10秒，视频支持时长因模型而异（8 / 10 / 15），有些模型时长限定"
            ),
            "resolution": ConfigField(
                type=str,
                default="720p",
                description="视频分辨率，有些模型分辨率固定",
                choices=["480p","720p", "1080p"]
            ),
            "watermark": ConfigField(
                type=bool,
                default=False,
                description="是否添加AI水印，有些视频模型默认没水印"
            ),
        }
    }

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]: # 获取插件组件
        """返回插件包含的组件列表"""
        components = []  # 先设置个列表，提升扩张性
        components.append((VideoConfigCommand.get_command_info(), VideoConfigCommand))
        components.append((VideoGenerationCommand.get_command_info(), VideoGenerationCommand))

        return components
