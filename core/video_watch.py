import asyncio
import base64
import re
from typing import Optional, Tuple, Dict, Any

import aiohttp

from src.common.logger import get_logger

logger = get_logger("video_watch")


class VideoWatcher:
    """麦麦看视频：调用多模态模型生成视频描述"""

    def __init__(self, action_instance):
        self.action = action_instance
        self.log_prefix = action_instance.log_prefix

    async def describe_video(self, video_ref: str) -> Tuple[bool, str]:
        """返回 (成功, 描述/原因)"""
        if not video_ref:
            return False, "视频为空"

        try:
            client_type = self.action.get_config("video_watch.client_type", "gemini")
            if client_type != "gemini":
                return False, "当前仅支持 Gemini 作为看视频模型"

            video_base64, mime_type = await self._prepare_video_payload(video_ref)
            if not video_base64:
                return False, "视频解析失败"

            return await self._describe_with_gemini(video_base64, mime_type)
        except Exception as e:
            logger.error(f"{self.log_prefix} 麦麦看视频异常: {e!r}", exc_info=True)
            return False, "麦麦看视频异常"

    async def describe_video_base64(self, video_base64: str, mime_type: str = "video/mp4") -> Tuple[bool, str]:
        """当已拿到 base64 时，直接生成描述，避免重复下载"""
        if not video_base64:
            return False, "视频为空"

        client_type = self.action.get_config("video_watch.client_type", "gemini")
        if client_type != "gemini":
            return False, "当前仅支持 Gemini 作为看视频模型"

        if isinstance(video_base64, str) and video_base64.startswith("base64://"):
            video_base64 = video_base64[len("base64://") :]

        return await self._describe_with_gemini(video_base64, mime_type or "video/mp4")

    async def _prepare_video_payload(self, video_ref: str) -> Tuple[Optional[str], str]:
        """准备视频数据，优先转为 base64"""
        if self._is_url(video_ref):
            return await self._download_video_as_base64(video_ref)

        if isinstance(video_ref, str) and video_ref.startswith("base64://"):
            return video_ref[len("base64://") :], "video/mp4"

        if isinstance(video_ref, str) and video_ref.startswith("data:"):
            # data:video/mp4;base64,xxxxx
            try:
                header, data = video_ref.split(",", 1)
                mime_part = header.split(";", 1)[0]
                mime_type = mime_part.replace("data:", "") if mime_part else "video/mp4"
                return data, mime_type or "video/mp4"
            except Exception:
                return None, "video/mp4"

        # 兜底：认为已经是 base64
        return video_ref, "video/mp4"

    async def _download_video_as_base64(self, video_url: str) -> Tuple[Optional[str], str]:
        max_mb = int(self.action.get_config("video_watch.max_video_mb", 20))
        if max_mb <= 0:
            max_mb = int(self.action.get_config("video.max_video_mb_for_base64", 20))
        max_bytes = max_mb * 1024 * 1024

        proxy_enabled = self.action.get_config("proxy.enabled", False)
        proxy_url = self.action.get_config("proxy.url", "http://127.0.0.1:7890")
        proxy = proxy_url if proxy_enabled else None

        timeout = aiohttp.ClientTimeout(total=180)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(video_url, proxy=proxy) as resp:
                    if resp.status != 200:
                        logger.warning(f"{self.log_prefix} 下载视频失败: HTTP {resp.status}")
                        return None, "video/mp4"

                    content_length = resp.headers.get("Content-Length")
                    if content_length and int(content_length) > max_bytes:
                        logger.warning(f"{self.log_prefix} 视频过大，超过 {max_mb}MB")
                        return None, "video/mp4"

                    data = await resp.read()
                    if len(data) > max_bytes:
                        logger.warning(f"{self.log_prefix} 视频过大，超过 {max_mb}MB")
                        return None, "video/mp4"

                    mime_type = resp.headers.get("Content-Type", "video/mp4")
                    if not mime_type.startswith("video/"):
                        mime_type = "video/mp4"

                    video_base64 = base64.b64encode(data).decode("ascii")
                    return video_base64, mime_type
        except asyncio.TimeoutError:
            logger.warning(f"{self.log_prefix} 下载视频超时")
            return None, "video/mp4"
        except Exception as e:
            logger.error(f"{self.log_prefix} 下载视频异常: {e!r}")
            return None, "video/mp4"

    async def _describe_with_gemini(self, video_base64: str, mime_type: str) -> Tuple[bool, str]:
        base_url = self.action.get_config(
            "video_watch.base_url", "https://generativelanguage.googleapis.com/v1beta"
        ).rstrip("/")
        api_key = self.action.get_config("video_watch.api_key", "")
        model = self.action.get_config("video_watch.model_identifier", "gemini-3-flash-preview")
        prompt = self.action.get_config("video_watch.visual_style", "请直接用中文描述这个视频，最多30字。")

        if not api_key:
            return False, "缺少视频识别 API Key"

        url = f"{base_url}/models/{model}:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}
        payload: Dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {"inlineData": {"mimeType": mime_type, "data": video_base64}},
                    ],
                }
            ]
        }

        ok, data = await self._post_json(url, headers, payload)
        if not ok:
            return False, str(data)

        #logger.info(f"输出内容：{data}")
        text = self._extract_text_from_gemini(data)
        if not text:
            return False, "模型未返回有效描述"
        return True, text

    async def _post_json(
        self,
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
        timeout_seconds: int = 120,
    ) -> Tuple[bool, Any]:
        try:
            timeout = aiohttp.ClientTimeout(total=timeout_seconds)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, headers=headers, json=payload) as resp:
                    text = await resp.text()
                    if resp.status < 200 or resp.status >= 300:
                        return False, f"HTTP {resp.status}: {text[:200]}"
                    try:
                        return True, await resp.json()
                    except Exception:
                        return False, f"返回解析失败: {text[:200]}"
        except asyncio.TimeoutError:
            return False, "请求超时"
        except Exception as e:
            return False, f"请求异常: {str(e)[:200]}"

    @staticmethod
    def _extract_text_from_gemini(data: Dict[str, Any]) -> str:
        try:
            candidates = data.get("candidates") or []
            if not candidates:
                return ""
            content = candidates[0].get("content") or {}
            parts = content.get("parts") or []
            text = parts[-1]['text']
            if text:
                return text
        except Exception:
            return ""
        return ""

    @staticmethod
    def _clean_description(text: str) -> str:
        """清洗描述文本，只保留最终中文描述"""
        if not text:
            return ""

        cleaned = text.strip()

        # 优先截取“最终结果”之后的内容（若存在）
        markers = [
            "最终结果",
            "最终结果是",
            "最终描述",
            "final result",
            "Final result",
            "The final result is",
            "Finalizing",
        ]
        for marker in markers:
            if marker in cleaned:
                cleaned = cleaned.split(marker, 1)[-1].strip()

        # 去掉包裹用的引号与多余标点
        cleaned = cleaned.strip(" \t\r\n:：\"“”'[]（）()")

        # 提取最后一段中文描述（含中文标点）
        candidates = re.findall(r"[\u4e00-\u9fff][^。！？!?]*[。！？!?]?", cleaned)
        if candidates:
            return candidates[-1].strip()

        # 若没有中文，直接返回清理后的文本
        return cleaned

    @staticmethod
    def _is_url(value: str) -> bool:
        return isinstance(value, str) and value.startswith(("http://", "https://"))
