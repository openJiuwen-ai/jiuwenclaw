# 多模态生成接口使用指南

本文档介绍如何使用图片生成、语音生成和视频生成接口。

## 概述

我们在以下文件中添加了三个新的生成接口：
- `openjiuwen/core/foundation/llm/model.py` - 统一的模型调用入口
- `openjiuwen/core/foundation/llm/model_clients/base_model_client.py` - 抽象基类
- `openjiuwen/core/foundation/llm/model_clients/openai_model_client.py` - OpenAI 客户端实现
- `openjiuwen/core/foundation/llm/model_clients/siliconflow_model_client.py` - SiliconFlow 客户端实现

## 响应类型

所有生成接口的响应类型定义在 `openjiuwen/core/foundation/llm/schema/generation_response.py`：

- `ImageGenerationResponse` - 图片生成响应
- `AudioGenerationResponse` - 语音生成响应
- `VideoGenerationResponse` - 视频生成响应

## 1. 图片生成接口

### 功能说明
支持两种模式：
- **文生图（Text-to-Image）**：从文本描述生成图片
- **文+图生图（Text+Image-to-Image）**：基于原图和文本描述生成新图片（编辑/变体）

### 方法签名
```python
async def generate_image(
    prompt: str,
    *,
    image_url: Optional[str] = None,
    model: Optional[str] = None,
    size: Optional[str] = "1024x1024",
    quality: Optional[str] = "standard",
    n: Optional[int] = 1,
    timeout: Optional[float] = None,
    **kwargs
) -> ImageGenerationResponse
```

### 参数说明
- `prompt`: 图片描述文本（必需）
- `image_url`: 原图 URL，用于图片编辑/变体（可选，提供时为文+图生图模式）
- `model`: 使用的模型（可选，如 "dall-e-3", "dall-e-2"）
- `size`: 图片尺寸（可选，如 "1024x1024", "512x512"）
- `quality`: 图片质量（可选，"standard" 或 "hd"）
- `n`: 生成图片数量（可选，默认 1）
- `timeout`: 请求超时时间（可选）

### 使用示例

#### 文生图模式
```python
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig, ModelClientConfig

# 配置模型客户端
model_client_config = ModelClientConfig(
    client_provider="OpenAI",
    client_id="openai_client_1",
    api_key="your-api-key",
    api_base="https://api.openai.com/v1"
)

model_config = ModelRequestConfig(
    model_name="dall-e-3"
)

# 创建模型实例
model = Model(model_client_config, model_config)

# 纯文本生成图片
response = await model.generate_image(
    prompt="一只可爱的橙色小猫在草地上玩耍",
    size="1024x1024",
    quality="hd",
    n=1
)

# 获取生成的图片 URL
for image_url in response.images:
    print(f"生成的图片: {image_url}")
```

#### 文+图生图模式
```python
# 基于原图生成变体或编辑
response = await model.generate_image(
    prompt="将小猫的颜色改为蓝色",
    image_url="https://example.com/original-cat.jpg",
    size="1024x1024",
    quality="hd"
)

for image_url in response.images:
    print(f"编辑后的图片: {image_url}")
```

## 2. 语音生成接口

### 功能说明
将文本转换为语音（TTS - Text-to-Speech）

### 方法签名
```python
async def generate_speech(
    prompt: str,
    *,
    model: Optional[str] = None,
    voice: Optional[str] = "alloy",
    speed: Optional[float] = 1.0,
    response_format: Optional[str] = "mp3",
    timeout: Optional[float] = None,
    **kwargs
) -> AudioGenerationResponse
```

### 参数说明
- `prompt`: 要转换为语音的文本（必需）
- `model`: 使用的模型（可选，如 "tts-1", "tts-1-hd"）
- `voice`: 声音选择（可选，如 "alloy", "echo", "fable", "onyx", "nova", "shimmer"）
- `speed`: 语速（可选，0.25 到 4.0，默认 1.0）
- `response_format`: 音频格式（可选，"mp3", "opus", "aac", "flac"）
- `timeout`: 请求超时时间（可选）

### 使用示例
```python
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig, ModelClientConfig

# 配置模型客户端
model_client_config = ModelClientConfig(
    client_provider="OpenAI",
    client_id="openai_client_1",
    api_key="your-api-key",
    api_base="https://api.openai.com/v1"
)

model_config = ModelRequestConfig(
    model_name="tts-1"
)

# 创建模型实例
model = Model(model_client_config, model_config)

# 生成语音
response = await model.generate_speech(
    prompt="你好，欢迎使用语音生成功能！",
    voice="nova",
    speed=1.0,
    response_format="mp3"
)

# 保存音频文件
with open("output.mp3", "wb") as f:
    f.write(response.audio_data)
    
print(f"音频格式: {response.format}")
print(f"使用模型: {response.model}")
```

