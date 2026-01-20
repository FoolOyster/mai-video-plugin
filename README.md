# mai-video-plugin（麦麦视频生成插件）

兼容多种“视频生成模型 API”的麦麦插件，支持 **文生视频** 与 **图生视频**（从消息中取最近一张图片作为参考图）。
当前仅适配 **QQ 平台**，视频发送依赖 **Napcat HTTP 正向服务器**。

- 插件名：`mai_video_plugin`
- 插件入口：`VideoPlugin`（见 `plugin.py`）
- License：MIT

---

## 功能特性

- 文生视频：`/video <描述>`
- 图生视频：在发送命令时消息中带图（或引用近期带图消息），插件会自动取图进行图生视频
- 横屏 / 竖屏：`/video-l`（landscape）、`/video-p`（portrait）
- 多模型配置：支持多个模型配置并可在运行时切换
- 多种 API 格式适配：
  - `openai`（通用：Sora2 / Veo 等风格接口）
  - `siliconflow`
  - `doubao`
  - `vectorengine`
- 可选对象存储图床：COS / OSS / Cloudflare R2
  （用于图生视频时把 base64 图片上传成临时 URL，提高兼容性）
- 可选代理：支持 HTTP/HTTPS/SOCKS5 代理转发 API 请求

---

## 安装与依赖

### Python 依赖

如需启用对象存储上传（COS/OSS/R2），需要安装 `requirements.txt` 中依赖：

- `oss2`
- `cos-python-sdk-v5`
- `boto3` / `botocore`

> 插件本体还使用了 `requests`、`aiohttp` 等库（通常在宿主环境中已存在）。

---

## 配置说明（config.toml）

插件使用 `config_schema` 定义配置（见 `plugin.py`），关键配置如下：

### 1) 启用插件

```toml
[plugin]
enabled = true
```

### 2) 组件配置

```toml
[components]
enable_debug_info = false
command_model = "model1"
max_requests = 3
admin_users = ["12345678"]
```

字段说明：

- `enable_debug_info`：输出更多调试信息
- `command_model`：`/video` 默认使用的模型 ID
- `max_requests`：全局并发视频任务数（越大越吃内存）
- `admin_users`：可使用管理命令（set/reset/config）的管理员 QQ 号列表（字符串）

### 3) Napcat 配置（用于发送视频）

```toml
[napcat]
HOST = "127.0.0.1"
PORT = 5700
```

插件会通过 Napcat HTTP API 调用：
- 私聊：`/send_private_msg`
- 群聊：`/send_group_msg`

### 4) 日志

```toml
[logging]
level = "INFO" # DEBUG/INFO/WARNING/ERROR
prefix = "[unified_video_Plugin]"
```

### 5) 代理（可选）

```toml
[proxy]
enabled = false
url = "http://127.0.0.1:7890"
timeout = 60
```

开启后：API 请求与下载视频会走代理（`requests` + `aiohttp`）。

### 6) 对象存储图床（可选）

```toml
[image_uploader]
enabled = false
provider = "cos" # cos/oss/r2
access_key_id = "xxxxx"
secret_access_key = "xxxxx"
region = "ap-guangzhou"
bucket_name = "your-bucket"
endpoint = ""
```

- `enabled=false` 时：图生视频直接把 base64 图片传给模型（某些平台可能不支持）
- `enabled=true` 时：会上传到 `tmp_images/`，生成有效期 1 小时的临时 URL

### 7) 模型配置（多模型）

插件默认提供 `models.model1` 的基础配置模板（见 `plugin.py`）：

```toml
[models]

[models.model1]
name = "OpenAI-Sora2模型"
base_url = "https://api.openai.com/v1"
api_key = "xxxxxxxx"
format = "openai" # openai/siliconflow/doubao/vectorengine
model = "sora-2"
support_option = "3" # 1=仅文生视频 2=仅图生视频 3=都支持
seconds = "10"
resolution = "720p" # 480p/720p/1080p（用于 size 推导）
watermark = false
```

你也可以继续添加 `model2`、`model3`：

```toml
[models.model2]
name = "硅基 Wan2.2"
base_url = "https://api.siliconflow.cn/v1"
api_key = "xxxx"
format = "siliconflow"
model = "Wan-AI/Wan2.2-I2V-A14B"
support_option = "3"
```

---

## 使用方法（QQ）

### 生成视频

- 默认比例（由模型或接口自适应）：
  - `/video 一只小猫在雨夜的霓虹灯街道奔跑，电影质感`
- 横屏：
  - `/video-l 一辆跑车穿越沙漠公路，镜头跟拍`
- 竖屏：
  - `/video-p 少女在樱花树下回头微笑，柔光，慢动作`

### 图生视频

在发送 `/video ...` 时带上一张图片（或引用最近含图消息），插件会自动取最近图片作为输入图：
- 若启用 `image_uploader.enabled=true`：图片会先上传成临时 URL，再交给模型
- 否则：直接以 base64 方式传入模型（部分平台可能不兼容）

---

## 配置管理命令（管理员）

命令入口：`VideoConfigCommand`（`core/video_command.py`）

- `/video list` 或 `/video models`：列出模型
- `/video config`：查看当前运行配置（是否被运行时覆盖）
- `/video set <模型ID>`：运行时切换 `/video` 使用的模型（不写文件，重启会恢复）
- `/video reset`：清除运行时覆盖，恢复默认
- `/video help`：帮助

权限判断：`components.admin_users` 中包含当前用户 QQ 号才可执行 set/reset/config。

---

## 视频比例与参数映射

插件根据命令自动调整参数（见 `core/video_command.py`）：

- `openai`：
  - `/video-l`：`size=1280x720`（720p）或 `1792x1024`（1080p）
  - `/video-p`：`size=720x1280`（720p）或 `1024x1792`（1080p）
- `siliconflow`：
  - `/video-l`：`image_size=1280x720`
  - `/video-p`：`image_size=720x1280`
- `doubao`：
  - `/video-l`：`ratio=16:9`
  - `/video-p`：`ratio=9:16`
- `vectorengine`：
  - `/video-l`：`aspect_ratio=16:9`（部分模型会走 `3:2` 分支）
  - `/video-p`：`aspect_ratio=9:16`（部分模型会走 `2:3` 分支）

---

## 工作流程（实现概览）

- `VideoGenerationCommand`
  - 解析命令 `/video(-l|-p)`
  - 从消息/历史消息中提取图片（如有）
  - （可选）上传图片到 COS/OSS/R2 得到临时 URL
  - 调用 `ApiClient.generate_video()` 走不同平台格式
  - 轮询任务状态拿到视频 URL
  - 下载视频并转成 `base64://...`
  - 使用 Napcat 发送视频到群/私聊

---

## 常见问题（FAQ）

1) 为什么我发了命令但一直在等？
- 大多数视频模型生成需要几分钟，插件会轮询任务状态（每 5 秒一次）。

2) 发送失败提示“风控/风险控制”？
- Napcat 返回风控时插件会停止重试，这是 QQ 侧风控限制，通常只能更换号/降低频率/换发送方式。

3) 图生视频不生效？
- 确认消息里确实带图或引用了含图消息；
- 若模型只支持文生视频，请把该模型的 `support_option` 设置为正确值或切换模型；
- 某些平台不接受 base64 图片，建议开启 `image_uploader.enabled=true`。

---

## License

MIT License © 2026 FoolOyster
```
