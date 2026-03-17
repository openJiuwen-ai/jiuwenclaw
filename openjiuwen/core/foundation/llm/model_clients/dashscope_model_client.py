# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Optional, List

import dashscope
from dashscope import MultiModalConversation, VideoSynthesis

from openjiuwen.core.common.exception.errors import ValidationError, ModelError
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.schema.message import UserMessage
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig, ProviderType
from openjiuwen.core.foundation.llm.schema.generation_response import (
    ImageGenerationResponse,
    AudioGenerationResponse,
    VideoGenerationResponse
)


DASHSCOPE_VOICE = ["Cherry", "Serena", "Ethan", "Chelsie", "Momo", "Vivian", "Moon", "Maia", "Kai", "Nofish",
                   "Bella", "Jennifer", "Ryan", "Katerina", "Aiden", "Eldric Sage", "Mia", "Mochi", "Bellona",
                   "Vincent", "Bunny", "Neil", "Elias", "Arthur", "Nini" "Ebona", "Seren", "Pip", "Stella", "Bodega",
                   "Sonrisa", "Alek", "Dolce", "Sohee", "Ono Anna", "Lenn", "Emilien", "Andre", "Radio Gol", "Jada",
                   "Dylan", "Li", "Marcus", "Roy", "Peter", "Sunny", "Eric", "Rocky", "Kiki"]

DASHSCOPE_LANGUAGE_TYPE = [
    "Chinese", "English", "German", "Italian", "Portuguese",
    "Spanish", "Japanese", "Korean", "French", "Russian"]


