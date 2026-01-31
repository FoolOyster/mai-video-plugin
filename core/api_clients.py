import asyncio
import json
import time
from typing import Dict, Any, Tuple, Optional

import aiohttp

from src.common.logger import get_logger

logger = get_logger("video_api_clients")


class CircuitBreaker:
    def __init__(self, failure_threshold: int, recovery_seconds: int, half_open_max_success: int):
        self.failure_threshold = max(1, int(failure_threshold))
        self.recovery_seconds = max(1, int(recovery_seconds))
        self.half_open_max_success = max(1, int(half_open_max_success))
        self.failure_count = 0
        self.opened_at: Optional[float] = None
        self.half_open_success = 0

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        if time.monotonic() - self.opened_at >= self.recovery_seconds:
            return True
        return False

    def record_success(self):
        if self.opened_at is None:
            self.failure_count = 0
            return
        self.half_open_success += 1
        if self.half_open_success >= self.half_open_max_success:
            self.opened_at = None
            self.failure_count = 0
            self.half_open_success = 0

    def record_failure(self):
        if self.opened_at is not None:
            return
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self.opened_at = time.monotonic()
            self.half_open_success = 0


class ApiClient:
    """多平台 API 客户端。"""

    _breakers: Dict[str, CircuitBreaker] = {}

    def __init__(self, action_instance):
        self.action = action_instance
        self.log_prefix = action_instance.log_prefix

    def _get_proxy_config(self) -> Optional[Dict[str, Any]]:
        try:
            proxy_enabled = self.action.get_config("proxy.enabled", False)
            if not proxy_enabled:
                return None
            proxy_url = self.action.get_config("proxy.url", "http://127.0.0.1:7890")
            timeout = self.action.get_config("proxy.timeout", 60)
            return {"proxy": proxy_url, "timeout": timeout}
        except Exception as e:
            logger.warning(f"{self.log_prefix} 代理配置读取失败: {e}")
            return None

    def _get_breaker(self, key: str) -> CircuitBreaker:
        if key not in self._breakers:
            threshold = self.action.get_config("circuit_breaker.failure_threshold", 5)
            recovery = self.action.get_config("circuit_breaker.recovery_seconds", 120)
            half_open = self.action.get_config("circuit_breaker.half_open_max_success", 2)
            self._breakers[key] = CircuitBreaker(threshold, recovery, half_open)
        return self._breakers[key]

    async def _request_json(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any] = None,
        timeout_seconds: int = 120,
        max_retries: int = 0,
        backoff_seconds: int = 2,
        proxy: Optional[str] = None,
    ) -> Tuple[bool, Any]:
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                timeout = aiohttp.ClientTimeout(total=timeout_seconds)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.request(
                        method=method,
                        url=url,
                        headers=headers,
                        json=payload,
                        proxy=proxy,
                    ) as resp:
                        text = await resp.text()
                        if resp.status < 200 or resp.status >= 300:
                            if resp.status in (408, 429, 500, 502, 503, 504) and attempt < max_retries:
                                await asyncio.sleep(backoff_seconds * (2 ** attempt))
                                continue
                            return False, f"HTTP {resp.status}: {text[:200]}"
                        try:
                            return True, json.loads(text) if text else {}
                        except json.JSONDecodeError as e:
                            return False, f"JSON 解析失败: {str(e)[:100]}"
            except aiohttp.ClientError as e:
                last_error = str(e)
                if attempt < max_retries:
                    await asyncio.sleep(backoff_seconds * (2 ** attempt))
                    continue
                return False, f"网络错误: {str(e)[:100]}"
            except Exception as e:
                last_error = str(e)
                if attempt < max_retries:
                    await asyncio.sleep(backoff_seconds * (2 ** attempt))
                    continue
                return False, f"请求异常: {str(e)[:100]}"
            return False, f"请求失败: {last_error}"

    async def generate_video(
        self,
        prompt: str,
        model_config: Dict[str, Any],
        input_image: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> Tuple[bool, str]:
        api_format = model_config.get("format", "openai")
        breaker_key = model_id or model_config.get("base_url", "default")
        breaker_enabled = self.action.get_config("circuit_breaker.enabled", True)
        breaker = self._get_breaker(breaker_key)
        if breaker_enabled and not breaker.allow():
            return False, "服务暂不可用（熔断中）"

        try:
            if api_format == "openai":
                success, result = await self._make_openai_request(prompt, model_config, input_image)
            elif api_format == "siliconflow":
                success, result = await self._make_siliconflow_request(prompt, model_config, input_image)
            elif api_format == "doubao":
                success, result = await self._make_doubao_request(prompt, model_config, input_image)
            elif api_format == "vectorengine":
                success, result = await self._make_vectorengine_request(prompt, model_config, input_image)
            else:
                return False, f"不支持的接口格式: {api_format}"

            if success:
                if breaker_enabled:
                    breaker.record_success()
                return True, result

            if breaker_enabled:
                breaker.record_failure()
            return False, result
        except Exception as e:
            if breaker_enabled:
                breaker.record_failure()
            logger.error(f"{self.log_prefix} API 调用异常: {e}")
            return False, f"API 异常: {str(e)[:100]}"

    async def _make_openai_request(
        self, prompt: str, model_config: Dict[str, Any], input_image: Optional[str] = None
    ) -> Tuple[bool, str]:
        api_key = model_config.get("api_key", "").replace("Bearer ", "")
        model_name = model_config.get("model", "sora-2")
        base_url = model_config.get("base_url", "https://api.openai.com/v1").rstrip("/")
        size = model_config.get("size", None)
        seconds = model_config.get("seconds", "10")
        watermark = model_config.get("watermark", False)

        create_url = f"{base_url}/videos"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model_name,
            "prompt": prompt,
            "size": size,
            "seconds": seconds,
            "watermark": watermark,
        }
        if input_image:
            payload["images"] = [input_image]

        proxy_config = self._get_proxy_config()
        proxy = proxy_config.get("proxy") if proxy_config else None
        timeout_seconds = self.action.get_config("api.request_timeout_seconds", 120)
        max_retries = self.action.get_config("api.submit_max_retries", 2)
        backoff_seconds = self.action.get_config("api.submit_backoff_seconds", 2)

        ok, data = await self._request_json(
            "POST",
            create_url,
            headers,
            payload,
            timeout_seconds,
            max_retries,
            backoff_seconds,
            proxy,
        )
        if not ok:
            return False, str(data)

        task_id = data.get("id")
        if not task_id:
            return False, f"API 返回异常: {str(data)[:100]}"

        return await self._poll_openai_task(task_id, model_config)

    async def _make_siliconflow_request(
        self, prompt: str, model_config: Dict[str, Any], input_image: Optional[str] = None
    ) -> Tuple[bool, str]:
        api_key = model_config.get("api_key", "").replace("Bearer ", "")
        model_name = model_config.get("model", "Wan-AI/Wan2.2-I2V-A14B")
        base_url = model_config.get("base_url", "https://api.siliconflow.cn/v1").rstrip("/")
        size = model_config.get("size", None)

        create_url = f"{base_url}/video/submit"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {"model": model_name, "prompt": prompt, "image_size": size}
        if input_image:
            payload["image"] = input_image

        proxy_config = self._get_proxy_config()
        proxy = proxy_config.get("proxy") if proxy_config else None
        timeout_seconds = self.action.get_config("api.request_timeout_seconds", 120)
        max_retries = self.action.get_config("api.submit_max_retries", 2)
        backoff_seconds = self.action.get_config("api.submit_backoff_seconds", 2)

        ok, data = await self._request_json(
            "POST",
            create_url,
            headers,
            payload,
            timeout_seconds,
            max_retries,
            backoff_seconds,
            proxy,
        )
        if not ok:
            return False, str(data)

        request_id = data.get("requestId")
        if not request_id:
            return False, f"API 返回异常: {str(data)[:100]}"

        return await self._poll_siliconflow_task(request_id, model_config)

    async def _make_doubao_request(
        self, prompt: str, model_config: Dict[str, Any], input_image: Optional[str] = None
    ) -> Tuple[bool, str]:
        api_key = model_config.get("api_key", "").replace("Bearer ", "")
        model_name = model_config.get("model", "doubao-seedance-1-0-pro-250528")
        base_url = model_config.get("base_url", "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
        generate_audio = model_config.get("generate_audio", None)
        ratio = model_config.get("ratio", "adaptive")
        duration = int(model_config.get("seconds", "5"))
        watermark = model_config.get("watermark", False)

        create_url = f"{base_url}/contents/generations/tasks"
        content = [{"type": "text", "text": prompt}]
        if input_image:
            content.append({"type": "image_url", "image_url": {"url": input_image}})

        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model_name,
            "content": content,
            "ratio": ratio,
            "duration": duration,
            "watermark": watermark,
        }
        if generate_audio:
            payload["generate_audio"] = generate_audio

        proxy_config = self._get_proxy_config()
        proxy = proxy_config.get("proxy") if proxy_config else None
        timeout_seconds = self.action.get_config("api.request_timeout_seconds", 120)
        max_retries = self.action.get_config("api.submit_max_retries", 2)
        backoff_seconds = self.action.get_config("api.submit_backoff_seconds", 2)

        ok, data = await self._request_json(
            "POST",
            create_url,
            headers,
            payload,
            timeout_seconds,
            max_retries,
            backoff_seconds,
            proxy,
        )
        if not ok:
            return False, str(data)

        task_id = data.get("id")
        if not task_id:
            return False, f"API 返回异常: {str(data)[:100]}"

        return await self._poll_doubao_task(task_id, model_config)

    async def _make_vectorengine_request(
        self, prompt: str, model_config: Dict[str, Any], input_image: Optional[str] = None
    ) -> Tuple[bool, str]:
        api_key = model_config.get("api_key", "").replace("Bearer ", "")
        model_name = model_config.get("model", "grok-video-3")
        base_url = model_config.get("base_url", "https://api.vectorengine.ai/v1").rstrip("/")
        resolution = model_config.get("resolution", None)
        aspect_ratio = model_config.get("aspect_ratio", None)
        orientation = model_config.get("orientation", None)

        create_url = f"{base_url}/video/create"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {"model": model_name, "prompt": prompt}
        if resolution:
            payload["size"] = resolution
        if aspect_ratio:
            payload["aspect_ratio"] = aspect_ratio
        if orientation:
            payload["orientation"] = orientation
        if input_image:
            payload["images"] = [input_image]

        proxy_config = self._get_proxy_config()
        proxy = proxy_config.get("proxy") if proxy_config else None
        timeout_seconds = self.action.get_config("api.request_timeout_seconds", 120)
        max_retries = self.action.get_config("api.submit_max_retries", 2)
        backoff_seconds = self.action.get_config("api.submit_backoff_seconds", 2)

        ok, data = await self._request_json(
            "POST",
            create_url,
            headers,
            payload,
            timeout_seconds,
            max_retries,
            backoff_seconds,
            proxy,
        )
        if not ok:
            return False, str(data)

        task_id = data.get("id")
        if not task_id:
            return False, f"API 返回异常: {str(data)[:100]}"

        return await self._poll_vectorengine_task(task_id, model_config)

    async def _poll_openai_task(self, task_id: str, model_config: Dict[str, Any]) -> Tuple[bool, str]:
        api_key = model_config.get("api_key", "").replace("Bearer ", "")
        base_url = model_config.get("base_url", "https://api.openai.com/v1").rstrip("/")
        url = f"{base_url}/videos/{task_id}"
        headers = {"Authorization": f"Bearer {api_key}"}
        return await self._poll_common(url, headers, method="GET")

    async def _poll_siliconflow_task(self, request_id: str, model_config: Dict[str, Any]) -> Tuple[bool, str]:
        api_key = model_config.get("api_key", "").replace("Bearer ", "")
        base_url = model_config.get("base_url", "https://api.siliconflow.cn/v1").rstrip("/")
        url = f"{base_url}/video/status"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {"requestId": request_id}
        return await self._poll_common(url, headers, method="POST", payload=payload)

    async def _poll_doubao_task(self, task_id: str, model_config: Dict[str, Any]) -> Tuple[bool, str]:
        api_key = model_config.get("api_key", "").replace("Bearer ", "")
        base_url = model_config.get("base_url", "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
        url = f"{base_url}/contents/generations/tasks/{task_id}"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        return await self._poll_common(url, headers, method="GET")

    async def _poll_vectorengine_task(self, task_id: str, model_config: Dict[str, Any]) -> Tuple[bool, str]:
        api_key = model_config.get("api_key", "").replace("Bearer ", "")
        base_url = model_config.get("base_url", "https://api.vectorengine.ai/v1").rstrip("/")
        url = f"{base_url}/video/query?id={task_id}"
        headers = {"Authorization": f"Bearer {api_key}"}
        return await self._poll_common(url, headers, method="GET")

    async def _poll_common(
        self,
        url: str,
        headers: Dict[str, str],
        method: str = "GET",
        payload: Dict[str, Any] = None,
    ) -> Tuple[bool, str]:
        proxy_config = self._get_proxy_config()
        proxy = proxy_config.get("proxy") if proxy_config else None
        timeout_seconds = self.action.get_config("api.request_timeout_seconds", 120)
        poll_interval = self.action.get_config("api.poll_interval_seconds", 5)
        poll_timeout = self.action.get_config("api.poll_timeout_seconds", 900)
        poll_max_attempts = self.action.get_config("api.poll_max_attempts", 0)

        start_time = time.monotonic()
        attempts = 0
        while True:
            if poll_timeout > 0 and (time.monotonic() - start_time) > poll_timeout:
                return False, "轮询超时"
            if poll_max_attempts and attempts >= poll_max_attempts:
                return False, "轮询次数达到上限"

            ok, data = await self._request_json(
                method,
                url,
                headers,
                payload,
                timeout_seconds,
                0,
                0,
                proxy,
            )
            if not ok:
                return False, str(data)

            status = (data.get("status") or "").lower()
            if status in ("completed", "succeeded", "success", "succeed"):
                return True, self._extract_video_url(data)
            if status in ("failed", "error", "expired"):
                error_msg = data.get("error") or data.get("message") or str(data)
                return False, f"任务失败: {str(error_msg)[:200]}"

            attempts += 1
            await asyncio.sleep(poll_interval)

    @staticmethod
    def _extract_video_url(data: Dict[str, Any]) -> str:
        if "video_url" in data:
            return data.get("video_url")
        if "content" in data and isinstance(data["content"], dict):
            return data["content"].get("video_url")
        if "results" in data:
            results = data.get("results") or {}
            videos = results.get("videos") or []
            if videos and isinstance(videos, list):
                return videos[0].get("url") or ""
        return ""
