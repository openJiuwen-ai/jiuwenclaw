# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from dataclasses import dataclass


@dataclass
class OperationMetadata:
    """
    Simple metadata.

    Attributes:
        schema_version (int): Schema version number (used for chained upgrades).
        description (str | None): Optional description for logging and auditing purposes.
    """
    schema_version: int
    description: str | None = None


@dataclass
class BaseOperation:
    """
    Base class for all Operations: pure DTO, no execution logic.

    Attributes:
        metadata (OperationMetadata): Metadata for the operation.
    """
    metadata: OperationMetadata

    @property
    def schema_version(self) -> int:
        return self.metadata.schema_version

    @property
    def description(self) -> str:
        return self.metadata.description or self.__class__.__name__