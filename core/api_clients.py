import asyncio
import json
import aiohttp
import requests
from typing import Dict, Any, Tuple

from src.common.logger import get_logger

logger = get_logger("video_api_cilents")

class ApiClient:
    """统一的API客户端，处理不同格式的图片生成API"""

    def __init__(self, action_instance):
        self.action = action_instance
        self.log_prefix = action_instance.log_prefix

    def _get_proxy_config(self):
        """获取代理配置"""
        try:
            proxy_enabled = self.action.get_config("proxy.enabled", False)
            if not proxy_enabled:
                return None

            proxy_url = self.action.get_config("proxy.url", "http://127.0.0.1:7890")
            timeout = self.action.get_config("proxy.timeout", 60)

            proxy_config = {
                "http": proxy_url,
                "https": proxy_url,
                "timeout": timeout
            }

            logger.info(f"{self.log_prefix} 代理已启用: {proxy_url}")
            return proxy_config
        except Exception as e:
            logger.warning(f"{self.log_prefix} 获取代理配置失败: {e}, 将不使用代理")
            return None

    async def generate_video(self, prompt: str, model_config: Dict[str, Any], 
                             input_image: str = None) -> Tuple[bool, str]:
        """根据API格式调用不同的请求方法，支持重试"""
        api_format = model_config.get("format", "openai")
        try:
            logger.debug(f"{self.log_prefix} 开始API调用")
            # openai格式
            if api_format == "openai":
                success, result = await self._make_openai_request(
                    prompt=prompt,
                    model_config=model_config,
                    input_image=input_image
                )
            # SiliconFlow视频格式
            elif api_format == "siliconflow":
                success, result = await self._make_siliconflow_request(
                    prompt=prompt,
                    model_config=model_config,
                    input_image=input_image
                )
            # Doubao视频格式
            elif api_format == "doubao":
                success, result = await self._make_doubao_request(
                    prompt=prompt,
                    model_config=model_config,
                    input_image=input_image
                )
            # 向量引擎统一视频格式
            elif api_format == "vectorengine":
                success, result = await self._make_vectorengine_request(
                    prompt=prompt,
                    model_config=model_config,
                    input_image=input_image
                )
            
            if success:
                logger.info(f"{self.log_prefix} 视频生成成功")
                return True, result
            else:
                return False, result

        except Exception as e:
            logger.error(f"{self.log_prefix} 后API调用仍异常: {e}")
            return False, f"API调用异常: {str(e)[:100]}"
    

    async def _make_openai_request(self, prompt: str, model_config: Dict[str, Any], input_image: str = None) -> Tuple[bool, str]:
        """发送OPENAI格式的HTTP请求生成视频"""
        try:
            # API配置
            api_key = model_config.get("api_key", "").replace("Bearer ", "")
            model_name = model_config.get("model", "sora-2")  # 使用最新模型
            base_url = model_config.get("base_url", "https://api.openai.com/v1").rstrip('/')
            size = model_config.get("size", None)
            seconds = model_config.get("seconds", "10")
            watermark = model_config.get("watermark", False)
        
            # 构建API端点
            CREATE_URL = f"{base_url}/videos"
        
            # 请求头
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }

            # 构建请求内容
            payload = {
                "model": model_name,
                "prompt": prompt,
                "size": size,
                "seconds": seconds,  # ⚠ 必须是字符串，默认是10s
                "watermark":watermark
            }

            # 如果有输入图片，添加到请求中
            if input_image:
                logger.info(f"{self.log_prefix} (OPENAI) 使用图生视频模式")
                payload["images"] = [input_image]
            else:
                logger.info(f"{self.log_prefix} (OPENAI) 使用文生视频模式")
        
            logger.info(f"{self.log_prefix} (OPENAI) 发起视频请求: {model_name}")

            # 获取代理配置
            proxy_config = self._get_proxy_config()

            # 构建请求参数
            request_kwargs = {
                "url": CREATE_URL,
                "headers": headers,
                "json": payload,
                "timeout": proxy_config.get('timeout', 120) if proxy_config else 120
            }

            # 如果启用了代理，添加代理配置
            if proxy_config:
                request_kwargs["proxies"] = {
                    "http": proxy_config["http"],
                    "https": proxy_config["https"]
                }

            # 发送请求
            response = requests.post(**request_kwargs)

            # 检查响应状态
            if response.status_code != 200:
                error_msg = response.text
                logger.error(f"{self.log_prefix} (OPENAI) API请求失败: HTTP {response.status_code} - {error_msg}")
                return False, f"API请求失败: {error_msg[:100]}"

            # 解析响应
            try:
                response_json = response.json()

                # 查找生成的视频任务数据
                task_id = response_json.get("id")
                # 成功申请视频创建任务
                if task_id:
                    logger.info(f"{self.log_prefix} (OPENAI) 成功申请视频创建")
                    # 等待视频生成结果，然后返回（一般需要3-5分钟）
                    return await self._poll_openai_task(task_id=task_id, model_config=model_config)
                else:
                    logger.error(f"{self.log_prefix} (OPENAI) 申请视频创建失败")
                    return False, f"API错误: {str(response)}"
                
            
            except json.JSONDecodeError as e:
                logger.error(f"{self.log_prefix} (OPENAI) JSON解析失败: {e}")
                return False, f"响应解析失败: {str(e)}"
        
        except requests.RequestException as e:
            logger.error(f"{self.log_prefix} (OPENAI) 网络请求异常: {e}")
            return False, f"网络请求失败: {str(e)}"
    
        except Exception as e:
            logger.error(f"{self.log_prefix} (OPENAI) 请求异常: {e!r}", exc_info=True)
            return False, f"请求失败: {str(e)}"
        
    async def _make_siliconflow_request(self, prompt: str, model_config: Dict[str, Any], input_image: str = None) -> Tuple[bool, str]:
        """发送SiliconFlow格式的HTTP请求生成视频"""
        try:
            # API配置
            api_key = model_config.get("api_key", "").replace("Bearer ", "")
            model_name = model_config.get("model", "Wan-AI/Wan2.2-I2V-A14B")
            base_url = model_config.get("base_url", "https://api.siliconflow.cn/v1").rstrip('/')
            size = model_config.get("size", None)
        
            # 构建API端点
            CREATE_URL = f"{base_url}/video/submit"
        
            # 请求头
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }

            # 构建请求内容
            payload = {
                "model": model_name,
                "prompt": prompt,
                "image_size": size,
            }

            # 如果有输入图片，添加到请求中
            if input_image:
                logger.info(f"{self.log_prefix} (SiliconFlow) 使用图生视频模式")
                payload["image"] = input_image
            else:
                logger.info(f"{self.log_prefix} (SiliconFlow) 使用文生视频模式")
        
            logger.info(f"{self.log_prefix} (SiliconFlow 发起视频请求: {model_name}")

            # 获取代理配置
            proxy_config = self._get_proxy_config()

            # 构建请求参数
            request_kwargs = {
                "url": CREATE_URL,
                "headers": headers,
                "json": payload,
                "timeout": proxy_config.get('timeout', 120) if proxy_config else 120
            }

            # 如果启用了代理，添加代理配置
            if proxy_config:
                request_kwargs["proxies"] = {
                    "http": proxy_config["http"],
                    "https": proxy_config["https"]
                }

            # 发送请求
            response = requests.post(**request_kwargs)

            # 检查响应状态
            if response.status_code != 200:
                error_msg = response.text
                logger.error(f"{self.log_prefix} (SiliconFlow) API请求失败: HTTP {response.status_code} - {error_msg}")
                return False, f"API请求失败: {error_msg[:100]}"

            # 解析响应
            try:
                response_json = response.json()

                # 查找生成的视频任务数据
                requestId = response_json.get("requestId")
                # 成功申请视频创建任务
                if requestId:
                    logger.info(f"{self.log_prefix} (SiliconFlow) 成功申请视频创建")
                    # 等待视频生成结果，然后返回
                    return await self._poll_siliconflow_task(requestId=requestId, model_config=model_config)
                else:
                    logger.error(f"{self.log_prefix} (SiliconFlow) 申请视频创建失败")
                    return False, f"API错误: {str(response)}"
                
            
            except json.JSONDecodeError as e:
                logger.error(f"{self.log_prefix} (SiliconFlow) JSON解析失败: {e}")
                return False, f"响应解析失败: {str(e)}"
        
        except requests.RequestException as e:
            logger.error(f"{self.log_prefix} (SiliconFlow) 网络请求异常: {e}")
            return False, f"网络请求失败: {str(e)}"
    
        except Exception as e:
            logger.error(f"{self.log_prefix} (SiliconFlow) 请求异常: {e!r}", exc_info=True)
            return False, f"请求失败: {str(e)}"
        
    async def _make_doubao_request(self, prompt: str, model_config: Dict[str, Any], input_image: str = None) -> Tuple[bool, str]:
        """发送Doubao格式的HTTP请求生成视频"""
        try:
            # API配置
            api_key = model_config.get("api_key", "").replace("Bearer ", "")
            model_name = model_config.get("model", "doubao-seedance-1-0-pro-250528")
            base_url = model_config.get("base_url", "https://ark.cn-beijing.volces.com/api/v3").rstrip('/')
            generate_audio = model_config.get("generate_audio", None)
            ratio = model_config.get("ratio", "adaptive")
            duration = int(model_config.get("seconds", "5"))
            watermark = model_config.get("watermark", False)
        
            # 构建API端点
            CREATE_URL = f"{base_url}/contents/generations/tasks"

            # 构建content内容
            content = []
            content.append({"type": "text","text": prompt})

            # 如果有输入图片，添加到请求中
            if input_image:
                logger.info(f"{self.log_prefix} (Doubao) 使用图生视频模式")
                content.append({"type": "image_url","image_url": {"url": input_image}})
            else:
                logger.info(f"{self.log_prefix} (Doubao) 使用文生视频模式")
        
            # 请求头
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }

            # 构建请求内容
            payload = {
                "model": model_name,
                "content": content,
                "ratio": ratio,
                "duration": duration,
                "watermark": watermark,
            }

            # 是否有声（需在config文件中自行添加）
            if generate_audio:
                payload["generate_audio"] = generate_audio

            # 获取代理配置
            proxy_config = self._get_proxy_config()

            logger.info(f"{self.log_prefix} (Doubao 发起视频请求: {model_name}")
            # 构建请求参数
            request_kwargs = {
                "url": CREATE_URL,
                "headers": headers,
                "json": payload,
                "timeout": proxy_config.get('timeout', 120) if proxy_config else 120
            }

            # 如果启用了代理，添加代理配置
            if proxy_config:
                request_kwargs["proxies"] = {
                    "http": proxy_config["http"],
                    "https": proxy_config["https"]
                }

            # 发送请求
            response = requests.post(**request_kwargs)

            # 检查响应状态
            if response.status_code != 200:
                error_msg = response.text
                logger.error(f"{self.log_prefix} (Doubao) API请求失败: HTTP {response.status_code} - {error_msg}")
                return False, f"API请求失败: {error_msg[:100]}"

            # 解析响应
            try:
                response_json = response.json()

                # 查找生成的视频任务数据
                task_id = response_json.get("id")
                # 成功申请视频创建任务
                if task_id:
                    logger.info(f"{self.log_prefix} (Doubao) 成功申请视频创建")
                    # 等待视频生成结果，然后返回
                    return await self._poll_doubao_task(task_id=task_id, model_config=model_config)
                else:
                    logger.error(f"{self.log_prefix} (Doubao) 申请视频创建失败")
                    return False, f"API错误: {str(response)}"
                
            
            except json.JSONDecodeError as e:
                logger.error(f"{self.log_prefix} (Doubao) JSON解析失败: {e}")
                return False, f"响应解析失败: {str(e)}"
        
        except requests.RequestException as e:
            logger.error(f"{self.log_prefix} (Doubao) 网络请求异常: {e}")
            return False, f"网络请求失败: {str(e)}"
    
        except Exception as e:
            logger.error(f"{self.log_prefix} (Doubao) 请求异常: {e!r}", exc_info=True)
            return False, f"请求失败: {str(e)}"
        
    async def _make_vectorengine_request(self, prompt: str, model_config: Dict[str, Any], input_image: str = None) -> Tuple[bool, str]:
        """发送向量引擎统一视频格式的HTTP请求生成视频"""
        try:
            # API配置
            api_key = model_config.get("api_key", "").replace("Bearer ", "")
            model_name = model_config.get("model", "grok-video-3")  # 使用最新模型
            base_url = model_config.get("base_url", "https://api.vectorengine.ai/v1").rstrip('/')
            resolution = model_config.get("resolution", None)
            aspect_ratio = model_config.get("aspect_ratio", None)
            orientation = model_config.get("orientation", None)
        
            # 构建API端点
            CREATE_URL = f"{base_url}/video/create"
        
            # 请求头
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }

            # 构建请求内容
            payload = {
                "model": model_name,
                "prompt": prompt,
            }

            # 添加部分请求内容
            if resolution:
                payload["size"] = resolution
            # 如果有设置视频比例
            if aspect_ratio:
                payload["aspect_ratio"] = aspect_ratio
            if orientation:
                payload["orientation"] = orientation

            # 如果有输入图片，添加到请求中
            if input_image:
                logger.info(f"{self.log_prefix} (向量引擎) 使用图生视频模式")
                payload["images"] = [input_image]
            else:
                logger.info(f"{self.log_prefix} (向量引擎) 使用文生视频模式")
        
            logger.info(f"{self.log_prefix} (向量引擎) 发起视频请求: {model_name}")

            # 获取代理配置
            proxy_config = self._get_proxy_config()

            # 构建请求参数
            request_kwargs = {
                "url": CREATE_URL,
                "headers": headers,
                "json": payload,
                "timeout": proxy_config.get('timeout', 120) if proxy_config else 120
            }

            # 如果启用了代理，添加代理配置
            if proxy_config:
                request_kwargs["proxies"] = {
                    "http": proxy_config["http"],
                    "https": proxy_config["https"]
                }

            # 发送请求
            response = requests.post(**request_kwargs)

            # 检查响应状态
            if response.status_code != 200:
                error_msg = response.text
                logger.error(f"{self.log_prefix} (向量引擎) API请求失败: HTTP {response.status_code} - {error_msg}")
                return False, f"API请求失败: {error_msg[:100]}"

            # 解析响应
            try:
                response_json = response.json()

                # 查找生成的视频任务数据
                task_id = response_json.get("id")
                # 成功申请视频创建任务
                if task_id:
                    logger.info(f"{self.log_prefix} (向量引擎) 成功申请视频创建")
                    # 等待视频生成结果，然后返回（一般需要3-5分钟）
                    return await self._poll_vectorengine_task(task_id=task_id, model_config=model_config)
                else:
                    logger.error(f"{self.log_prefix} (向量引擎) 申请视频创建失败")
                    return False, f"API错误: {str(response)}"
                
            
            except json.JSONDecodeError as e:
                logger.error(f"{self.log_prefix} (向量引擎) JSON解析失败: {e}")
                return False, f"响应解析失败: {str(e)}"
        
        except requests.RequestException as e:
            logger.error(f"{self.log_prefix} (向量引擎) 网络请求异常: {e}")
            return False, f"网络请求失败: {str(e)}"
    
        except Exception as e:
            logger.error(f"{self.log_prefix} (向量引擎) 请求异常: {e!r}", exc_info=True)
            return False, f"请求失败: {str(e)}"


    async def _poll_openai_task(self, task_id,  model_config: Dict[str, Any],) -> Tuple[bool, str]:
        # API配置
        api_key = model_config.get("api_key", "").replace("Bearer ", "")
        base_url = model_config.get("base_url", "https://api.openai.com/v1").rstrip('/')
        # 请求头
        headers = {"Authorization": f"Bearer {api_key}"}
        url = f"{base_url}/videos/{task_id}"
        # 获取代理配置
        proxy_config = self._get_proxy_config()
        # aiohttp 超时设置
        timeout = aiohttp.ClientTimeout(
            total=proxy_config.get("timeout", 120) if proxy_config else 120
        )
        # aiohttp 使用的 proxy（只需要一个 https）
        proxy = None
        if proxy_config:
            proxy = proxy_config.get("https") or proxy_config.get("http")

        try:
            async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
                while True:
                    async with session.get(url, proxy=proxy) as resp:
                        data = await resp.json()

                    if data["status"] == "completed":
                        return True, data["video_url"]

                    if data["status"] == "failed":
                        logger.error(f"{self.log_prefix} (OPENAI) 视频生成失败: {data}")
                        return False, f"视频生成失败：{data["error"]}"

                    await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"{self.log_prefix} (OPENAI) 请求异常: {e}")
            return False, f"请求失败: {str(e)}"
        
    async def _poll_siliconflow_task(self, requestId,  model_config: Dict[str, Any],) -> Tuple[bool, str]:
        # API配置
        api_key = model_config.get("api_key", "").replace("Bearer ", "")
        base_url = model_config.get("base_url", "https://api.siliconflow.cn/v1").rstrip('/')
        
        url = f"{base_url}/video/status"

        payload = { "requestId": requestId }
        # 请求头
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        # 获取代理配置
        proxy_config = self._get_proxy_config()
        # aiohttp 超时设置
        timeout = aiohttp.ClientTimeout(
            total=proxy_config.get("timeout", 120) if proxy_config else 120
        )
        # aiohttp 使用的 proxy（只需要一个 https）
        proxy = None
        if proxy_config:
            proxy = proxy_config.get("https") or proxy_config.get("http")

        try:
            async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
                while True:
                    async with session.post(url, json=payload, proxy=proxy) as resp:
                        data = await resp.json()
                    # 判断任务状态
                    if data.get("status") == "Succeed":
                        video_url = data["results"]["videos"][0]["url"]
                        return True, video_url or "视频地址未返回"
                    if data.get("status") in ("Failed"):
                        self.logger.error(f"{self.log_prefix} (SiliconFlow) 视频生成失败: {data}")
                        return False, f"视频生成失败: {data.get('error', '未知错误')}"
                    # 等待5秒再轮询
                    await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"{self.log_prefix} (SiliconFlow) 请求异常: {e}")
            return False, f"请求失败: {str(e)}"
        
    async def _poll_doubao_task(self, task_id,  model_config: Dict[str, Any],) -> Tuple[bool, str]:
        # API配置
        api_key = model_config.get("api_key", "").replace("Bearer ", "")
        base_url = model_config.get("base_url", "https://ark.cn-beijing.volces.com/api/v3").rstrip('/')
        # 请求头
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        url = f"{base_url}/contents/generations/tasks/{task_id}"
        # 获取代理配置
        proxy_config = self._get_proxy_config()
        # aiohttp 超时设置
        timeout = aiohttp.ClientTimeout(
            total=proxy_config.get("timeout", 120) if proxy_config else 120
        )
        # aiohttp 使用的 proxy（只需要一个 https）
        proxy = None
        if proxy_config:
            proxy = proxy_config.get("https") or proxy_config.get("http")

        try:
            async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
                while True:
                    async with session.get(url, proxy=proxy) as resp:
                        data = await resp.json()

                    if data["status"] == "succeeded":
                        return True, data["content"]["video_url"]

                    if data["status"] == "failed":
                        logger.error(f"{self.log_prefix} (Doubao) 视频生成失败: {data["error"]}")
                        return False, f"视频生成失败：{data["error"]}"
                    
                    if data["status"] == "expired":
                        logger.error(f"{self.log_prefix} (Doubao) 视频生成超时: {data["error"]}")
                        return False, f"视频生成超时：{data["error"]}"

                    await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"{self.log_prefix} (Doubao) 请求异常: {e}")
            return False, f"请求失败: {str(e)}"
        
    async def _poll_vectorengine_task(self, task_id,  model_config: Dict[str, Any],) -> Tuple[bool, str]:
        # API配置
        api_key = model_config.get("api_key", "").replace("Bearer ", "")
        base_url = model_config.get("base_url", "https://api.vectorengine.ai/v1").rstrip('/')
        # 请求头
        headers = {"Authorization": f"Bearer {api_key}"}
        url = f"{base_url}/video/query?id={task_id}"
        # 获取代理配置
        proxy_config = self._get_proxy_config()
        # aiohttp 超时设置
        timeout = aiohttp.ClientTimeout(
            total=proxy_config.get("timeout", 120) if proxy_config else 120
        )
        # aiohttp 使用的 proxy（只需要一个 https）
        proxy = None
        if proxy_config:
            proxy = proxy_config.get("https") or proxy_config.get("http")

        try:
            async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
                while True:
                    async with session.get(url, proxy=proxy) as resp:
                        data = await resp.json()

                    if data["status"] == "completed":
                        return True, data["video_url"]

                    if data["status"] == "failed":
                        logger.error(f"{self.log_prefix} (向量引擎) 视频生成失败: {data}")
                        return False, f"视频生成失败：{data["error"]}"

                    await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"{self.log_prefix} (向量引擎) 请求异常: {e}")
            return False, f"请求失败: {str(e)}"