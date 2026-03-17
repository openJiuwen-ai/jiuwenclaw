# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Union, Literal, List, Optional

from pydantic import BaseModel, Field, ConfigDict

from openjiuwen.core.sys_operation.result import BaseResult


class ReadFileData(BaseModel):
    """Data structure for read file"""
    path: str = Field(..., description="File path of the read file")
    content: Union[str, bytes] = Field(..., description="File content (text string or binary bytes)")
    mode: Literal['text', 'bytes'] = Field(..., description="File read mode: 'text' (string) or 'bytes' (binary)")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class ReadFileChunkData(BaseModel):
    """Data structure for chunked file read"""
    path: str = Field(..., description="File path of the read file")
    chunk_content: Union[str, bytes] = Field(..., description="Current chunk content (text string or binary bytes)")
    mode: Literal['text', 'bytes'] = Field(..., description="File read mode: 'text' (string) or 'bytes' (binary)")
    chunk_size: int = Field(..., description="Size of each chunk (in bytes)")
    chunk_index: int = Field(..., description="Index of current chunk (starting from 0)")
    is_last_chunk: bool = Field(..., description="Whether current chunk is the last one")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class WriteFileData(BaseModel):
    """Data structure for write file"""
    path: str = Field(..., description="File path of the write file")
    size: int = Field(..., description="File content size in bytes")
    mode: Literal['text', 'bytes'] = Field(..., description="File write mode: 'text' (string) or 'bytes' (binary)")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class UploadFileData(BaseModel):
    """Data structure for upload file"""
    local_path: str = Field(..., description="File path of the local file")
    target_path: str = Field(..., description="File path of the target file")
    size: int = Field(..., description="File content size in bytes")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class UploadFileChunkData(BaseModel):
    """Data structure for chunked upload file"""
    local_path: str = Field(..., description="File path of the local file")
    target_path: str = Field(..., description="File path of the target file")
    chunk_size: int = Field(..., description="Size of each chunk (in bytes)")
    chunk_index: int = Field(..., description="Index of current chunk (starting from 0)")
    is_last_chunk: bool = Field(..., description="Whether current chunk is the last one")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class DownloadFileData(BaseModel):
    """Data structure for download file"""
    source_path: str = Field(..., description="File path of the source file")
    local_path: str = Field(..., description="File path of the local file")
    size: int = Field(..., description="File content size in bytes")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class DownloadFileChunkData(BaseModel):
    """Data structure for chunked file download"""
    source_path: str = Field(..., description="File path of the source file")
    local_path: str = Field(..., description="File path of the local file")
    chunk_size: int = Field(..., description="Size of each chunk (in bytes)")
    chunk_index: int = Field(..., description="Index of current chunk (starting from 0)")
    is_last_chunk: bool = Field(..., description="Whether current chunk is the last one")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class FileSystemItem(BaseModel):
    """Base model for file/directory common properties"""
    name: str = Field(..., description="Name of the file/directory")
    path: str = Field(..., description="Full absolute path of the file/directory")
    size: int = Field(..., description="Size in bytes (file size for files; total contents size for directories)")
    modified_time: str = Field(..., description="Last modification time")
    is_directory: bool = Field(..., description="Whether the item is a directory (True) or file (False)")
    type: Optional[str] = Field(default=None, description="File extension (only for files)")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class FileSystemData(BaseModel):
    """Data structure for list files and list directories"""
    total_count: int = Field(..., description="Total number of items (files/directories)")
    list_items: List[FileSystemItem] = Field(..., description="List of file/directory details")
    root_path: str = Field(..., description="Original input directory path")
    recursive: bool = Field(..., description="Actual recursive status used")
    max_depth: Optional[int] = Field(default=None, description="Actual maximum recursion depth used")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class SearchFilesData(BaseModel):
    """Data structure for search files"""
    total_matches: int = Field(..., description="Total number of files matching the search pattern")
    matching_files: List[FileSystemItem] = Field(..., description="List of matching files")
    search_path: str = Field(..., description="Original base path used for the search")
    search_pattern: str = Field(..., description="Original search pattern used")
    exclude_patterns: Optional[List[str]] = Field(default=None, description="Original exclude patterns used")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class ReadFileResult(BaseResult[ReadFileData]):
    """ReadFileResult"""
    pass


class ReadFileStreamResult(BaseResult[ReadFileChunkData]):
    """ReadFileStreamResult"""
    pass


class WriteFileResult(BaseResult[WriteFileData]):
    """WriteFileResult"""
    pass


class UploadFileResult(BaseResult[UploadFileData]):
    """UploadFileResult"""
    pass


class UploadFileStreamResult(BaseResult[UploadFileChunkData]):
    """UploadFileStreamResult"""
    pass


class DownloadFileResult(BaseResult[DownloadFileData]):
    """DownloadFileResult"""
    pass


class DownloadFileStreamResult(BaseResult[DownloadFileChunkData]):
    """DownloadFileStreamResult"""
    pass


class ListFilesResult(BaseResult[FileSystemData]):
    """ListFilesResult"""
    pass


class ListDirsResult(BaseResult[FileSystemData]):
    """ListDirsResult"""
    pass


class SearchFilesResult(BaseResult[SearchFilesData]):
    """SearchFilesResult"""
    pass
