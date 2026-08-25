"""Shared constants for the video-live read-only Core Agent profile."""

from __future__ import annotations


VIDEO_TOOL_CHANNEL_ID = "video_tool"
VIDEO_READONLY_TOOL_PROFILE = "video_readonly"
VIDEO_READONLY_TOOL_NAMES = frozenset({"free_search", "fetch_webpage"})


def is_video_readonly_tool_profile(config: object) -> bool:
    return (
        isinstance(config, dict)
        and str(config.get("tool_profile") or "").strip()
        == VIDEO_READONLY_TOOL_PROFILE
    )


__all__ = [
    "VIDEO_READONLY_TOOL_NAMES",
    "VIDEO_READONLY_TOOL_PROFILE",
    "VIDEO_TOOL_CHANNEL_ID",
    "is_video_readonly_tool_profile",
]
