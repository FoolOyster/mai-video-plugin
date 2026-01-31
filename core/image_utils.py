import asyncio
import base64
from typing import Optional, Tuple, Any, List

import aiohttp
from maim_message import Seg

from src.common.logger import get_logger

logger = get_logger("video_image_utils")


class ImageProcessor:
    """图片相关工具：从消息中获取base64格式图片"""

    def __init__(self, action_instance):
        self.action = action_instance
        self.log_prefix = action_instance.log_prefix

    async def get_recent_image(self) -> Optional[str]:
        try:
            message_segments = None
            if hasattr(self.action, "message") and hasattr(self.action.message, "message_segment"):
                message_segments = self.action.message.message_segment

            if message_segments:
                emoji_base64_list = self.find_and_return_emoji_in_message(message_segments)
                if emoji_base64_list:
                    return emoji_base64_list[0]

            try:
                from src.plugin_system.apis import message_api

                chat_id = self._get_chat_id()
                if chat_id:
                    recent_messages = message_api.get_recent_messages(
                        chat_id, hours=1.0, limit=15, filter_mai=True
                    )
                    for msg in reversed(recent_messages):
                        is_picid = False
                        if isinstance(msg, dict):
                            is_picid = msg.get("is_picid", False)
                        else:
                            is_picid = getattr(msg, "is_picid", False)

                        if is_picid and hasattr(msg, "message_segment") and msg.message_segment:
                            emoji_base64_list = self.find_and_return_emoji_in_message(msg.message_segment)
                            if emoji_base64_list:
                                return emoji_base64_list[0]
            except Exception as e:
                logger.debug(f"{self.log_prefix} 历史消息扫描失败: {e}")

            return None
        except Exception as e:
            logger.error(f"{self.log_prefix} 获取图片失败: {e!r}", exc_info=True)
            return None

    def _get_chat_id(self) -> Optional[str]:
        if hasattr(self.action, "message") and hasattr(self.action.message, "chat_stream"):
            chat_stream = self.action.message.chat_stream
            if hasattr(chat_stream, "stream_id"):
                return chat_stream.stream_id
        return None

    def find_and_return_emoji_in_message(self, message_segments) -> List[str]:
        emoji_base64_list: List[str] = []

        if isinstance(message_segments, Seg):
            if message_segments.type in ("emoji", "image"):
                emoji_base64_list.append(message_segments.data)
            elif message_segments.type == "seglist":
                emoji_base64_list.extend(
                    self.find_and_return_emoji_in_message(message_segments.data)
                )
            return emoji_base64_list

        for seg in message_segments:
            if seg.type in ("emoji", "image"):
                emoji_base64_list.append(seg.data)
            elif seg.type == "seglist":
                emoji_base64_list.extend(self.find_and_return_emoji_in_message(seg.data))
        return emoji_base64_list
