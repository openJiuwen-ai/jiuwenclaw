# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import (
    ABC,
    abstractmethod,
)
from typing import (
    Any,
    AsyncIterator,
    Dict,
    List,
    Optional,
    Union,
)

from openjiuwen.core.common.clients.client_registry import get_client_registry
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import (
    llm_logger,
    LogEventType,
)
from openjiuwen.core.common.security.user_config import UserConfig
from openjiuwen.core.foundation.llm.output_parsers.output_parser import BaseOutputParser
from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig,
)
from openjiuwen.core.foundation.llm.schema.generation_response import (
    AudioGenerationResponse,
    ImageGenerationResponse,
    VideoGenerationResponse,
)
from openjiuwen.core.foundation.llm.schema.message import (
    AssistantMessage,
    BaseMessage,
    ToolMessage,
    UserMessage,
)
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.tool import ToolInfo


class BaseModelClient(ABC):
    """LLM Model Client Abstract Base Class

    All Model Client implementations must inherit from this class and implement the abstract methods.
    """
    __client_name__: str = None
    __client_type__: str = "llm"

    def __init_subclass__(cls, **kwargs):
        """Initialize subclass and register it if it's a client class.

        This method is called whenever a class inherits from BaseClient.
        It automatically registers any class that defines __client_name__
        and __client_type__ attributes with the global client registry.

        Args:
            **kwargs: Additional keyword arguments passed to the subclass.
        """
        super().__init_subclass__(**kwargs)

        # Skip registration for BaseClient itself (though this method won't be called for BaseClient)
        # Check if client name and type are defined
        if hasattr(cls, '__client_name__') and hasattr(cls, '__client_type__'):
            # Automatically register with the global registry
            if cls.__client_name__ is not None:  # Ensure it's actually set
                get_client_registry().register_class(cls)

    def __init__(self, model_config: ModelRequestConfig, model_client_config: ModelClientConfig):
        """Initialize Model Client

        Args:
            model_config: Model parameter configuration (temperature, top_p, model_name, etc.)
            config: Client configuration (api_key, api_base, timeout, etc.)
        """
        self.model_config = model_config
        self.model_client_config = model_client_config
        self._validate_config()

    def _get_client_name(self) -> str:
        """Get client name for error messages (subclasses can override)

        Returns:
            Client name string
        """
        return self.__class__.__name__

    def _validate_config(self):
        """Validate configuration parameters (subclasses can optionally override)"""
        client_name = self._get_client_name()

        if not self.model_client_config.api_key:
            raise build_error(StatusCode.MODEL_SERVICE_CONFIG_ERROR,
                              error_msg=f"model client config api_key is required for {client_name}.")
        if not self.model_client_config.api_base:
            raise build_error(StatusCode.MODEL_SERVICE_CONFIG_ERROR,
                              rror_msg=f"model client config api_base is required for {client_name}.")

        if self.model_client_config.verify_ssl is not None and not isinstance(self.model_client_config.verify_ssl,
                                                                              bool):
            raise build_error(StatusCode.MODEL_SERVICE_CONFIG_ERROR,
                              error_msg="model client config verify_ssl must be a boolean type.")

        if self.model_client_config.verify_ssl is True and self.model_client_config.ssl_cert is None:
            raise build_error(StatusCode.MODEL_SERVICE_CONFIG_ERROR,
                              error_msg="model client config ssl_cert is required when verify_ssl is True.")

    @staticmethod
    def _convert_messages_to_dict(messages: Union[str, List[BaseMessage], List[dict]]) -> List[dict]:
        """Convert messages to specific API format

        Args:
            messages: String or list of BaseMessage

        Returns:
            List[dict]: Converted message list
        """
        """Convert to OpenAI format: [{"role": "user", "content": "..."}]

           Args:
               messages: 
                    String or list of BaseMessage

               Returns:
                    Message list in OpenAI API format
        """
        # If it's a string, convert to user message
        if not messages:
            raise build_error(StatusCode.MODEL_INVOKE_PARAM_ERROR,
                              error_msg="The message sent to the llm cannot be empty.")
        if isinstance(messages, str):
            return [{"role": "user", "content": messages}]

        if all(isinstance(item, dict) for item in messages):
            return messages

        # Convert BaseMessage list
        result = []
        for msg in messages:
            msg_dict = {"role": msg.role, "content": msg.content}

            # Handle tool_calls for AssistantMessage
            if isinstance(msg, AssistantMessage) and msg.tool_calls:
                tool_calls_list = []
                for tc in msg.tool_calls:
                    tool_calls_list.append({
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.name,
                            "arguments": tc.arguments
                        }
                    })
                msg_dict["tool_calls"] = tool_calls_list

            # Handle tool_call_id for ToolMessage
            if isinstance(msg, ToolMessage):
                msg_dict["tool_call_id"] = msg.tool_call_id

            result.append(msg_dict)

        return result

    @staticmethod
    def _convert_tools_to_dict(tools: Union[List[ToolInfo], List[dict], None]) -> Optional[List[dict]]:
        """Convert to OpenAI format: [{"type": "function", "function": {...}}]

        Args:
            tools: List of ToolInfo or None

        Returns:
            Tool list in OpenAI API format
        """
        if not tools:
            return None

        if all(isinstance(item, dict) for item in tools):
            return tools

        result = []
        for tool in tools:
            # Handle parameters (could be dict or BaseModel)
            if hasattr(tool.parameters, 'model_dump'):
                # If it's a Pydantic BaseModel
                parameters = tool.parameters.model_dump()
            else:
                # If it's already a dict
                parameters = tool.parameters

            tool_dict = {
                "type": tool.type,
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": parameters
                }
            }
            result.append(tool_dict)

        return result

    def _build_request_params(
            self,
            *,
            messages: Union[str, List[BaseMessage], List[dict]],
            tools: Union[List[ToolInfo], List[dict], None],
            temperature: Optional[float],
            top_p: Optional[float],
            model: Optional[str],
            stop: Union[Optional[str], None],
            max_tokens: Optional[int],
            stream: bool,
            **kwargs
    ) -> Dict[str, Any]:
        """Build OpenAI-compatible chat completion request parameters.

        Note:
            Most OpenAI-compatible providers use the same request payload. Subclasses can call
            this method and then apply provider-specific adjustments (e.g. additional fields).
        """
        if model is None and self.model_config.model_name is None:
            raise build_error(
                StatusCode.MODEL_CONFIG_ERROR.code,
                StatusCode.MODEL_CONFIG_ERROR.errmsg.format(error_msg="The model cannot be None.")
            )

        # Convert message format
        messages_dict = self._convert_messages_to_dict(messages)

        # Build basic parameters
        params: Dict[str, Any] = {
            "model": model if model else self.model_config.model_name,
            "messages": messages_dict,
            "stream": stream,
        }

        # Add temperature: prioritize parameter, otherwise use model_config, only add when not None
        final_temperature = temperature if temperature is not None else self.model_config.temperature
        if final_temperature is not None:
            params["temperature"] = final_temperature

        # Add top_p: prioritize parameter, otherwise use model_config, only add when not None
        final_top_p = top_p if top_p is not None else self.model_config.top_p
        if final_top_p is not None:
            params["top_p"] = final_top_p

        # Add max_tokens: prioritize parameter, otherwise use model_config, only add when not None
        final_max_tokens = max_tokens if max_tokens is not None else self.model_config.max_tokens
        if final_max_tokens is not None:
            params["max_tokens"] = final_max_tokens

        # Add stop: prioritize parameter, otherwise use model_config, only add when not None
        final_stop = stop if stop is not None else self.model_config.stop
        if final_stop is not None:
            params["stop"] = final_stop

        # Add tools
        tools_dict = self._convert_tools_to_dict(tools)
        if tools_dict:
            params["tools"] = tools_dict
            params["tool_choice"] = "auto"

        # Add other parameters (filter out internal parameters)
        # parser and output_parser are for internal use and should not be passed to model API
        internal_params = {"parser", "output_parser"}

        # Get all fields from model_config (including extra fields)
        extra_params = self.model_config.model_dump(
            exclude={"model_name", "model", "temperature", "top_p", "max_tokens", "stop"},
            exclude_none=True
        )
        params.update(extra_params)

        # Then add kwargs parameters (will override model_config params with same key)
        filtered_kwargs = {k: v for k, v in kwargs.items() if k not in internal_params}
        params.update(filtered_kwargs)

        # Logging
        client_name = self._get_client_name()
        if UserConfig.is_sensitive():
            llm_logger.info(
                "Before request chat model, LLM request params ready.",
                event_type=LogEventType.LLM_CALL_START,
                model_name=model if model else self.model_config.model_name,
                model_provider=self.model_client_config.client_provider,
                temperature=final_temperature,
                top_p=final_top_p,
                max_tokens=final_max_tokens,
                is_stream=stream,
                stop=final_stop,
                metadata={"client_name": client_name},
                extra_params=extra_params
            )
        else:
            llm_logger.info(
                "Before request chat model, LLM request params ready.",
                event_type=LogEventType.LLM_CALL_START,
                model_name=model if model else self.model_config.model_name,
                model_provider=self.model_client_config.client_provider,
                messages=messages_dict,
                tools=tools_dict,
                temperature=final_temperature,
                top_p=final_top_p,
                max_tokens=final_max_tokens,
                is_stream=stream,
                metadata={"client_name": client_name},
                extra_params=extra_params
            )

        return params

    @abstractmethod
    async def invoke(
            self,
            messages: Union[str, List[BaseMessage], List[dict]],
            *,
            tools: Union[List[ToolInfo], List[dict], None] = None,
            temperature: Optional[float] = None,
            top_p: Optional[float] = None,
            model: str = None,
            max_tokens: Optional[int] = None,
            stop: Union[Optional[str], None] = None,
            output_parser: Optional[BaseOutputParser] = None,
            timeout: float = None,
            **kwargs
    ) -> AssistantMessage:
        """Asynchronously invoke LLM

        Args:
            :param output_parser:
            :param model:
            :param stop:
            :param temperature:
            :param tools:
            :param messages:
            :param top_p:
            :param max_tokens:
            :param timeout:
            **kwargs: Additional parameters

        Returns:
            AssistantMessage: Model response
        """
        pass

    @abstractmethod
    async def stream(
            self,
            messages: Union[str, List[BaseMessage], List[dict]],
            *,
            tools: Union[List[ToolInfo], List[dict], None] = None,
            temperature: Optional[float] = None,
            top_p: Optional[float] = None,
            model: str = None,
            max_tokens: Optional[int] = None,
            stop: Union[Optional[str], None] = None,
            output_parser: Optional[BaseOutputParser] = None,
            timeout: float = None,
            **kwargs
    ) -> AsyncIterator[AssistantMessageChunk]:
        """Asynchronously stream invoke LLM

        Args:
            :param output_parser:
            :param model:
            :param stop:
            :param temperature:
            :param tools:
            :param messages:
            :param top_p:
            :param max_tokens:
            :param timeout:
            **kwargs: Additional parameters

        Yields:
            AssistantMessageChunk: Streaming response chunk
        """
        pass

    @abstractmethod
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
        """Generate image from text prompt (text-to-image or text+image-to-image)

        Args:
            prompt: Text description of the image to generate or edit
            image_url: Optional base image URL for image-to-image generation (editing/variations)
            model: Model to use for generation
            size: Size of the generated image (e.g., "1024x1024", "512x512")
            quality: Quality of the generated image ("standard" or "hd")
            n: Number of images to generate
            timeout: Request timeout in seconds
            **kwargs: Additional parameters

        Returns:
            ImageGenerationResponse: Generated image response
        """
        pass

    @abstractmethod
    async def generate_speech(
            self,
            messages: List[UserMessage],
            *,
            model: Optional[str] = None,
            voice: Optional[str] = "Cherry",
            language_type: Optional[str] = "Auto",
            **kwargs
    ) -> AudioGenerationResponse:
        """Generate speech audio from text

        Args:
            prompt: Text to convert to speech
            model: Model to use for generation
            voice: Voice to use (e.g., "alloy", "echo", "fable", "onyx", "nova", "shimmer")
            speed: Speed of the generated audio (0.25 to 4.0)
            response_format: Audio format ("mp3", "opus", "aac", "flac")
            timeout: Request timeout in seconds
            **kwargs: Additional parameters

        Returns:
            AudioGenerationResponse: Generated audio response
        """
        pass

    @abstractmethod
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
        """Generate video from text prompt (text-to-video or image-to-video)

        Args:
            messages: List of UserMessage containing text description of the video to generate
            img_url: Optional URL/path of the first frame image for image-to-video generation.
                     Supports: public URL, local file path (file:// prefix), or base64 encoded image
            audio_url: Optional URL of audio to add to the video
            model: Model to use for generation
            size: Video size (e.g., "1280*720"). Use '*' as separator.
            resolution: Video resolution (e.g., "720P", "1080P")
            duration: Duration of the video in seconds (default: 5)
            prompt_extend: Whether to automatically extend/enhance the prompt (default: True)
            watermark: Whether to add watermark to generated video (default: False)
            negative_prompt: Negative prompt to guide what not to generate
            seed: Random seed for reproducible generation
            **kwargs: Additional parameters

        Returns:
            VideoGenerationResponse: Generated video response
        """
        pass
