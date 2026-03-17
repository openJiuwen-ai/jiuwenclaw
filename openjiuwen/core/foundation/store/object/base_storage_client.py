# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import ABC, abstractmethod
from pathlib import Path


class BaseObjectStorageClient(ABC):
    """
    Base class for Object Storage client.

    This class provides the interface for basic bucket and object operations such as
    creating buckets, uploading/downloading files, listing objects, and deleting objects.
    """

    @abstractmethod
    async def upload_file(self, bucket_name, object_name, file_path) -> bool:
        """
        Upload a local file to an object storage bucket.

        :param bucket_name: Name of the target bucket
        :param object_name: Object key (path/name)
        :param file_path: Local file path to upload
        :return: True if upload succeeded, False otherwise
        """
        raise NotImplementedError()

    @abstractmethod
    async def download_file(self, bucket_name: str, object_name: str, file_path: str | Path) -> bool:
        """
        Download an object from Object Storage server

        :param bucket_name: Name of the bucket
        :param object_name: Object key to download
        :param file_path: Local file path where the object will be saved
        :return: True if download succeeded, False otherwise
        """
        raise NotImplementedError()

    @abstractmethod
    async def delete_object(self, bucket_name: str, object_name: str) -> bool:
        """
        Delete an object from an object storage bucket.

        :param bucket_name: Name of the bucket
        :param object_name: Object key to delete
        :return: True if deletion succeeded, False otherwise
        """
        raise NotImplementedError()

    @abstractmethod
    async def create_bucket(self, bucket_name: str, location: str) -> bool:
        """
        Create a new object storage bucket.

        :param bucket_name: Name of the bucket to be created
        :param location: Region/location where the bucket will be created
        :return: True if creation succeeded, False otherwise
        """
        raise NotImplementedError()

    @abstractmethod
    async def delete_bucket(self, bucket_name: str) -> bool:
        """
        Deletes an existing object storage bucket.

        :param bucket_name: Name of the bucket to be created
        :return: True if deletion succeeded, False otherwise
        """
        raise NotImplementedError()

    @abstractmethod
    async def list_objects(self, bucket_name: str, object_prefix: str, max_objects: int = 100) -> list[dict] | None:
        """
        List objects in an object storage bucket with a given prefix.

        :param bucket_name: Name of the bucket
        :param object_prefix: Prefix to filter objects listed
        :param max_objects: Maximum number of objects to be listed at a time.
        :return: List of dict objects if successful, otherwise None
        """
        raise NotImplementedError()
