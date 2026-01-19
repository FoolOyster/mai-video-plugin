import base64
import urllib.request
import traceback
from typing import Optional, Tuple, Any, List

from src.common.logger import get_logger
from maim_message import Seg

logger = get_logger("video_image_utils")

class ImageProcessor:
    """图片处理工具类 + 内置图床服务"""

    # 图片格式检测模式
    _image_format_patterns = {
        'jpeg': ['/9j/', '\xff\xd8\xff'],
        'png': ['iVBORw', '\x89PNG'],
        'webp': ['UklGR', 'RIFF'],
        'gif': ['R0lGOD', 'GIF8']
    }

    def __init__(self, action_instance):
        self.action = action_instance
        self.log_prefix = action_instance.log_prefix

        # 使用实例级别的失败缓存，避免跨实例状态共享问题
        self._failed_picids_cache = {}
        self._max_failed_cache_size = 500

    def _is_picid_failed(self, picid: str) -> bool:
        """检查picid是否在失败缓存中"""
        return picid in self._failed_picids_cache

    def _mark_picid_failed(self, picid: str):
        """将picid标记为失败，使用LRU缓存机制"""
        import time
        self._failed_picids_cache[picid] = time.time()

        # LRU清理机制
        if len(self._failed_picids_cache) > self._max_failed_cache_size:
            # 按时间排序，移除最旧的条目
            sorted_items = sorted(self._failed_picids_cache.items(), key=lambda x: x[1])
            items_to_remove = len(sorted_items) - self._max_failed_cache_size // 2
            for i in range(items_to_remove):
                del self._failed_picids_cache[sorted_items[i][0]]

    def _is_command_component(self) -> bool:
        """判断是否为Command组件"""
        return hasattr(self.action, 'message')

    async def get_recent_image(self) -> Optional[str]:
        """获取最近的图片消息，支持多种组件类型"""
        try:
            logger.debug(f"{self.log_prefix} 开始获取图片消息")

            # 方法1：从当前消息的message_segment中检索（最优先）
            message_segments = None

            # Command组件
            if hasattr(self.action, 'message') and hasattr(self.action.message, 'message_segment'):
                # Command组件
                message_segments = self.action.message.message_segment

            if message_segments:
                # 使用emoji插件的检索功能
                emoji_base64_list = self.find_and_return_emoji_in_message(message_segments)
                if emoji_base64_list:
                    logger.info(f"{self.log_prefix} 在当前消息中找到 {len(emoji_base64_list)} 张图片")
                    return emoji_base64_list[0]  # 返回第一张图片

            # 方法2：从历史消息中查找（作为后备）
            try:
                from src.plugin_system.apis import message_api

                # 获取chat_id
                chat_id = self._get_chat_id()
                if chat_id:
                    # 获取最近的消息
                    recent_messages = message_api.get_recent_messages(chat_id, hours=1.0, limit=15, filter_mai=True)
                    logger.debug(f"{self.log_prefix} 从历史消息获取到 {len(recent_messages)} 条消息")

                    for msg in reversed(recent_messages):
                        # 检查消息是否包含图片标记
                        is_picid = False
                        if isinstance(msg, dict):
                            is_picid = msg.get('is_picid', False)
                        else:
                            is_picid = getattr(msg, 'is_picid', False)

                        if is_picid:
                            # 尝试从消息段中提取
                            if hasattr(msg, 'message_segment') and msg.message_segment:
                                emoji_base64_list = self.find_and_return_emoji_in_message(msg.message_segment)
                                if emoji_base64_list:
                                    logger.info(f"{self.log_prefix} 从历史消息中找到图片")
                                    return emoji_base64_list[0]

            except Exception as e:
                logger.debug(f"{self.log_prefix} 从历史消息获取图片失败: {e}")

            logger.warning(f"{self.log_prefix} 未找到可用的图片消息")
            return None

        except Exception as e:
            logger.error(f"{self.log_prefix} 获取图片失败: {e!r}", exc_info=True)
            return None

    def _get_action_message(self) -> Optional[Any]:
        """获取action_message对象，Command"""
        if hasattr(self.action, 'message') and hasattr(self.action.message, 'message_recv'):
            # Command组件，使用message.message_recv作为action_message
            return self.action.message.message_recv
        return None

    def _get_chat_stream(self) -> Optional[Any]:
        """获取chat_stream对象，Command"""
        if hasattr(self.action, 'message') and hasattr(self.action.message, 'chat_stream'):
            # Command组件
            return self.action.message.chat_stream
        return None

    def _get_chat_id(self) -> Optional[str]:
        """获取chat_id，Command"""
        chat_stream = self._get_chat_stream()
        if chat_stream and hasattr(chat_stream, 'stream_id'):
            return chat_stream.stream_id
        return None

    def _process_image_data(self, data) -> Optional[str]:
        """处理图片数据，统一转换为base64格式"""
        try:
            if not data:
                return None

            # 如果是字符串类型
            if isinstance(data, str):
                # 检查是否是有效的base64图片数据
                if self._is_image_data(data):
                    return data
                # 如果不是，可能需要其他处理
                return None

            # 如果是字典类型，尝试提取内部数据
            if isinstance(data, dict):
                for key in ['data', 'base64', 'content', 'image']:
                    if key in data and data[key]:
                        result = self._process_image_data(data[key])
                        if result:
                            return result

            # 如果是字节类型，转换为base64
            if isinstance(data, bytes):
                try:
                    return base64.b64encode(data).decode('utf-8')
                except Exception as e:
                    logger.debug(f"{self.log_prefix} 字节数据转base64失败: {e}")
                    return None

            return None

        except Exception as e:
            logger.debug(f"{self.log_prefix} 处理图片数据失败: {str(e)[:50]}")
            return None

    def _is_image_data(self, data: str) -> bool:
        """检查字符串是否是有效的base64图片数据"""
        try:
            if not isinstance(data, str) or len(data) < 100:
                return False

            # 检查是否包含base64图片前缀
            if any(prefix in data[:50] for prefix in ['data:image/', '/9j/', 'iVBOR', 'UklGR', 'R0lGO']):
                return True

            # 检查base64格式特征
            if len(data) % 4 == 0 and all(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=' for c in data[:100]):
                # 尝试解码前几个字符看是否是图片格式
                try:
                    decoded_start = base64.b64decode(data[:100])
                    for format_name, patterns in self._image_format_patterns.items():
                        for pattern in patterns:
                            if isinstance(pattern, str) and decoded_start.startswith(pattern.encode()):
                                return True
                            elif isinstance(pattern, bytes) and decoded_start.startswith(pattern):
                                return True
                except Exception:
                    pass

            return False

        except Exception:
            return False

    def validate_image_size(self, image_size: str) -> bool:
        """验证图片尺寸格式"""
        try:
            width, height = map(int, image_size.split("x"))
            return 100 <= width <= 10000 and 100 <= height <= 10000
        except (ValueError, TypeError):
            return False

    def download_and_encode_base64(self, image_url: str) -> Tuple[bool, str]:
        """下载图片或处理Base64数据URL"""
        logger.info(f"{self.log_prefix} (B64) 处理图片: {image_url[:50]}...")
        
        try:
            # 检查是否为Base64数据URL
            if image_url.startswith('data:image/'):
                logger.info(f"{self.log_prefix} (B64) 检测到Base64数据URL")
                
                # 从数据URL中提取Base64部分
                if ';base64,' in image_url:
                    base64_data = image_url.split(';base64,', 1)[1]
                    logger.info(f"{self.log_prefix} (B64) 从数据URL提取Base64完成. 长度: {len(base64_data)}")
                    return True, base64_data
                else:
                    error_msg = "Base64数据URL格式不正确"
                    logger.error(f"{self.log_prefix} (B64) {error_msg}")
                    return False, error_msg
            else:
                # 处理普通HTTP URL
                logger.info(f"{self.log_prefix} (B64) 下载HTTP图片")
                with urllib.request.urlopen(image_url, timeout=600) as response:
                    if response.status == 200:
                        image_bytes = response.read()
                        base64_encoded_image = base64.b64encode(image_bytes).decode("utf-8")
                        logger.info(f"{self.log_prefix} (B64) 图片下载编码完成. Base64长度: {len(base64_encoded_image)}")
                        return True, base64_encoded_image
                    else:
                        error_msg = f"下载图片失败 (状态: {response.status})"
                        logger.error(f"{self.log_prefix} (B64) {error_msg} URL: {image_url[:30]}...")
                        return False, error_msg
                        
        except Exception as e:
            logger.error(f"{self.log_prefix} (B64) 处理图片时错误: {e!r}", exc_info=True)
            traceback.print_exc()
            return False, f"处理图片时发生错误: {str(e)[:50]}"

    def process_api_response(self, result) -> Optional[str]:
        """统一处理API响应，提取图片数据"""
        try:
            # 如果result是字符串，直接返回
            if isinstance(result, str):
                return result

            # 如果result是字典，尝试提取图片数据
            if isinstance(result, dict):
                # 尝试多种可能的字段
                for key in ['url', 'image', 'b64_json', 'data']:
                    if key in result and result[key]:
                        return result[key]

                # 检查嵌套结构
                if 'output' in result and isinstance(result['output'], dict):
                    output = result['output']
                    for key in ['image_url', 'images']:
                        if key in output:
                            data = output[key]
                            return data[0] if isinstance(data, list) and data else data

            return None
        except Exception as e:
            logger.error(f"{self.log_prefix} 处理API响应失败: {str(e)[:50]}")
            return None

    async def _get_message_by_id(self, message_id: str) -> Optional[dict]:
        """通过消息ID直接查询消息"""
        try:
            # 尝试使用数据库直接查询
            from src.common.database.database_model import Messages

            try:
                # 查询消息记录
                message_record = Messages.select().where(Messages.id == message_id).first()
                if message_record:
                    logger.info(f"{self.log_prefix} 通过数据库查询到消息: {message_id}")
                    # 将消息记录转换为字典格式
                    message_dict = {
                        'id': message_record.id,
                        'message_id': message_record.id,
                        'is_picid': getattr(message_record, 'is_picid', False),
                        'processed_plain_text': getattr(message_record, 'processed_plain_text', ''),
                        'display_message': getattr(message_record, 'display_message', ''),
                        'additional_config': getattr(message_record, 'additional_config', ''),
                        'raw_message': getattr(message_record, 'raw_message', ''),
                    }
                    return message_dict
            except Exception as e:
                logger.debug(f"{self.log_prefix} 数据库查询消息失败: {e}")

            # 如果数据库查询失败，尝试其他方式
            logger.debug(f"{self.log_prefix} 无法通过ID直接查询消息: {message_id}")
            return None

        except Exception as e:
            logger.debug(f"{self.log_prefix} 查询消息ID {message_id} 失败: {e}")
            return None

    def find_and_return_emoji_in_message(self, message_segments) -> List[str]:
        """从消息中查找并返回表情包/图片的base64数据列表 (来自emoji_manage插件)"""
        emoji_base64_list = []

        # 处理单个Seg对象的情况
        if isinstance(message_segments, Seg):
            if message_segments.type == "emoji":
                emoji_base64_list.append(message_segments.data)
            elif message_segments.type == "image":
                # 假设图片数据是base64编码的
                emoji_base64_list.append(message_segments.data)
            elif message_segments.type == "seglist":
                # 递归处理嵌套的Seg列表
                emoji_base64_list.extend(self.find_and_return_emoji_in_message(message_segments.data))
            return emoji_base64_list

        # 处理Seg列表的情况
        for seg in message_segments:
            if seg.type == "emoji":
                emoji_base64_list.append(seg.data)
            elif seg.type == "image":
                # 假设图片数据是base64编码的
                emoji_base64_list.append(seg.data)
            elif seg.type == "seglist":
                # 递归处理嵌套的Seg列表
                emoji_base64_list.extend(self.find_and_return_emoji_in_message(seg.data))
        return emoji_base64_list

    async def _extract_image_from_message(self, message) -> Optional[str]:
        """从消息中提取图片数据"""
        try:
            if not message:
                return None

            # 如果消息有message_segment，直接从中提取
            message_segment = None
            if isinstance(message, dict):
                message_segment = message.get('message_segment')
            else:
                message_segment = getattr(message, 'message_segment', None)

            if message_segment:
                emoji_base64_list = self.find_and_return_emoji_in_message(message_segment)
                if emoji_base64_list:
                    return emoji_base64_list[0]

            return None

        except Exception as e:
            logger.debug(f"{self.log_prefix} 从消息提取图片失败: {e}")
            return None

    async def _extract_base64_from_text(self, text: str) -> Optional[str]:
        """从文本中提取base64图片数据"""
        try:
            if not text:
                return None

            # 尝试匹配base64数据模式
            import re

            # 匹配data:image/格式的base64
            data_url_pattern = r'data:image/[^;]+;base64,([A-Za-z0-9+/=]+)'
            match = re.search(data_url_pattern, text)
            if match:
                return match.group(1)

            # 匹配纯base64数据（长度较长的情况）
            base64_pattern = r'([A-Za-z0-9+/]{100,}={0,2})'
            matches = re.findall(base64_pattern, text)
            for match in matches:
                if self._is_image_data(match):
                    return match

            return None

        except Exception as e:
            logger.debug(f"{self.log_prefix} 从文本提取base64失败: {e}")
            return None
