# mai-video-plugin（麦麦视频生成插件）

面向 MaiBot（麦麦）的视频生成插件，统一适配多家视频生成 API，支持文生视频与图生视频，并提供并发/限流/熔断/代理等工程能力。当前版本对接 QQ 平台消息流（见 manifest 描述）。

## 功能特点

- 多平台 API 适配：`openai` / `siliconflow` / `doubao` / `vectorengine`
- 文生视频 / 图生视频（可自动读取最近图片）
- 命令级横屏/竖屏/默认比例
- 全局并发 + 单用户并发限制
- 请求限流（时间窗）
- 可选熔断器保护（失败自动熔断）
- 代理支持（HTTP/HTTPS/SOCKS5）
- 可选对象存储临时上传图片（OSS/COS/R2）
- 可选「麦麦看视频」：生成视频后由多模态模型生成简短描述，让麦麦知道自己生成了什么视频（测试功能）
- 无需额外开启napcat网络配置

## 运行环境

- MaiBot 版本：`>= 0.12.0`
- Python 依赖：见 `requirements.txt`

## 安装与启用（示例流程）

1. 将插件目录放入 MaiBot 的插件目录(plugins)。
2. 安装依赖：

```bash
pip install -r requirements.txt
```

3. 在插件配置中启用插件（`plugin.enabled = true`）。
4. 配置至少一个模型（`models.model1` 或自定义 ID），并设置 `components.command_model` 为默认模型。
5. 重启 MaiBot。


## 使用方式

### 生成视频

- `/video <描述>`：默认比例
- `/video-l <描述>`：横屏
- `/video-p <描述>`：竖屏

### 插件相关命令

- `/video list` 或 `/video models`：列出可用模型
- `/video set <模型ID>`：切换运行时默认模型（仅管理员可用）
- `/video config`：查看当前配置与模型（仅管理员可用）
- `/video reset`：重置运行时覆盖（仅管理员可用）
- `/video help`：帮助说明

> 管理员用户需在 `components.admin_users` 中配置用户 ID。

## 配置说明（config.toml）

下面按配置段说明字段含义。

### [plugin]

- `name`：插件名称
- `config_version`：配置版本
- `enabled`：是否启用插件

### [components]

- `enable_debug_info`：是否向用户显示调试信息
- `command_model`：`/video` 默认模型 ID（例如 `model1`）
- `max_requests`：全局最大并发任务数
- `max_requests_per_user`：单用户最大并发任务数
- `rate_limit_window_seconds`：限流时间窗（秒）
- `max_requests_per_window`：单用户窗口内最大请求数
- `admin_users`：管理员用户 ID 列表（字符串数组）

### [logging]

- `level`：日志级别（`DEBUG`/`INFO`/`WARNING`/`ERROR`）
- `prefix`：日志前缀

### [proxy]

- `enabled`：是否启用代理
- `url`：代理地址（`http/https/socks5`）
- `timeout`：代理超时（秒）

### [api]

- `request_timeout_seconds`：API 请求超时
- `submit_max_retries`：提交请求最大重试次数
- `submit_backoff_seconds`：退避基准秒数（指数退避）
- `poll_interval_seconds`：轮询间隔
- `poll_timeout_seconds`：轮询超时上限
- `poll_max_attempts`：轮询最大次数（`0` 表示不限）

### [video]

- `max_prompt_length`：描述最大长度
- `max_image_mb`：输入图片最大大小（MB）
- `max_video_mb_for_base64`：下载视频转 base64 的最大大小（MB）
- `allow_url_send`：允许视频 URL 直发（发送更快；关闭则强制 base64 发送）
- `url_send_fallback_to_download`：URL 直发失败是否回退下载+base64

### [circuit_breaker]

- `enabled`：是否启用熔断
- `failure_threshold`：触发熔断的失败次数
- `recovery_seconds`：熔断恢复时间（秒）
- `half_open_max_success`：半开状态成功次数（达到后恢复）

### [image_uploader]

- `enabled`：是否启用对象存储临时上传图片
- `provider`：对象存储服务商（`oss`/`cos`/`r2`）
- `access_key_id`：Access Key ID
- `secret_access_key`：Secret Access Key
- `region`：区域
- `bucket_name`：桶名称
- `endpoint`：Endpoint（可选）

### [video_watch]

- `enabled`：是否启用「麦麦看视频」
- `visual_style`：视频描述提示词（建议中文、短句）
- `model_identifier`：看视频模型标识（当前仅支持 Gemini 系列）
- `client_type`：客户端类型（目前仅支持 `gemini`）
- `base_url`：Gemini 接口基础 URL
- `api_key`：Gemini API Key

### [models]

- 模型集合根节点（空对象即可）。
- 具体模型以 `models.<模型ID>` 形式配置，例如 `models.model1`。

### [models.model1]（示例）

- `name`：模型显示名称
- `base_url`：API 基础地址
- `api_key`：API 密钥
- `format`：API 格式（`openai`/`siliconflow`/`doubao`/`vectorengine`）
- `model`：模型名称/ID
- `support_option`：`1=仅文生`，`2=仅图生`，`3=文生+图生`
- `seconds`：视频时长（秒）
- `resolution`：分辨率（`480p`/`720p`/`1080p`）
- `watermark`：是否添加水印

## 示例配置片段

```toml
[plugin]
name = "mai_video_plugin"
config_version = "0.2.0"
enabled = true

[components]
command_model = "model1"
max_requests = 3
max_requests_per_user = 1
rate_limit_window_seconds = 120
max_requests_per_window = 3
admin_users = ["12345678"]

[models]

[models.model1]
name = "OpenAI Sora"
base_url = "https://api.openai.com/v1"
api_key = "YOUR_API_KEY"
format = "openai"
model = "sora-2"
support_option = "3"
seconds = "10"
resolution = "720p"
watermark = false
```


## 说明与提示

- 若 `allow_url_send=true` 但平台不支持直发视频，可开启 `url_send_fallback_to_download` 以确保发送成功（代价是更高内存占用）。
- 有些视频生成模型可能不支持base64格式图片上传或此格式图片上传质量差，可启用 `image_uploader`，请确保对象存储权限与临时 URL 有效期配置正确。
- 若启用 `video_watch`，需要具备可用的 Gemini API Key 与可识别视频的模型。注意：此麦麦看视频功能尚未完善，当你以生成的视频作为“引用消息”时，在麦麦提示词中该“引用消息”为视频文件信息而非视频描述。

## License

MIT
