# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
import os
from pathlib import Path
from typing import List

import aioboto3
from botocore.config import Config
from botocore.exceptions import ClientError

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.store.object.base_storage_client import BaseObjectStorageClient


class AioBotoClient(BaseObjectStorageClient):
    """
    Async S3 client implementation using aioboto3.
    """

    def __init__(
        self,
        server: str = None,
        access_key_id: str = None,
        secret_access_key: str = None,
        region_name: str = None,
    ):
        # Support aioboto3>=13.3.0 -> aiobotocore>=2.18
        # See:
        # - https://github.com/aio-libs/aiobotocore/issues/1385
        # - https://github.com/fsspec/s3fs/issues/931
        os.environ["AWS_REQUEST_CHECKSUM_CALCULATION"] = "WHEN_REQUIRED"
        os.environ["AWS_RESPONSE_CHECKSUM_VALIDATION"] = "WHEN_REQUIRED"

        access_key_id = access_key_id or os.getenv("OBS_ACCESS_KEY_ID")
        secret_access_key = secret_access_key or os.getenv("OBS_SECRET_ACCESS_KEY")
        server = server or os.getenv("OBS_SERVER")
        region_name = region_name or os.getenv("OBS_REGION")

        self._session = aioboto3.Session(
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region_name,
        )

        self._client_kwargs = dict(
            service_name="s3",
            endpoint_url=server,
            config=Config(
                signature_version="s3v4",
                s3={
                    "addressing_style": "virtual",
                    "payload_signing_enabled": False,
                },
            ),
        )

    def create_client(self):
        return self._session.client(**self._client_kwargs)

    async def create_bucket(self, bucket_name: str, location: str) -> bool:
        try:
            params = {
                "Bucket": bucket_name,
                "CreateBucketConfiguration": {"LocationConstraint": location},
            }

            async with self.create_client() as s3:
                await s3.create_bucket(**params)

            logger.info('Bucket "%s" created successfully in region "%s"', bucket_name, location)
            return True

        except ClientError as e:
            logger.error('Create Bucket "%s" failed: %r', bucket_name, e.response.get("Error"))
            return False

    async def delete_bucket(self, bucket_name: str) -> bool:
        try:
            async with self.create_client() as s3:
                await s3.delete_bucket(Bucket=bucket_name)

            logger.info('Bucket "%s" deleted successfully', bucket_name)
            return True

        except ClientError as e:
            logger.error('Delete Bucket "%s" failed: %r', bucket_name, e.response.get("Error"))
            return False

    async def upload_file(
        self,
        bucket_name: str,
        object_name: str,
        file_path: str | Path,
    ) -> bool:
        try:
            async with self.create_client() as s3:
                with open(file_path, "rb") as f:
                    await s3.upload_fileobj(
                        Bucket=bucket_name,
                        Key=object_name,
                        Fileobj=f,
                    )

            logger.info('Upload "%s" file "%s" to bucket "%s" succeeded', object_name, file_path, bucket_name)
            return True

        except ClientError as e:
            logger.error('Upload "%s" failed: %r', object_name, e.response.get("Error"))
            return False

    async def download_file(
        self,
        bucket_name: str,
        object_name: str,
        file_path: str | Path,
    ) -> bool:
        try:
            async with self.create_client() as s3:
                with open(file_path, "wb") as f:
                    await s3.download_fileobj(
                        Bucket=bucket_name,
                        Key=object_name,
                        Fileobj=f,
                    )

            logger.info('Download "%s" from bucket "%s" saved to "%s"', object_name, bucket_name, file_path)
            return True

        except ClientError as e:
            logger.error('Download "%s" failed: %r', object_name, e.response.get("Error"))
            return False

    async def delete_object(self, bucket_name: str, object_name: str) -> bool:
        try:
            async with self.create_client() as s3:
                await s3.delete_object(
                    Bucket=bucket_name,
                    Key=object_name,
                )

            logger.info('Delete file "%s" in bucket "%s" succeeded', object_name, bucket_name)
            return True

        except ClientError as e:
            logger.error('Delete file "%s" failed: %r', object_name, e.response.get("Error"))
            return False

    async def list_objects(
        self,
        bucket_name: str,
        object_prefix: str,
        max_objects: int = 100,
    ) -> List[dict] | None:
        try:
            async with self.create_client() as s3:
                resp = await s3.list_objects_v2(
                    Bucket=bucket_name,
                    Prefix=object_prefix,
                    MaxKeys=max_objects,
                )

            contents = resp.get("Contents", [])
            for obj in contents:
                logger.info(json.dumps(obj, indent=2, default=str))

            logger.info('Successfully listed %d objects in "%s".', len(contents), bucket_name)

            return contents

        except ClientError as e:
            logger.error('List objects in "%s" failed: %r', bucket_name, e.response.get("Error"))
            return None