## 3. 视频生成接口

### 功能说明
支持两种模式：
- **文生视频（Text-to-Video）**：从文本描述生成视频
- **文+图生视频（Text+Image-to-Video）**：基于首帧图片和文本描述生成视频

注意：OpenAI 当前不支持视频生成功能

### 方法签名
```python
async def generate_video(
    prompt: str,
    *,
    first_frame_url: Optional[str] = None,
    model: Optional[str] = None,
    duration: Optional[float] = None,
    resolution: Optional[str] = "1920x1080",
    fps: Optional[int] = 30,
    timeout: Optional[float] = None,
    **kwargs
) -> VideoGenerationResponse
```

### 参数说明
- `prompt`: 视频描述文本（必需）
- `first_frame_url`: 首帧图片 URL，用于图生视频（可选，提供时为文+图生视频模式）
- `model`: 使用的模型（可选）
- `duration`: 视频时长（秒，可选）
- `resolution`: 视频分辨率（可选，如 "1920x1080", "1280x720"）
- `fps`: 帧率（可选，默认 30）
- `timeout`: 请求超时时间（可选）

### 使用示例

#### 文生视频模式
```python
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig, ModelClientConfig

# 配置模型客户端（注意：需要支持视频生成的提供商）
model_client_config = ModelClientConfig(
    client_provider="YourVideoProvider",  # 替换为支持视频生成的提供商
    client_id="video_client_1",
    api_key="your-api-key",
    api_base="https://api.example.com/v1"
)

model_config = ModelRequestConfig(
    model_name="video-model"
)

# 创建模型实例
model = Model(model_client_config, model_config)

# 纯文本生成视频
try:
    response = await model.generate_video(
        prompt="一只小鸟在蓝天中自由飞翔",
        duration=5.0,
        resolution="1920x1080",
        fps=30
    )
    
    # 获取视频 URL 或数据
    if response.video_url:
        print(f"生成的视频: {response.video_url}")
    elif response.video_data:
        with open("output.mp4", "wb") as f:
            f.write(response.video_data)
        print("视频已保存到 output.mp4")
        
except Exception as e:
    print(f"视频生成失败: {e}")
```

#### 文+图生视频模式
```python
# 基于首帧图片生成视频
try:
    response = await model.generate_video(
        prompt="小鸟展翅飞向天空",
        first_frame_url="https://example.com/bird-first-frame.jpg",
        duration=5.0,
        resolution="1920x1080"
    )
    
    if response.video_url:
        print(f"生成的视频: {response.video_url}")
        
except Exception as e:
    print(f"视频生成失败: {e}")
```

## 提供商支持情况

| 接口 | OpenAI | SiliconFlow | 说明 |
|------|--------|-------------|------|
| 图片生成 | ✅ 支持 | ❌ 未实现 | OpenAI 使用 DALL-E 模型 |
| 语音生成 | ✅ 支持 | ❌ 未实现 | OpenAI 使用 TTS 模型 |
| 视频生成 | ❌ 不支持 | ❌ 未实现 | 当前主流提供商尚不支持 |

## 错误处理

所有接口在出错时都会抛出 `JiuWenBaseException` 异常。建议使用 try-except 块进行错误处理：

```python
from openjiuwen.core.common.exception.exception import JiuWenBaseException

try:
    response = await model.generate_image(
        prompt="一幅美丽的风景画"
    )
except JiuWenBaseException as e:
    print(f"生成失败: {e.message}")
```

## 扩展新的提供商

如果需要添加新的提供商支持（如其他视频生成服务），请：

1. 继承 `BaseModelClient` 类
2. 实现三个抽象方法：`generate_image`, `generate_speech`, `generate_video`
3. 在 `model.py` 的 `_CLIENT_TYPE_REGISTRY` 中注册新的客户端类

示例：
```python
class CustomVideoModelClient(BaseModelClient):
    async def generate_video(self, prompt, **kwargs):
        # 实现自定义视频生成逻辑
        pass
```

## 注意事项

1. **API 密钥安全**：请妥善保管 API 密钥，不要在代码中硬编码
2. **成本控制**：生成接口（特别是视频）可能消耗较多 token，请注意成本
3. **超时设置**：生成任务可能耗时较长，建议设置合理的超时时间
4. **内容审核**：生成的内容应符合相关法律法规和平台规范
5. **格式支持**：不同提供商支持的格式可能不同，请查阅具体文档

## 更新日志

- 2025-01-19: 添加图片生成、语音生成和视频生成接口
  - 在 `BaseModelClient` 中定义抽象方法
  - 在 `Model` 类中添加调用方法
  - 在 `OpenAIModelClient` 中实现图片和语音生成
  - 创建响应类型 schema