class DashScopeModelClient(OpenAIModelClient):
    """Alibaba Cloud DashScope Model Client
    
    This client extends OpenAIModelClient to support DashScope-specific multimodal generation APIs.
    DashScope (通义千问) provides text-to-image, text-to-speech, and text-to-video capabilities
    through Alibaba Cloud's proprietary APIs.
    
    For chat completions, it inherits all functionality from OpenAIModelClient since DashScope
    provides OpenAI-compatible chat API endpoints.
    """
    __client_name__ = ProviderType.DashScope.name

    def __init__(self, model_config: ModelRequestConfig, model_client_config: ModelClientConfig):
        super().__init__(model_config, model_client_config)

    def _get_client_name(self) -> str:
        """Get client name."""
        return "DashScope client"

    async def generate_image(
            self,
            messages: List[UserMessage],
            *,
            model: Optional[str] = None,
            size: Optional[str] = "1664*928",
            negative_prompt: Optional[str] = None,
            n: Optional[int] = 1,
            prompt_extend: bool = True,
            watermark: bool = False,
            seed: int = 0,
            **kwargs
    ) -> ImageGenerationResponse:
        """Generate image using DashScope Wanx (通义万相) API
        
        DashScope provides text-to-image generation through the Wanx service.
        
        Args:
            messages: List of messages, must only contain UserMessage type
            model: Model to use (e.g., "qwen-image-max", "wanx-v1")
            size: Size of the generated image (e.g., "1664*928", "1024*1024")
            negative_prompt: Negative prompt to avoid certain features in the image
            n: Number of images to generate (default: 1)
            prompt_extend: Whether to extend the prompt (default: True)
            watermark: Whether to add watermark (default: False)
            seed: Random seed for reproducibility (default: 0)
            **kwargs: Additional DashScope-specific parameters
            
        Returns:
            ImageGenerationResponse: Generated image response
            
        Raises:
            JiuWenBaseException: If messages contain non-UserMessage types or validation fails
        """
        try:
            # (1) Validate messages parameter - must have exactly one UserMessage
            if not messages or len(messages) != 1:
                raise ValidationError(
                    StatusCode.MODEL_INVOKE_PARAM_ERROR,
                    msg=f"Image generation requires exactly one message, but got {len(messages) if messages else 0}."
                )

            if not isinstance(messages[0], UserMessage):
                raise ValidationError(
                    StatusCode.MODEL_INVOKE_PARAM_ERROR,
                    msg=f"Image generation requires a UserMessage, but got {type(messages[0]).__name__}."
                )

            # (2) Validate and convert message content to DashScope format
            msg = messages[0]
            content_list = []
            image_count = 0
            text_count = 0

            # Handle content: can be string or list of dicts
            if isinstance(msg.content, str):
                # Simple text prompt
                content_list.append({"text": msg.content})
                text_count = 1
            elif isinstance(msg.content, list):
                # Complex content with text and/or images
                for item in msg.content:
                    if isinstance(item, str):
                        content_list.append({"text": item})
                        text_count += 1
                    elif isinstance(item, dict):
                        # Validate dict structure
                        if "text" in item:
                            content_list.append({"text": item["text"]})
                            text_count += 1
                        elif "image" in item:
                            content_list.append({"image": item["image"]})
                            image_count += 1
                        else:
                            raise ValidationError(
                                StatusCode.MODEL_INVOKE_PARAM_ERROR,
                                msg=f"Content dict must contain 'text' or 'image' key, but got: {list(item.keys())}"
                            )
                    else:
                        raise ValidationError(
                            StatusCode.MODEL_INVOKE_PARAM_ERROR,
                            msg=f"Content item must be string or dict, but got {type(item).__name__}."
                        )
            else:
                raise ValidationError(
                    StatusCode.MODEL_INVOKE_PARAM_ERROR,
                    msg=f"Message content must be string or list, but got {type(msg.content).__name__}."
                )

            # Validate content requirements
            if text_count == 0:
                raise ValidationError(
                    StatusCode.MODEL_INVOKE_PARAM_ERROR,
                    msg="Image generation requires at least one text prompt."
                )

            if image_count > 3:
                raise ValidationError(
                    StatusCode.MODEL_INVOKE_PARAM_ERROR,
                    msg=f"Image generation supports at most 3 input images, but got {image_count}."
                )

            dashscope_messages = [{
                "role": msg.role,
                "content": content_list
            }]

            # Use default model if not specified
            if model is None:
                model = self.model_config.model_name

            # Prepare API parameters
            api_params = {
                "api_key": self.model_client_config.api_key,
                "model": model,
                "messages": dashscope_messages,
                "result_format": "message",
                "stream": False,
                "watermark": watermark,
                "prompt_extend": prompt_extend,
                "size": size,
                "n": n,
            }

            # Add optional parameters
            if negative_prompt:
                api_params["negative_prompt"] = negative_prompt

            if seed:
                api_params["seed"] = seed

            # Add any additional kwargs
            api_params.update(kwargs)

            # Log request
            logger.info(
                f"Calling DashScope image generation API with model: {model}, size: {size}"
            )

            # Call DashScope API
            dashscope.base_http_api_url = self.model_client_config.api_base

            response = MultiModalConversation.call(**api_params)

            # Handle response
            if response.status_code != 200:
                error_msg = (
                    f"DashScope image generation failed. "
                    f"HTTP status: {response.status_code}, "
                    f"Error code: {response.code}, "
                    f"Error message: {response.message}"
                )
                logger.error(error_msg)
                raise ModelError(
                    StatusCode.MODEL_CALL_FAILED,
                    msg=error_msg
                )

            # Extract image URLs from response
            image_urls = []
            if response.output and response.output.get("choices"):
                for choice in response.output["choices"]:
                    if choice.get("message") and choice["message"].get("content"):
                        for content_item in choice["message"]["content"]:
                            if isinstance(content_item, dict) and "image" in content_item:
                                image_urls.append(content_item["image"])

            if not image_urls:
                raise ModelError(
                    StatusCode.MODEL_CALL_FAILED,
                    msg="No images returned from DashScope API."
                )

            # Log success
            logger.info(
                f"DashScope image generation succeeded. Generated {len(image_urls)} image(s)."
            )

            # Return ImageGenerationResponse
            return ImageGenerationResponse(
                model=model,
                images=image_urls,
                created=None  # DashScope doesn't provide creation timestamp
            )

        except Exception as e:
            error_msg = f"Unexpected error during DashScope image generation: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise ModelError(
                StatusCode.MODEL_CALL_FAILED,
                msg=error_msg,
                cause=e
            ) from e

    async def generate_speech(
            self,
            messages: List[UserMessage],
            *,
            model: Optional[str] = None,
            voice: Optional[str] = "Cherry",
            language_type: Optional[str] = "Auto",
            **kwargs
    ) -> AudioGenerationResponse:
        """Generate speech using DashScope Cosyvoice API
        
        DashScope provides text-to-speech generation through the Cosyvoice service.
        
         Args:
            messages: List of UserMessage containing text to convert to speech
            model: Model to use for generation
            voice: Voice to use for speech synthesis (required), refer to supported voices
            language_type: Language type for synthesized audio, defaults to "Auto" for automatic detection
            **kwargs: Additional parameters

        Returns:
            AudioGenerationResponse: Generated audio response
            
        Raises:
            JiuWenBaseException: If messages contain non-UserMessage types or validation fails
        """
        try:
            # (1) Validate messages parameter - must have at least one UserMessage
            if not messages or len(messages) > 1:
                raise ValidationError(
                    StatusCode.MODEL_INVOKE_PARAM_ERROR,
                    msg="Speech generation requires at least one message, but got 0."
                )

            # Validate all messages are UserMessage type
            for idx, msg in enumerate(messages):
                if not isinstance(msg, UserMessage):
                    raise ValidationError(
                        StatusCode.MODEL_INVOKE_PARAM_ERROR,
                        msg=f"Speech generation requires UserMessage types,"
                            f" but message at index {idx} is {type(msg).__name__}."
                    )

            if len(messages) > 1:
                pass

            text_to_synthesize = messages[0].content

            if not text_to_synthesize or not text_to_synthesize.strip():
                raise ValidationError(
                    StatusCode.MODEL_INVOKE_PARAM_ERROR,
                    msg="Speech generation requires non-empty text content."
                )

            if model is None:
                model = self.model_config.model_name

            dashscope.base_http_api_url = self.model_client_config.api_base

            # Prepare API parameters
            api_params = {
                "api_key": self.model_client_config.api_key,
                "model": model,
                "text": text_to_synthesize,
                "voice": voice,
                "language_type": language_type,
            }

            # Add any additional kwargs
            api_params.update(kwargs)

            # Log request
            logger.info(
                f"Calling DashScope speech generation API with model: {model},"
                f" voice: {voice}, language: {language_type}"
            )

            # Call DashScope API
            response = MultiModalConversation.call(**api_params)

            # Handle response
            if response.status_code != 200:
                error_msg = (
                    f"DashScope speech generation failed. "
                    f"HTTP status: {response.status_code}, "
                    f"Error code: {response.code}, "
                    f"Error message: {response.message}"
                )
                logger.error(error_msg)
                raise ModelError(
                    StatusCode.MODEL_CALL_FAILED,
                    msg=error_msg
                )

            # Extract audio information from response
            # Response format: response.output.audio.url, response.output.audio.data
            audio_url = None
            audio_data = None
            audio_format = None

            if response.output and response.output.get("audio"):
                audio_info = response.output["audio"]
                audio_url = audio_info.get("url")
                audio_data_str = audio_info.get("data")

                # Convert audio data string to bytes if present
                if audio_data_str:
                    audio_data = audio_data_str.encode('utf-8') if isinstance(audio_data_str, str) else audio_data_str

                # Infer audio format from URL extension
                if audio_url:
                    if audio_url.endswith('.wav'):
                        audio_format = "wav"
                    elif audio_url.endswith('.mp3'):
                        audio_format = "mp3"
                    elif audio_url.endswith('.pcm'):
                        audio_format = "pcm"

            if not audio_url and not audio_data:
                raise ModelError(
                    StatusCode.MODEL_CALL_FAILED,
                    msg="No audio URL or data returned from DashScope API."
                )

            # Log success
            logger.info(
                f"DashScope speech generation succeeded. Audio format: {audio_format or 'unknown'}, "
                f"URL present: {bool(audio_url)}, Data present: {bool(audio_data)}"
            )

            # Return AudioGenerationResponse
            return AudioGenerationResponse(
                model=model,
                audio_url=audio_url,
                audio_data=audio_data,
                format=audio_format
            )

        except Exception as e:
            error_msg = f"Unexpected error during DashScope speech generation: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise ModelError(
                StatusCode.MODEL_CALL_FAILED,
                msg=error_msg,
                cause=e
            ) from e

    async def generate_video(
            self,
            messages: List[UserMessage],
            *,
            img_url: Optional[str] = None,
            audio_url: Optional[str] = None,
            model: Optional[str] = None,
            size: Optional[str] = None,
            resolution: Optional[str] = None,
            duration: Optional[int] = 5,
            prompt_extend: bool = True,
            watermark: bool = False,
            negative_prompt: Optional[str] = None,
            seed: Optional[int] = None,
            **kwargs
    ) -> VideoGenerationResponse:
        """Generate video using DashScope video generation API
        
        DashScope provides text-to-video (t2v) and image-to-video (i2v) generation capabilities.
        When img_url is provided, it performs image-to-video generation; otherwise, text-to-video.
        
        Args:
            messages: List of UserMessage containing text description of the video to generate
            img_url: Optional URL/path of the first frame image for image-to-video generation.
                     Supports: public URL, local file path (file:// prefix), or base64 encoded image
            audio_url: Optional URL of audio to add to the video
            model: Model to use (e.g., "wan2.6-t2v" for text-to-video, "wan2.6-i2v-flash" for image-to-video)
            size: Video size for text-to-video (e.g., "1280*720"). Use '*' as separator.
            resolution: Video resolution for image-to-video (e.g., "720P", "1080P")
            duration: Duration of the video in seconds (default: 5)
            prompt_extend: Whether to automatically extend/enhance the prompt (default: True)
            watermark: Whether to add watermark to generated video (default: False)
            negative_prompt: Negative prompt to guide what not to generate
            seed: Random seed for reproducible generation
            **kwargs: Additional DashScope-specific parameters
            
        Returns:
            VideoGenerationResponse: Generated video response containing video_url
            
        Raises:
            JiuWenBaseException: If messages contain non-UserMessage types or validation fails
        """
        try:
            # (1) Validate messages parameter - must have exactly one UserMessage
            if not messages or len(messages) != 1:
                raise ValidationError(
                    StatusCode.MODEL_INVOKE_PARAM_ERROR,
                    msg=f"Video generation requires exactly one message, but got {len(messages) if messages else 0}."
                )

            # Validate message is UserMessage type
            if not isinstance(messages[0], UserMessage):
                raise ValidationError(
                    StatusCode.MODEL_INVOKE_PARAM_ERROR,
                    msg=f"Video generation requires UserMessage type, but got {type(messages[0]).__name__}."
                )

            # Extract prompt from message content
            prompt = messages[0].content

            # Validate prompt
            if not prompt or not prompt.strip():
                raise ValidationError(
                    StatusCode.MODEL_INVOKE_PARAM_ERROR,
                    msg="Video generation requires non-empty text content."
                )

            # Use default model if not specified
            if model is None:
                model = self.model_config.model_name

            # Set DashScope base URL
            dashscope.base_http_api_url = self.model_client_config.api_base

            # Build API parameters
            api_params = {
                "api_key": self.model_client_config.api_key,
                "model": model,
                "prompt": prompt,
                "prompt_extend": prompt_extend,
                "watermark": watermark,
            }

            # Add duration if specified
            if duration is not None:
                api_params["duration"] = duration

            # Add negative prompt if specified
            if negative_prompt:
                api_params["negative_prompt"] = negative_prompt

            # Add seed if specified
            if seed is not None:
                api_params["seed"] = seed

            # Add audio URL if specified
            if audio_url:
                api_params["audio_url"] = audio_url

            # Determine if this is image-to-video or text-to-video
            if img_url:
                # Image-to-video generation
                api_params["img_url"] = img_url
                # For i2v, use resolution parameter (e.g., "720P")
                if resolution:
                    api_params["resolution"] = resolution
                elif size:
                    # If only size is provided, try to convert to resolution
                    api_params["size"] = size
                
                logger.info(
                    f"Calling DashScope image-to-video generation API with model: {model}, "
                    f"resolution: {resolution or size}, duration: {duration}"
                )
            else:
                # Text-to-video generation
                # For t2v, use size parameter (e.g., "1280*720")
                if size:
                    api_params["size"] = size
                elif resolution:
                    # If only resolution is provided, use it
                    api_params["resolution"] = resolution
                
                logger.info(
                    f"Calling DashScope text-to-video generation API with model: {model}, "
                    f"size: {size or resolution}, duration: {duration}"
                )

            # Add any additional kwargs
            api_params.update(kwargs)

            # Call DashScope VideoSynthesis API
            response = VideoSynthesis.call(**api_params)

            # Handle response
            if response.status_code != 200:
                error_msg = (
                    f"DashScope video generation failed. "
                    f"HTTP status: {response.status_code}, "
                    f"Error code: {response.code}, "
                    f"Error message: {response.message}"
                )
                logger.error(error_msg)
                raise ModelError(
                    StatusCode.MODEL_CALL_FAILED,
                    msg=error_msg
                )

            # Extract video URL from response
            video_url = None
            video_duration = None
            video_resolution = None

            if response.output:
                video_url = getattr(response.output, 'video_url', None)
                
            if response.usage:
                video_duration = response.usage.get('duration') or response.usage.get('output_video_duration')
                video_resolution = response.usage.get('size')

            if not video_url:
                raise ModelError(
                    StatusCode.MODEL_CALL_FAILED,
                    msg="No video URL returned from DashScope API."
                )

            # Log success
            logger.info(
                f"DashScope video generation succeeded. Video URL: {video_url[:100]}..."
            )

            # Return VideoGenerationResponse
            return VideoGenerationResponse(
                model=model,
                video_url=video_url,
                duration=video_duration,
                resolution=video_resolution,
                format="mp4"
            )
        except Exception as e:
            error_msg = f"Unexpected error during DashScope video generation: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise ModelError(
                StatusCode.MODEL_CALL_FAILED,
                msg=error_msg,
                cause=e
            ) from e
