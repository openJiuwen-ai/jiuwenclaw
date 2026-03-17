# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class GenerationResponse(BaseModel):
    """Generation response"""

    model: Optional[str] = Field(default=None, description="Model used for generation")


class ImageGenerationResponse(GenerationResponse):
    """Image generation response"""

    images: List[str] = Field(default=None, description="List of generated image URLs")
    images_base64: List[str] = Field(default=None, description="List of generated image in base64")
    created: Optional[int] = Field(default=None, description="Timestamp of creation")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class AudioGenerationResponse(GenerationResponse):
    """Audio/Speech generation response"""

    audio_url: Optional[str] = Field(default=None, description="URL of the generated audio")
    audio_data: Optional[bytes] = Field(default=None, description="Binary audio data")
    duration: Optional[float] = Field(default=None, description="Duration in seconds")
    format: Optional[str] = Field(default="mp3", description="Audio format (mp3, wav, etc.)")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class VideoGenerationResponse(GenerationResponse):
    """Video generation response"""

    video_url: Optional[str] = Field(default=None, description="URL of the generated video")
    video_data: Optional[bytes] = Field(default=None, description="Binary video data")
    duration: Optional[float] = Field(default=None, description="Duration in seconds")
    resolution: Optional[str] = Field(default=None, description="Video resolution (e.g., '1920x1080')")
    format: Optional[str] = Field(default="mp4", description="Video format (mp4, avi, etc.)")

    model_config = ConfigDict(arbitrary_types_allowed=True)
