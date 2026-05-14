# Object Storage Interface Design

## Overview

JiuwenClaw's object storage interface is an **AgentServer internal component** for handling file interactions between AgentServer and multiple object storage services (Huawei Cloud OBS, Aliyun OSS, local file system, etc.).

### Core Positioning

**Important Notes**:
- ✅ **This is AgentServer's internal storage interface**, only handles file upload/download between AgentServer and object storage
- ❌ **Does not involve** how Web clients upload/download files (handled by frontend team)
- ❌ **Does not provide** HTTP endpoints or direct connections to Web clients
- ❌ **Does not change** existing message flow (User → Channel → Gateway → AgentServer)

### Responsibility Boundaries

```
┌─────────────────────────────────────────────────────────────┐
│                  Responsibility Boundaries                    │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  [Frontend Team Responsible]    [AgentServer Storage Interface] │
│                                                             │
│  Web client uploads files to       Download files from      │
│  object storage                    object storage to         │
│  Web client downloads files        local workspace          │
│  from object storage               Upload local files to     │
│  Manage object storage             object storage            │
│  connections and authentication                             │
│                                                             │
│  [Existing System]               [Not Involved]              │
│  - Message flow: User→Channel→Gateway→AgentServer           │
│  - E2AEnvelope message protocol                            │
│  - WebChannel's _process_files()                           │
│  - Gateway's FileTransferHandler                           │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Core Functions

### 1. File Download Flow (Input Files)

```
Web client (uploads files to object storage, handled by frontend team)
         ↓
    Send file URI via WebSocket
         ↓
Gateway → AgentServer (receives E2AEnvelope.params.files)
         ↓
AgentServer storage interface (downloads files to local workspace)
         ↓
Agent (uses local file paths)
```

**Use Cases**:
- Users upload images, documents, and other files for Agent analysis
- Multiple files can be uploaded in a single conversation
- Supports all file types

**Detailed Flow**:

```
┌─────────────────────────────────────────────────────────────┐
│           Input Flow: Object Storage → Agent Workspace       │
└─────────────────────────────────────────────────────────────┘

Frontend Team                   Our Code                Agent
  │                               │                       │
  │ 1. Upload files to object     │                       │
  │    storage                    │                       │
  │    → Huawei Cloud OBS          │                       │
  │    → Aliyun OSS                │                       │
  │    → Local storage service     │                       │
  │                               │                       │
  │ 2. Send WebSocket message     │                       │
  │    {                           │                       │
  │      type: "req",             │                       │
  │      method: "chat.send",     │                       │
  │      params: {                 │                       │
  │        content: "Analyze image",│                     │
  │        files: [{                │                      │
  │          uri: "https://obs.../img.jpg" ← Object storage URI│
  │        }]                       │                      │
  │      }                          │                      │
  │    }                           │                       │
  │                               │                       │
  └───────────────────────────────┼───────────────────────┘
                                  │ Channel → Gateway
                                  │
                                  ▼
                        ┌─────────────────────────┐
                        │      AgentServer          │
                        │                             │
                        │  ┌─────────────────────┐  │
                        │  │ Receives E2AEnvelope│  │
                        │  │   params.files: [{    │  │
                        │  │     uri: "https://obs│  │
                        │  │   ...img.jpg"         │  │
                        │  │   }]                 │  │
                        │  └──────────┬──────────┘  │
                        │             │             │
                        │             ▼             │
                        │  ┌─────────────────────┐  │
                        │  │ StorageBackend      │  │
                        │  │  .download_file()     │  │
                        │  │                     │  │
                        │  │  uri: "https://obs... │  │
                        │  │  local_path: "/home/ │  │
                         │  │    .../files/..."   │  │
                        │  └──────────┬──────────┘  │
                        │             │             │
                        │             ▼             │
                        │  ┌─────────────────────┐  │
                        │  │ File downloaded to   │  │
                        │  │ local /home/.../img.jpg│  │
                        │  └─────────────────────┘  │
                        │                             │
                        └─────────────────────────────┘
```

### 2. File Upload Flow (Output Files)

```
Agent (generates files to local workspace)
         ↓
AgentServer storage interface (uploads files to object storage)
         ↓
AgentServer response (returns E2AResponse.files with URI)
         ↓
Gateway → Web client (receives URI, handled by frontend team)
```

**Use Cases**:
- Agent generates charts, reports, and other files to return to users
- Files processed by Agent need persistent storage

**Detailed Flow**:

```
┌─────────────────────────────────────────────────────────────┐
│           Output Flow: Agent Workspace → Object Storage      │
└─────────────────────────────────────────────────────────────┘

Agent                          Our Code              Frontend Team
  │                               │                       │
  │ 1. Generate files to local    │                       │
  │    /home/.../files/.../chart.png│                       │
  │                               │                       │
  └───────────────────────────────┼───────────────────────┘
                                  │
                                  ▼
                        ┌─────────────────────────┐
                        │      AgentServer          │
                        │                             │
                        │  ┌─────────────────────┐  │
                        │  │ Agent generates file │  │
                        │  │ local_path: "/home/..." │  │
                        │  └──────────┬──────────┘  │
                        │             │             │
                        │             ▼             │
                        │  ┌─────────────────────┐  │
                        │  │ StorageBackend      │  │
                        │  │  .upload_file()       │  │
                        │  │                     │  │
                        │  │  local_path: "/home/..." │ │
                        │  │  user_id: "alice"      │ │
                        │  └──────────┬──────────┘  │
                        │             │             │
                        │             ▼             │
                        │  ┌─────────────────────┐  │
                        │  │ File uploaded to     │  │
                        │  │ object storage        │  │
                        │  │ uri: "https://obs...  │  │
                        │  │     chart.png"        │  │
                        │  └─────────────────────┘  │
                        │                             │
                        │  ┌─────────────────────┐  │
                        │  │ Build E2AResponse    │  │
                        │  │   body.files: [{     │  │
                        │  │     uri: "https://obs...│ │
                         │  │       chart.png"     │  │
                        │  │   }]                 │  │
                        │  └──────────┬──────────┘  │
                        │             │             │
                        └─────────────┼─────────────┘
                                      │
                                      │ E2AResponse
                                      │ Channel → Gateway → Web
                                      │
                                      ▼
                        ┌─────────────────────────┐
                        │   Frontend receives      │
                        │   response               │
                        │   .files = [{uri: "..."}]│
                        │                           │
                        │  ┌─────────────────────┐ │
                        │  │ Frontend team        │ │
                        │  │  downloads from uri  │ │
                        │  └─────────────────────┘ │
                        └─────────────────────────┘
```

## Storage Backend Support

| Storage Backend | Type | Use Case | Config Key |
|----------------|------|----------|------------|
| **LocalStorage** | Local filesystem | Open source, development | `local` |
| **ObsStorage** | Huawei Cloud OBS | Commercial, production | `huawei_obs` |
| **OssStorage** | Aliyun OSS | Commercial, production | `aliyun_oss` |
| **Extended Support** | AWS S3/Azure Blob/Tencent COS, etc. | Commercial, production | Custom |

### Core Abstract Base Class

The system provides `BaseStorageBackend` abstract base class. Commercial implementations only need to inherit and implement core methods:

```python
from abc import ABC, abstractmethod

class BaseStorageBackend(ABC):
    """
    🎯 Simplified storage backend abstract base class - unified config validation and client management

    Core improvements:
    1. Unified configuration validation (ak/sk validation)
    2. Template method pattern reduces code duplication
    3. Lazy-loading clients + connection testing
    4. Backward compatible with existing implementations
    """

    def __init__(self, config: dict):
        """Unified initialization entry point"""
        # 1. Configuration validation
        self.config = self._validate_config(config)

        # 2. Basic property setup
        self.access_key = self.config.get("access_key", "")
        self.secret_key = self.config.get("secret_key", "")
        self.bucket = self.config.get("bucket", "")
        self.endpoint = self.config.get("endpoint", "")
        self.region = self.config.get("region", "")

        # 3. Lazy-loading client
        self._client = None

        # 4. Logging
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.info(f"{self.__class__.__name__} initialized: bucket={self.bucket}")

    def _validate_config(self, config: dict) -> dict:
        """🔒 Base configuration validation template method - subclasses can override"""
        if not isinstance(config, dict):
            raise ValueError("Configuration must be a dictionary")
        return config

    def _get_client(self):
        """🔧 Client acquisition template method (lazy loading)"""
        if self._client is None:
            self._client = self._create_client()
            self._test_connection()  # Optional connection test
        return self._client

    @abstractmethod
    def _create_client(self):
        """🏗️ Client creation abstract method - subclasses must implement"""
        pass

    def _test_connection(self):
        """🔌 Connection test template method - subclasses can override"""
        pass

    @abstractmethod
    async def upload_file(
        self,
        local_path: str,
        user_id: str,
        chat_id: str,
        channel_type: str
    ) -> str:
        """Upload file - returns URI"""
        pass

    @abstractmethod
    async def download_file(self, uri: str, local_path: str) -> None:
        """Download file"""
        pass

    @abstractmethod
    async def delete_chat_files(
        self,
        user_id: str,
        chat_id: str,
        channel_type: str,
        older_than_hours: Optional[int] = None
    ) -> int:
        """Delete chat files - returns deletion count"""
        pass

    @abstractmethod
    async def delete_user_files(
        self,
        user_id: str,
        older_than_hours: int = 24
    ) -> int:
        """Delete user files - returns deletion count"""
        pass
```

### Local Storage Implementation Example

```python
"""Local filesystem storage backend implementation example"""

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Optional

from jiuwenclaw.storage.backend import BaseStorageBackend
from jiuwenclaw.storage.exceptions import DownloadError, UploadError
from jiuwenclaw.storage.utils import sanitize_chat_id, build_object_key

logger = logging.getLogger(__name__)


class LocalStorageBackend(BaseStorageBackend):
    """
    Local filesystem as "object storage".

    Actually downloads from local storage service via HTTP,
    or directly accesses local filesystem (file:// protocol).
    """

    def _validate_config(self, config: dict) -> dict:
        """Local storage specific configuration validation"""
        super()._validate_config(config)

        if not config.get("base_dir"):
            raise ValueError("Local storage configuration incomplete, missing: base_dir")

        return config

    def __init__(self, config: dict):
        """Initialize local storage backend"""
        super().__init__(config)
        self.base_dir = Path(self.config["base_dir"]).expanduser()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.logger.info(f"LocalStorageBackend initialized with base_dir: {self.base_dir}")

    def _create_client(self):
        """Local storage doesn't need a client"""
        return None  # Local storage doesn't need a client

    def _test_connection(self):
        """Test local storage directory access"""
        if not self.base_dir.exists():
            raise ValueError(f"Local storage directory doesn't exist: {self.base_dir}")

    async def download_file(self, uri: str, local_path: str) -> None:
        """Download from local storage service or filesystem.

        Args:
            uri: File URI (supports file://, http://, https://)
            local_path: Local save path

        Raises:
            DownloadError: Download failure
        """
        try:
            # Ensure target directory exists
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)

            # Handle different URI protocols
            if uri.startswith("file://"):
                # Local filesystem, direct copy
                await self._download_from_filesystem(uri, local_path)
            elif uri.startswith(("http://", "https://")):
                # Local storage service, download via HTTP
                await self._download_from_http(uri, local_path)
            else:
                raise DownloadError(f"Unsupported URI format: {uri}")

            self.logger.info(f"Successfully downloaded file: {uri} -> {local_path}")

        except Exception as e:
            self.logger.error(f"Failed to download file {uri}: {e}")
            raise DownloadError(f"Download failed: {e}") from e

    async def _download_from_filesystem(self, uri: str, local_path: str) -> None:
        """Copy file from filesystem"""
        source_path = uri[7:]  # Remove file:// prefix
        shutil.copy2(source_path, local_path)

    async def _download_from_http(self, uri: str, local_path: str) -> None:
        """Download file via HTTP"""
        async with aiohttp.ClientSession() as session:
            async with session.get(uri) as response:
                if response.status == 200:
                    with open(local_path, 'wb') as f:
                        f.write(await response.read())
                else:
                    raise DownloadError(f"HTTP download failed: {response.status}")

    async def upload_file(
        self,
        local_path: str,
        user_id: str,
        chat_id: str,
        channel_type: str
    ) -> str:
        """Upload file to local storage.

        Args:
            local_path: Local file path
            user_id: User ID
            chat_id: Chat ID
            channel_type: Channel type

        Returns:
            Local storage URI (http://localhost:... or file://)

        Raises:
            UploadError: Upload failure
        """
        try:
            from datetime import datetime, timezone

            # Ensure file exists
            if not Path(local_path).exists():
                raise UploadError(f"Local file doesn't exist: {local_path}")

            # Clean chat_id
            try:
                cleaned_chat_id = sanitize_chat_id(chat_id, channel_type)
            except ValueError as e:
                raise UploadError(f"Invalid chat_id: {e}") from e

            # Build object key
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            filename = Path(local_path).name

            object_key = build_object_key(
                user_id=user_id,
                channel_type=channel_type,
                chat_id=cleaned_chat_id,
                timestamp=timestamp,
                filename=filename
            )

            # Target path
            target_path = self.base_dir / object_key
            target_path.parent.mkdir(parents=True, exist_ok=True)

            # Copy file
            shutil.copy2(local_path, target_path)

            # Build return URI
            uri = f"file://{target_path}"

            self.logger.info(
                f"Successfully uploaded file to local storage: {local_path} -> {uri} "
                f"(user={user_id}, channel={channel_type}, chat={cleaned_chat_id})"
            )
            return uri

        except Exception as e:
            self.logger.error(
                f"Failed to upload file {local_path} "
                f"(user={user_id}, channel={channel_type}, chat={chat_id}): {e}"
            )
            raise UploadError(f"Upload failed: {e}") from e

    async def delete_chat_files(
        self,
        user_id: str,
        chat_id: str,
        channel_type: str,
        older_than_hours: Optional[int] = None
    ) -> int:
        """Delete all files for a specific chat."""
        try:
            import time
            from datetime import datetime, timezone

            # Clean chat_id
            try:
                cleaned_chat_id = sanitize_chat_id(chat_id, channel_type)
            except ValueError as e:
                raise UploadError(f"Invalid chat_id: {e}") from e

            # Build prefix
            chat_prefix = f"{channel_type}_{cleaned_chat_id}"
            prefix = f"files/{user_id}/{chat_prefix}/"

            # Calculate time threshold
            time_threshold = None
            if older_than_hours is not None:
                time_threshold = time.time() - (older_than_hours * 3600)

            deleted_count = 0

            # Find and delete files
            if not self.base_dir.exists():
                return 0

            for file_path in self.base_dir.rglob("*"):
                if not file_path.is_file():
                    continue

                # Check if file path matches prefix
                try:
                    relative_path = file_path.relative_to(self.base_dir)
                    if not str(relative_path).startswith(prefix):
                        continue
                except ValueError:
                    continue

                # Check time filter
                if older_than_hours is not None:
                    file_mtime = file_path.stat().st_mtime
                    if file_mtime > time_threshold:
                        continue

                # Delete file
                file_path.unlink()
                deleted_count += 1

            self.logger.info(
                f"Deleted {deleted_count} local files "
                f"(user={user_id}, channel={channel_type}, chat={cleaned_chat_id})"
            )
            return deleted_count

        except Exception as e:
            self.logger.error(
                f"Failed to delete local chat files "
                f"(user={user_id}, channel={channel_type}, chat={chat_id}): {e}"
            )
            raise UploadError(f"Deletion failed: {e}") from e

    async def delete_user_files(
        self,
        user_id: str,
        older_than_hours: int = 24
    ) -> int:
        """Batch delete old files for a user."""
        try:
            import time

            # Build prefix
            prefix = f"files/{user_id}/"

            # Calculate time threshold
            time_threshold = time.time() - (older_than_hours * 3600)
            deleted_count = 0

            # Find and delete files
            if not self.base_dir.exists():
                return 0

            for file_path in self.base_dir.rglob("*"):
                if not file_path.is_file():
                    continue

                # Check if file path matches prefix
                try:
                    relative_path = file_path.relative_to(self.base_dir)
                    if not str(relative_path).startswith(prefix):
                        continue
                except ValueError:
                    continue

                # Check time filter
                file_mtime = file_path.stat().st_mtime
                if file_mtime > time_threshold:
                    continue

                # Delete file
                file_path.unlink()
                deleted_count += 1

            self.logger.info(
                f"Deleted {deleted_count} local files older than {older_than_hours}h "
                f"(user={user_id})"
            )
            return deleted_count

        except Exception as e:
            self.logger.error(f"Failed to delete local user files (user={user_id}): {e}")
            raise UploadError(f"Deletion failed: {e}") from e
```

### Huawei Cloud OBS Implementation Example

```python
"""Huawei Cloud OBS storage backend implementation example"""

from jiuwenclaw.storage.backend import BaseStorageBackend
from jiuwenclaw.storage.exceptions import DownloadError, UploadError
from jiuwenclaw.storage.utils import sanitize_chat_id, build_object_key

class ObsStorageBackend(BaseStorageBackend):
    """Huawei Cloud OBS storage backend"""

    def _validate_config(self, config: dict) -> dict:
        """OBS specific configuration validation"""
        super()._validate_config(config)

        required_fields = ["access_key", "secret_key", "bucket"]
        missing = [field for field in required_fields if not config.get(field)]
        if missing:
            raise ValueError(f"OBS configuration incomplete, missing: {', '.join(missing)}")

        # Set default endpoint
        if not config.get("endpoint"):
            config["endpoint"] = "obs.cn-north-4.myhuaweicloud.com"

        return config

    def _create_client(self):
        """Create OBS client"""
        try:
            from obs import ObsClient
            client = ObsClient(
                access_key_id=self.access_key,
                secret_access_key=self.secret_key,
                server=self.endpoint,
            )
            self.logger.info("OBS client created successfully")
            return client
        except ImportError:
            raise ImportError("esdk-obs-py SDK not installed, please run: pip install esdk-obs-py")
        except Exception as e:
            raise UploadError(f"Failed to create OBS client: {e}")

    def _test_connection(self):
        """Test OBS connection"""
        try:
            client = self._get_client()
            client.listBuckets()
        except Exception as e:
            self.logger.warning(f"OBS connection test failed: {e}")

    # Implement other core methods...
```

### Aliyun OSS Implementation Example

```python
"""Aliyun OSS storage backend implementation example"""

from jiuwenclaw.storage.backend import BaseStorageBackend

class OssStorageBackend(BaseStorageBackend):
    """Aliyun OSS storage backend"""

    def _validate_config(self, config: dict) -> dict:
        """OSS specific configuration validation"""
        super()._validate_config(config)

        required_fields = ["access_key", "secret_key", "bucket"]
        missing = [field for field in required_fields if not config.get(field)]
        if missing:
            raise ValueError(f"OSS configuration incomplete, missing: {', '.join(missing)}")

        # Set default endpoint
        if not config.get("endpoint"):
            config["endpoint"] = "oss-cn-hangzhou.aliyuncs.com"

        return config

    def _create_client(self):
        """Create OSS client"""
        try:
            import oss2
            auth = oss2.Auth(self.access_key, self.secret_key)
            bucket = oss2.Bucket(auth, self.endpoint, self.bucket)
            self.logger.info("OSS bucket created successfully")
            return bucket
        except ImportError:
            raise ImportError("oss2 SDK not installed, please run: pip install oss2")
        except Exception as e:
            raise UploadError(f"Failed to create OSS bucket: {e}")

    # Implement other core methods...
```

## File Isolation Mechanism

### CHAT_ID Level Isolation (v2.0)

**Design Principle**: Use `user_id + chat_id + channel_type` for file isolation, enabling session-level file management.

**Path Structure**:
```
Object storage path structure:
bucket: jiuwenclaw-data
└── files/
    └── {user_id}/                          # User ID
        └── {channel_type}_{chat_id}/       # Channel_Chat ID (cleaned)
            └── {YYYYMMDD_HHMMSS}/          # Upload timestamp
                └── filename.ext

Example URI:
https://obs.../files/alice/web_chat456/20250511_143052/image.jpg
                    ↑     ↑         ↑
                  user_id channel+chat timestamp
```

**chat_id Cleaning Rules** (implemented via `sanitize_chat_id()`):
- Remove special characters (except `-` and `_`)
- Replace `/` with `-`
- Limit length to 64 characters
- Ensure starting with a letter

**Utility Functions**:
```python
from jiuwenclaw.storage.utils import sanitize_chat_id, build_object_key

# Clean chat_id
cleaned_id = sanitize_chat_id("chat/456", "web")
# Returns: "web_chat-456"

# Build object key
object_key = build_object_key(
    user_id="alice",
    channel_type="web",
    chat_id="chat456",
    timestamp="20250511_143052",
    filename="image.jpg"
)
# Returns: "files/alice/web_chat456/20250511_143052/image.jpg"
```

**Advantages**:
- Complete isolation between different users' files
- Separation of different chat files for the same user
- Support for session-level file cleanup
- File URIs in historical messages remain permanently accessible
- Flexible support for multiple channels (web, websocket, etc.)

## Configuration

### Basic Configuration

Add `storage` configuration section in `config.yaml`:

```yaml
storage:
  # ============================================================
  # Object storage configuration (AgentServer internal use)
  # ============================================================
  # Storage type: local | huawei-obs | aliyun-oss
  type: ${STORAGE_TYPE:-local}

  # Local filesystem storage configuration (open source, development)
  local:
    base_dir: ${STORAGE_LOCAL_BASE_DIR:-~/.jiuwenclaw/storage/local}
    upload_dir: ${STORAGE_LOCAL_UPLOAD_DIR:-~/.jiuwenclaw/uploads}

  # Huawei Cloud OBS configuration (commercial, production)
  huawei_obs:
    access_key: ${OBS_ACCESS_KEY:-}
    secret_key: ${OBS_SECRET_KEY:-}
    endpoint: ${OBS_ENDPOINT:-obs.cn-north-4.myhuaweicloud.com}
    bucket: ${OBS_BUCKET:-}

  # Aliyun OSS configuration (commercial, production)
  aliyun_oss:
    access_key: ${OSS_ACCESS_KEY:-}
    secret_key: ${OSS_SECRET_KEY:-}
    endpoint: ${OSS_ENDPOINT:-oss-cn-hangzhou.aliyuncs.com}
    bucket: ${OSS_BUCKET:-}
```

### Environment Variable Configuration

All storage configuration items can be set via environment variables without modifying `config.yaml`.

Configure in `.env` file:

```bash
# === Storage type selection ===
# Options: local, huawei-obs, aliyun-oss
STORAGE_TYPE=local

# === Local storage configuration ===
# STORAGE_LOCAL_BASE_DIR=~/.jiuwenclaw/storage/local
# STORAGE_LOCAL_UPLOAD_DIR=~/.jiuwenclaw/uploads

# === Huawei Cloud OBS configuration (commercial) ===
# STORAGE_TYPE=huawei-obs
# OBS_ACCESS_KEY=your_access_key_id
# OBS_SECRET_KEY=your_secret_access_key
# OBS_ENDPOINT=obs.cn-north-4.myhuaweicloud.com
# OBS_BUCKET=your-bucket-name

# === Aliyun OSS configuration (commercial) ===
# STORAGE_TYPE=aliyun-oss
# OSS_ACCESS_KEY=your_access_key_id
# OSS_SECRET_KEY=your_secret_access_key
# OSS_ENDPOINT=oss-cn-hangzhou.aliyuncs.com
# OSS_BUCKET=your-bucket-name
```

**Configuration Priority**: Environment variables > config.yaml configuration > defaults

**Environment Variable Template Location**: `jiuwenclaw/resources/.env.template`

**Configuration File Location**: `jiuwenclaw/resources/config.yaml`

## Core Interfaces

### StorageBackend Abstract Interface

```python
# jiuwenclaw/storage/backend.py

from abc import ABC, abstractmethod

class BaseStorageBackend(ABC):
    """
    🎯 Simplified storage backend abstract base class - v2.0

    Core responsibilities:
    1. download_file(): Download files from object storage URI to local workspace
    2. upload_file(): Upload local files to object storage, return URI
    3. delete_chat_files(): Delete files for a specific chat
    4. delete_user_files(): Delete user files

    Design principles:
    - Simple: Only 4 core methods
    - Internal: Only used within AgentServer
    - Stateless: Doesn't maintain session state
    - Isolation: Use user_id + chat_id + channel_type for file isolation
    """

    @abstractmethod
    async def download_file(
        self,
        uri: str,           # Object storage URI (from E2AEnvelope.params.files)
        local_path: str,    # Local save path (Agent workspace)
    ) -> None:
        """
        Download files from object storage to local workspace

        Supported URI formats:
            - https://obs... (Huawei Cloud OBS)
            - https://oss... (Aliyun OSS)
            - http://... (Local storage service)
            - file://... (Local filesystem)

        Use cases:
            After AgentServer receives E2AEnvelope.params.files,
            files need to be downloaded to workspace for Agent use
        """
        pass

    @abstractmethod
    async def upload_file(
        self,
        local_path: str,    # Local file generated by Agent
        user_id: str,       # User ID (for path isolation)
        chat_id: str,       # Chat ID (for session-level isolation)
        channel_type: str,  # Channel type (web, websocket, etc.)
    ) -> str:              # Return object storage URI
        """
        Upload local files to object storage

        Parameters:
            local_path: Local file path
            user_id: User ID
            chat_id: Chat ID (will be cleaned and validated)
            channel_type: Channel type

        Returns:
            Object storage URI (https://obs:// or https://oss://)

        Use cases:
            After Agent generates files, they need to be uploaded to object storage
            URI returned to frontend via E2AResponse
        """
        pass

    @abstractmethod
    async def delete_chat_files(
        self,
        user_id: str,
        chat_id: str,
        channel_type: str,
        older_than_hours: Optional[int] = None
    ) -> int:
        """
        Delete files for a specific chat

        Returns the number of files deleted
        """
        pass

    @abstractmethod
    async def delete_user_files(
        self,
        user_id: str,
        older_than_hours: int = 24
    ) -> int:
        """
        Delete user files

        Returns the number of files deleted
        """
        pass
```

### Factory Class

```python
# jiuwenclaw/storage/factory.py

class StorageService:
    """Storage service factory class (singleton)

    Note: Specific implementations have been moved to documentation.
    Commercial scenarios require self-implementation inheriting from BaseStorageBackend.
    """

    _instance = None

    @classmethod
    async def get_instance(cls) -> BaseStorageBackend:
        """Get storage service instance (singleton)"""
        if cls._instance is None:
            cls._instance = await cls._create_backend()
        return cls._instance

    @classmethod
    async def _create_backend(cls) -> BaseStorageBackend:
        """Create backend based on configuration

        Note: This only provides a framework. Specific implementations need to be
        implemented according to project requirements.

        Reference documentation: docs/zh/对象存储接口设计.md - Commercial implementation examples section
        """
        try:
            from jiuwenclaw.config import get_config

            config = get_config()
            storage_config = config.get("storage", {})
            backend_type = storage_config.get("type", "local")

            # Note: The following code is just an example framework
            # For specific implementations, please refer to implementation examples in docs/zh/对象存储接口设计.md

            if backend_type == "local":
                # Local storage implementation example
                # Please refer to LocalStorageBackend implementation in the documentation
                raise NotImplementedError(
                    "Local storage implementation please refer to LocalStorageBackend example in docs/zh/对象存储接口设计.md"
                )

            elif backend_type == "huawei-obs":
                # Huawei Cloud OBS implementation example
                # Please refer to ObsStorageBackend implementation in the documentation
                raise NotImplementedError(
                    "Huawei Cloud OBS implementation please refer to ObsStorageBackend example in docs/zh/对象存储接口设计.md"
                )

            elif backend_type == "aliyun-oss":
                # Aliyun OSS implementation example
                # Please refer to OssStorageBackend implementation in the documentation
                raise NotImplementedError(
                    "Aliyun OSS implementation please refer to OssStorageBackend example in docs/zh/对象存储接口设计.md"
                )

            else:
                raise ConfigError(f"Unknown storage type: {backend_type}")

        except Exception as e:
            logger.error(f"Failed to create storage backend: {e}")
            raise ConfigError(f"Failed to create storage backend: {e}") from e
```

## AgentServer Integration

### Input File Processing (Download)

```python
# jiuwenclaw/agentserver/interface.py

class JiuWenClaw:

    def __init__(self, ...):
        # Initialize storage service
        from jiuwenclaw.storage import StorageService
        self._storage = StorageService.get_instance()

    async def _prepare_input_files(
        self,
        envelope: E2AEnvelope,
        workspace_dir: str,
    ):
        """
        Preprocess input files: Download from object storage to local workspace

        Call timing: Before process_message starts

        Input: envelope.params.files = [{uri: "https://obs.../img.jpg"}]
        Output: Modify params, add path field
        """
        if "files" not in envelope.params:
            return

        user_id = envelope.user_id or "default"

        for file_info in envelope.params["files"]:
            uri = file_info.get("uri") or file_info.get("url")
            if not uri:
                continue

            # Build local path (only use user_id)
            filename = file_info.get("name", "unknown_file")
            local_path = self._build_local_path(
                workspace_dir, user_id, "input", filename
            )

            try:
                # Call storage interface to download
                await self._storage.download_file(uri=uri, local_path=local_path)

                # Add local path to file_info
                file_info["path"] = local_path

            except Exception as e:
                logger.error(f"Failed to download file {uri}: {e}")
                # Optional: Remove failed file reference
```

### Output File Processing (Upload)

```python
# jiuwenclaw/agentserver/interface.py

class JiuWenClaw:

    async def _handle_output_files(
        self,
        response: AgentResponse,
        user_id: str,
        chat_id: str,
        channel_type: str,
    ):
        """
        Postprocess output files: Upload to object storage

        Call timing: Before process_message ends

        Input: response may contain local file paths
        Output: Modify response, add uri field
        """
        # Extract file paths generated by Agent
        local_files = self._extract_generated_files(response)

        if not local_files:
            return

        uploaded_files = []

        for local_path in local_files:
            try:
                # Call storage interface to upload (use user_id + chat_id + channel_type)
                uri = await self._storage.upload_file(
                    local_path=local_path,
                    user_id=user_id,
                    chat_id=chat_id,
                    channel_type=channel_type
                )

                uploaded_files.append({
                    "path": local_path,
                    "uri": uri
                })

            except Exception as e:
                logger.error(f"Failed to upload file {local_path}: {e}")

        # Update response
        if response.payload:
            response.payload["files"] = uploaded_files
```

### Complete Example: Input/Output File Processing

```python
# AgentServer receives request
envelope = E2AEnvelope(
    method="chat.send",
    params={
        'content': 'Please analyze this image',
        'files': [{
            'uri': 'https://obs.../files/alice/web_chat123/20250511_143052/input/image.jpg',
            'name': 'image.jpg'
        }]
    },
    context={
        'user_id': 'alice',
        'chat_id': 'chat123',
        'channel_type': 'web'
    }
)

# 1. Input file processing (download)
await self._prepare_input_files(envelope, workspace_dir)
# Result: params.files[0]['path'] = '~/.jiuwenclaw/agent/jiuwenclaw_workspace/files/alice/input/image.jpg'

# 2. Agent processing (uses local files)
response = await self._agent.process(envelope.params)

# 3. Output file processing (upload)
if response.contains_files():
    await self._handle_output_files(
        response,
        user_id='alice',
        chat_id='chat123',
        channel_type='web'
    )
    # Result: response.files = [{'path': '...', 'uri': 'https://obs.../files/alice/web_chat123/20250511_143105/output/result.png'}]

# 4. Return response to Gateway, Gateway passes to Web client
return response
```

## Developer Guide

### Extending New Storage Backends

To support new object storage services, inherit from the `BaseStorageBackend` abstract base class:

```python
# jiuwenclaw/storage/my_backend.py

from jiuwenclaw.storage.backend import BaseStorageBackend

class MyStorageBackend(BaseStorageBackend):
    """Custom storage backend"""

    def _validate_config(self, config: dict) -> dict:
        """Configuration validation"""
        super()._validate_config(config)
        if not config.get("my_field"):
            raise ValueError("Missing my_field")
        return config

    def _create_client(self):
        """Create client"""
        return MyStorageClient(
            key=self.access_key,
            secret=self.secret_key
        )

    async def download_file(self, uri: str, local_path: str) -> None:
        """Implement download logic"""
        # 1. Parse object key from URI
        # 2. Download file to local path
        pass

    async def upload_file(
        self,
        local_path: str,
        user_id: str,
        chat_id: str,
        channel_type: str
    ) -> str:
        """Implement upload logic"""
        # 1. Upload local file to object storage
        # 2. Generate access URI
        # 3. Return URI
        pass

    async def delete_chat_files(
        self,
        user_id: str,
        chat_id: str,
        channel_type: str,
        older_than_hours: Optional[int] = None
    ) -> int:
        """Delete chat files"""
        pass

    async def delete_user_files(
        self,
        user_id: str,
        older_than_hours: int = 24
    ) -> int:
        """Delete user files"""
        pass
```

Then register in the factory class:

```python
# jiuwenclaw/storage/factory.py

async def _create_backend(cls) -> BaseStorageBackend:
    config = get_config()["storage"]
    backend_type = config.get("type", "local")

    if backend_type == "my-storage":
        from jiuwenclaw.storage.my_backend import MyStorageBackend
        return MyStorageBackend(config["my_storage"])

    # ... other backends
```

### Unit Test Example

```python
# tests/unit/test_storage_backend.py

import pytest
from jiuwenclaw.storage.backend import BaseStorageBackend

class TestStorageBackend:

    @pytest.fixture
    def backend(self, tmp_path):
        # Need to create concrete implementation class for testing
        from jiuwenclaw.storage.backend import BaseStorageBackend

        class MockStorageBackend(BaseStorageBackend):
            def _create_client(self):
                return None

            async def upload_file(self, local_path, user_id, chat_id, channel_type):
                return f"mock://uri/{local_path}"

            async def download_file(self, uri, local_path):
                pass

            async def delete_chat_files(self, user_id, chat_id, channel_type, older_than_hours=None):
                return 0

            async def delete_user_files(self, user_id, older_than_hours=24):
                return 0

        config = {
            "access_key": "test_key",
            "secret_key": "test_secret",
            "bucket": "test_bucket"
        }
        return MockStorageBackend(config)

    @pytest.mark.asyncio
    async def test_download_file(self, backend):
        """Test file download"""
        # Execute download
        await backend.download_file(
            uri="file:///path/to/test.jpg",
            local_path="/tmp/test.jpg"
        )

        # Verify results (verify based on actual implementation)

    @pytest.mark.asyncio
    async def test_upload_file(self, backend):
        """Test file upload"""
        # Execute upload
        uri = await backend.upload_file(
            local_path="/tmp/test.jpg",
            user_id="test_user",
            chat_id="test_chat",
            channel_type="web"
        )

        # Verify results
        assert uri is not None
        assert uri.startswith("mock://uri/")
```

## Troubleshooting

### Common Issues

#### 1. File Download Failure

**Symptoms**: Agent cannot access uploaded files

**Troubleshooting Steps**:
1. Check if file URI is correct
2. Check if file exists
3. View AgentServer logs
4. Verify storage backend configuration

#### 2. File Upload Failure

**Symptoms**: Files generated by Agent cannot be returned to users

**Troubleshooting Steps**:
1. Check local file path
2. Check storage backend permissions
3. Check file size limits
4. View AgentServer logs

#### 3. Signed URL Cannot Be Accessed

**Symptoms**: Web client cannot download files via URL

**Troubleshooting Steps**:
1. Check if URL has expired
2. Check bucket permission configuration
3. Verify endpoint configuration
4. Test if URL is correct

### Log Viewing

```bash
# View AgentServer logs
tail -f ~/.jiuwenclaw/agent/.logs/agent_server.log

# View storage related logs
grep "storage" ~/.jiuwenclaw/agent/.logs/agent_server.log
```

## Performance Optimization Recommendations

### 1. Concurrency Control

Configure concurrent transfer count:

```yaml
storage:
  max_concurrent_transfers: 5  # Number of concurrent uploads/downloads
```

### 2. File Size Limits

Configure file size limits:

```yaml
storage:
  local:
    max_file_size: 104857600  # 100MB
  huawei_obs:
    max_file_size: 104857600
```

### 3. Timeout Settings

Configure transfer timeout:

```yaml
storage:
  transfer_timeout: 300  # 5 minutes
```

### 4. Large File Handling

For large files (>10MB):
- Use chunked download/upload
- Show progress indicator
- Support resumable transfers

## Security Recommendations

### 1. Access Key Management

- ✅ Use environment variables to store keys
- ❌ Don't hardcode keys in configuration files
- ✅ Rotate keys regularly
- ✅ Use principle of least privilege

### 2. Network Security

- ✅ Use HTTPS in production
- ✅ Configure bucket policies to restrict access
- ✅ Use signed URLs (temporary access)

### 3. File Validation

- ✅ Verify file types
- ✅ Limit file sizes
- ✅ Scan for malicious files

### 4. Access Control

- Only process files belonging to that user/session
- Verify URI legitimacy
- Limit file sizes

## Version Compatibility

| JiuwenClaw Version | Storage Interface Version | Compatibility Notes |
|-------------------|--------------------------|-------------------|
| v0.1.x            | -                        | Not supported     |
| v0.2.0+           | 1.0                      | First release     |

### Backward Compatibility

- Don't modify existing file handling logic
- New features enabled via configuration
- Keep fallback options

### Migration Path

```
Phase 1: Add storage module (optional)
├─ Configure storage.type=local
└─ Disabled by default, enabled via configuration

Phase 2: Gradual integration into AgentServer
├─ Call storage interface in process_message
├─ Keep original logic as fallback
└─ Migrate gradually

Phase 3: Commercial version enable OBS/OSS
├─ Configure storage.type=huawei-obs
└─ Full functionality
```

## Commercial Implementation Examples (Extended)

This section provides more commercial storage service implementation examples based on the `BaseStorageBackend` abstract base class.

### Core Advantages

1. **Unified Configuration Validation** - Automatic ak/sk validation
2. **Template Method Pattern** - Reduce code duplication
3. **Lazy-Loading Clients** - Delayed initialization, connection testing
4. **Simple to Use** - Only need to implement 4 core methods

### Abstract Base Class Structure

```python
class BaseStorageBackend(ABC):
    """🎯 Simplified storage backend abstract base class"""

    def __init__(self, config: dict):
        # 1. Configuration validation
        self.config = self._validate_config(config)

        # 2. Basic properties
        self.access_key = self.config.get("access_key", "")
        self.secret_key = self.config.get("secret_key", "")
        self.bucket = self.config.get("bucket", "")

        # 3. Lazy-loading client
        self._client = None

    def _validate_config(self, config: dict) -> dict:
        """🔒 Configuration validation - subclasses can override"""
        if not isinstance(config, dict):
            raise ValueError("Configuration must be a dictionary")
        return config

    def _get_client(self):
        """🔧 Client acquisition (lazy loading)"""
        if self._client is None:
            self._client = self._create_client()
            self._test_connection()
        return self._client

    @abstractmethod
    def _create_client(self):
        """🏗️ Client creation - subclasses must implement"""
        pass

    def _test_connection(self):
        """🔌 Connection test - subclasses can override"""
        pass

    # Core business methods
    @abstractmethod
    async def upload_file(self, local_path: str, user_id: str,
                         chat_id: str, channel_type: str) -> str:
        """Upload file"""
        pass

    @abstractmethod
    async def download_file(self, uri: str, local_path: str) -> None:
        """Download file"""
        pass

    @abstractmethod
    async def delete_chat_files(self, user_id: str, chat_id: str,
                               channel_type: str,
                               older_than_hours: Optional[int] = None) -> int:
        """Delete chat files"""
        pass

    @abstractmethod
    async def delete_user_files(self, user_id: str,
                               older_than_hours: int = 24) -> int:
        """Delete user files"""
        pass
```

### AWS S3 Storage Implementation

```python
"""AWS S3 storage backend implementation"""

import asyncio
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from jiuwenclaw.storage.backend import BaseStorageBackend
from jiuwenclaw.storage.exceptions import UploadError, DownloadError
from jiuwenclaw.storage.utils import sanitize_chat_id, build_object_key


class S3StorageBackend(BaseStorageBackend):
    """AWS S3 storage backend"""

    def _validate_config(self, config: dict) -> dict:
        """S3 specific configuration validation"""
        super()._validate_config(config)

        required_fields = ["access_key", "secret_key", "bucket", "region"]
        missing = [field for field in required_fields if not config.get(field)]
        if missing:
            raise ValueError(f"S3 configuration incomplete, missing: {', '.join(missing)}")

        # Set default region
        if not config.get("region"):
            config["region"] = "us-east-1"

        return config

    def _create_client(self):
        """Create S3 client"""
        try:
            import boto3

            client = boto3.client(
                's3',
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name=self.region
            )
            self.logger.info("S3 client created successfully")
            return client

        except ImportError:
            raise ImportError("Please install boto3: pip install boto3")
        except Exception as e:
            raise UploadError(f"Failed to create S3 client: {e}")

    def _test_connection(self):
        """Test S3 connection"""
        try:
            client = self._get_client()
            client.head_bucket(Bucket=self.bucket)
        except Exception as e:
            self.logger.warning(f"S3 connection test failed: {e}")

    async def upload_file(self, local_path: str, user_id: str,
                         chat_id: str, channel_type: str) -> str:
        """Upload file to S3"""
        from jiuwenclaw.storage.utils import sanitize_chat_id, build_object_key

        cleaned_chat_id = sanitize_chat_id(chat_id, channel_type)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = Path(local_path).name

        # Build object key
        object_key = build_object_key(
            user_id=user_id,
            channel_type=channel_type,
            chat_id=cleaned_chat_id,
            timestamp=timestamp,
            filename=filename
        )

        # Upload file
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self._get_client().upload_file(
                self.bucket, object_key, local_path
            )
        )

        # Return URI
        uri = f"https://{self.bucket}.s3.{self.region}.amazonaws.com/{object_key}"
        self.logger.info(f"Uploaded to S3: {local_path} -> {uri}")
        return uri

    async def download_file(self, uri: str, local_path: str) -> None:
        """Download file from S3"""
        object_key = self._parse_uri(uri)
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self._get_client().download_file(
                self.bucket, object_key, local_path
            )
        )

        self.logger.info(f"Downloaded from S3: {uri} -> {local_path}")

    async def delete_chat_files(self, user_id: str, chat_id: str,
                               channel_type: str,
                               older_than_hours: Optional[int] = None) -> int:
        """Delete chat files"""
        from jiuwenclaw.storage.utils import sanitize_chat_id

        cleaned_chat_id = sanitize_chat_id(chat_id, channel_type)
        prefix = f"files/{user_id}/{channel_type}_{cleaned_chat_id}/"
        return await self._delete_by_prefix(prefix, older_than_hours)

    async def delete_user_files(self, user_id: str,
                               older_than_hours: int = 24) -> int:
        """Delete user files"""
        prefix = f"files/{user_id}/"
        return await self._delete_by_prefix(prefix, older_than_hours)

    async def _delete_by_prefix(self, prefix: str,
                               older_than_hours: Optional[int]) -> int:
        """Generic method for deletion by prefix"""
        import time
        import asyncio

        client = self._get_client()
        deleted_count = 0

        # List objects
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: client.list_objects_v2(Bucket=self.bucket, Prefix=prefix)
        )

        if 'Contents' not in response:
            return 0

        # Delete objects
        for obj in response['Contents']:
            # Time filter
            if older_than_hours is not None:
                obj_time = obj['LastModified'].timestamp()
                if time.time() - obj_time < older_than_hours * 3600:
                    continue

            # Delete
            await loop.run_in_executor(
                None,
                lambda o=obj: client.delete_object(Bucket=self.bucket, Key=o['Key'])
            )
            deleted_count += 1

        return deleted_count

    def _parse_uri(self, uri: str) -> str:
        """Parse S3 URI"""
        # https://bucket.s3.region.amazonaws.com/key
        prefix = f"https://{self.bucket}.s3.{self.region}.amazonaws.com/"
        if uri.startswith(prefix):
            return uri[len(prefix):]
        raise ValueError(f"Invalid S3 URI: {uri}")
```

### Azure Blob Storage Implementation

```python
"""Azure Blob storage backend implementation"""

import asyncio
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from jiuwenclaw.storage.backend import BaseStorageBackend
from jiuwenclaw.storage.exceptions import UploadError, DownloadError
from jiuwenclaw.storage.utils import sanitize_chat_id, build_object_key


class AzureBlobStorageBackend(BaseStorageBackend):
    """Azure Blob storage backend"""

    def _validate_config(self, config: dict) -> dict:
        """Azure specific configuration validation"""
        super()._validate_config(config)

        required_fields = ["connection_string", "container"]
        missing = [field for field in required_fields if not config.get(field)]
        if missing:
            raise ValueError(f"Azure configuration incomplete, missing: {', '.join(missing)}")

        return config

    def _create_client(self):
        """Create Azure Blob client"""
        try:
            from azure.storage.blob import BlobServiceClient

            client = BlobServiceClient.from_connection_string(
                self.config["connection_string"]
            )
            self.logger.info("Azure Blob client created successfully")
            return client

        except ImportError:
            raise ImportError("Please install azure-storage-blob: pip install azure-storage-blob")
        except Exception as e:
            raise UploadError(f"Failed to create Azure Blob client: {e}")

    def _test_connection(self):
        """Test Azure connection"""
        try:
            client = self._get_client()
            container_client = client.get_container_client(self.config["container"])
            container_client.get_container_properties()
        except Exception as e:
            self.logger.warning(f"Azure connection test failed: {e}")

    async def upload_file(self, local_path: str, user_id: str,
                         chat_id: str, channel_type: str) -> str:
        """Upload file to Azure Blob"""
        from jiuwenclaw.storage.utils import sanitize_chat_id, build_object_key

        cleaned_chat_id = sanitize_chat_id(chat_id, channel_type)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = Path(local_path).name

        # Build blob path
        blob_path = build_object_key(
            user_id=user_id,
            channel_type=channel_type,
            chat_id=cleaned_chat_id,
            timestamp=timestamp,
            filename=filename
        )

        # Upload file
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self._get_client().get_blob_client(
                self.config["container"], blob_path
            ).upload_blob(local_path)
        )

        # Return URI
        uri = f"{self.config['account_url']}/{self.config['container']}/{blob_path}"
        self.logger.info(f"Uploaded to Azure Blob: {local_path} -> {uri}")
        return uri

    # Other methods implemented similarly to S3...
```

### Tencent Cloud COS Storage Implementation

```python
"""Tencent Cloud COS storage backend implementation"""

from jiuwenclaw.storage.backend import BaseStorageBackend
from jiuwenclaw.storage.utils import sanitize_chat_id, build_object_key


class TencentCOSStorageBackend(BaseStorageBackend):
    """Tencent Cloud COS storage backend"""

    def _validate_config(self, config: dict) -> dict:
        """COS specific configuration validation"""
        super()._validate_config(config)

        required_fields = ["secret_id", "secret_key", "bucket", "region"]
        missing = [field for field in required_fields if not config.get(field)]
        if missing:
            raise ValueError(f"COS configuration incomplete, missing: {', '.join(missing)}")

        return config

    def _create_client(self):
        """Create COS client"""
        try:
            from cos import CosConfig
            from cos import CosS3Client
            from cos import CosCredentials

            config = CosConfig(
                Region=self.region,
                Credential=CosCredentials(
                    self.secret_id,  # COS uses secret_id
                    self.secret_key
                )
            )
            client = CosS3Client(config)
            self.logger.info("Tencent COS client created successfully")
            return client

        except ImportError:
            raise ImportError("Please install cos-python-sdk-v5: pip install cos-python-sdk-v5")
        except Exception as e:
            raise UploadError(f"Failed to create COS client: {e}")

    # Implement core methods...
```

## Configuration Examples

### Commercial Storage Configuration

```yaml
storage:
  # Storage type selection
  type: ${STORAGE_TYPE:-local}

  # Local storage configuration
  local:
    base_dir: ${STORAGE_LOCAL_BASE_DIR:-~/.jiuwenclaw/storage/local}

  # S3 storage configuration
  s3:
    access_key: ${S3_ACCESS_KEY:-}
    secret_key: ${S3_SECRET_KEY:-}
    bucket: ${S3_BUCKET:-}
    region: ${S3_REGION:-us-east-1}

  # Azure Blob configuration
  azure:
    connection_string: ${AZURE_CONNECTION_STRING:-}
    container: ${AZURE_CONTAINER:-}
    account_url: ${AZURE_ACCOUNT_URL:-}

  # Tencent Cloud COS configuration
  tencent_cos:
    secret_id: ${COS_SECRET_ID:-}
    secret_key: ${COS_SECRET_KEY:-}
    bucket: ${COS_BUCKET:-}
    region: ${COS_REGION:-ap-guangzhou}
```

### Environment Variable Configuration

```bash
# S3 configuration
export STORAGE_TYPE=s3
export S3_ACCESS_KEY=your_access_key
export S3_SECRET_KEY=your_secret_key
export S3_BUCKET=your_bucket_name
export S3_REGION=us-east-1

# Azure configuration
export STORAGE_TYPE=azure
export AZURE_CONNECTION_STRING="DefaultEndpointsProtocol=https;AccountName=..."
export AZURE_CONTAINER=your_container_name
export AZURE_ACCOUNT_URL=https://your_account.blob.core.windows.net

# Tencent Cloud COS configuration
export STORAGE_TYPE=tencent_cos
export COS_SECRET_ID=your_secret_id
export COS_SECRET_KEY=your_secret_key
export COS_BUCKET=your_bucket_name
export COS_REGION=ap-guangzhou
```

## Implementation Guide

### Quick Start

1. **Select Implementation Example**
   - Choose appropriate storage backend implementation from this documentation
   - Copy example code to project

2. **Create Implementation File**
   ```bash
   # Create implementation file
   touch jiuwenclaw/storage/local_backend.py

   # Paste implementation code copied from documentation
   ```

3. **Update Factory Class**
   ```python
   # Add corresponding code in factory.py

   if backend_type == "local":
       from jiuwenclaw.storage.local_backend import LocalStorageBackend
       local_config = storage_config.get("local", {})
       return LocalStorageBackend(local_config)
   ```

4. **Update Exports**
   ```python
   # Add exports in __init__.py
   from jiuwenclaw.storage.local_backend import LocalStorageBackend

   __all__ = [..., "LocalStorageBackend"]
   ```

### Best Practices

#### Error Handling

```python
def _create_client(self):
    """Create client - complete error handling"""
    try:
        import some_storage_sdk

        client = some_storage_sdk.Client(
            key=self.access_key,
            secret=self.secret_key
        )
        self.logger.info(f"{self.__class__.__name__} client created")
        return client

    except ImportError as e:
        raise ImportError(
            f"Please install {self.__class__.__name__} SDK: pip install some_storage_sdk"
        ) from None
    except Exception as e:
        raise UploadError(f"Failed to create {self.__class__.__name__} client: {e}") from e
```

#### Connection Testing

```python
def _test_connection(self):
    """Connection test - friendly error messages"""
    try:
        client = self._get_client()
        # Perform simple connection test operation
        if hasattr(client, 'list_buckets'):
            client.list_buckets()  # S3/OBS/OSS
        elif hasattr(client, 'get_container_properties'):
            client.get_container_properties()  # Azure
        else:
            self.logger.info("Skip connection test (no available test method)")

    except Exception as e:
        self.logger.warning(f"{self.__class__.__name__} connection test failed: {e}")
        # Don't throw exception, allow retry
```

#### URI Parsing

```python
def _parse_uri(self, uri: str) -> str:
    """URI parsing - support multiple formats"""
    # Option 1: Standard format
    # https://bucket.endpoint/key
    standard_prefix = f"https://{self.bucket}.{self.endpoint}/"
    if uri.startswith(standard_prefix):
        return uri[len(standard_prefix):]

    # Option 2: Path format
    # https://endpoint/bucket/key
    path_prefix = f"https://{self.endpoint}/{self.bucket}/"
    if uri.startswith(path_prefix):
        return uri[len(path_prefix):]

    # Option 3: Custom format
    # s3://bucket/key
    if uri.startswith(f"s3://{self.bucket}/"):
        return uri[len(f"s3://{self.bucket}/"):]

    raise ValueError(f"Invalid {self.__class__.__name__} URI: {uri}")
```

### Test Examples

```python
"""Storage backend unit tests"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from jiuwenclaw.storage.s3_backend import S3StorageBackend


@pytest.fixture
def s3_config():
    return {
        "access_key": "test_key",
        "secret_key": "test_secret",
        "bucket": "test_bucket",
        "region": "us-east-1"
    }


@pytest.fixture
def s3_backend(s3_config):
    """Create S3 backend instance"""
    with patch('boto3.client'):
        backend = S3StorageBackend(s3_config)
        return backend


def test_validate_config(s3_config):
    """Test configuration validation"""
    # Missing required field
    with pytest.raises(ValueError, match="Configuration incomplete"):
        invalid_config = {"access_key": "test"}
        S3StorageBackend(invalid_config)


@pytest.mark.asyncio
async def test_upload_file(s3_backend):
    """Test file upload"""
    with patch.object(s3_backend, '_get_client') as mock_client:
        mock_client.return_value.upload_file = MagicMock()

        # Mock file exists
        with patch('pathlib.Path.exists', return_value=True):
            uri = await s3_backend.upload_file(
                "/tmp/test.pdf",
                "user123",
                "chat456",
                "web"
            )

        # Verify return URI format
        assert uri.startswith("https://test_bucket.s3.us-east-1.amazonaws.com/")
        assert "user123" in uri
        assert "web_chat456" in uri
```

## Refactoring Notes (v2.0)

### Refactoring Goals

Completed storage module refactoring following the simplified approach, moving all concrete implementations to this documentation, keeping only abstract base class code.

### Core Improvements

1. **🏗️ Abstract Base Class `BaseStorageBackend`**
   - Unified configuration validation (ak/sk validation)
   - Template method pattern reduces code duplication
   - Lazy-loading clients + connection testing
   - Backward compatible (`StorageBackend` as alias)

2. **📚 Documented Implementation Examples**
   - All concrete implementations moved to this documentation
   - Provides complete commercial implementation examples
   - Supports easy extension of new storage backends

3. **🔧 Code Simplification**
   - Deleted all concrete implementation files
   - Only keep abstract base class and utility functions
   - Factory class only provides framework, no concrete implementations

### v2.0 Major Changes

#### 1. Interface Signature Changes

**Before** (v1.0):
```python
async def upload_file(
    self,
    local_path: str,
    user_id: str
) -> str:
```

**After** (v2.0):
```python
async def upload_file(
    self,
    local_path: str,
    user_id: str,
    chat_id: str,        # New: Chat ID
    channel_type: str    # New: Channel type
) -> str:
```

#### 2. New File Management Methods

```python
async def delete_chat_files(
    self,
    user_id: str,
    chat_id: str,
    channel_type: str,
    older_than_hours: Optional[int] = None
) -> int:
    """Delete files for a specific chat"""

async def delete_user_files(
    self,
    user_id: str,
    older_than_hours: int = 24
) -> int:
    """Delete user files"""
```

#### 3. File Isolation Mechanism Upgrade

- **v1.0**: Only use `user_id` for isolation
- **v2.0**: Use `user_id + chat_id + channel_type` for isolation
- **Benefits**: Supports session-level file management and cleanup

#### 4. Code Organization Changes

**Deleted Files**:
- `jiuwenclaw/storage/local_backend.py`
- `jiuwenclaw/storage/obs_backend.py`
- `jiuwenclaw/storage/oss_backend.py`

**Retained Files**:
- `jiuwenclaw/storage/backend.py` - Abstract base class
- `jiuwenclaw/storage/factory.py` - Factory class framework
- `jiuwenclaw/storage/utils.py` - Utility functions
- `jiuwenclaw/storage/exceptions.py` - Exception definitions

#### 5. Export Changes

**Before**:
```python
from jiuwenclaw.storage import (
    StorageBackend,
    LocalStorageBackend,
    ObsStorageBackend,
    OssStorageBackend,
)
```

**After**:
```python
from jiuwenclaw.storage import (
    BaseStorageBackend,  # New base class name
    StorageBackend,      # Backward compatible: alias
    StorageService,      # Factory class
)
```

### Usage

#### Commercial Scenario Implementation

1. **Copy Example Code**
   ```bash
   # Copy required implementation examples from this documentation
   # For example: Copy LocalStorageBackend implementation
   ```

2. **Create Implementation File**
   ```python
   # jiuwenclaw/storage/local_backend.py

   from jiuwenclaw.storage import BaseStorageBackend
   # Paste implementation code copied from this documentation...
   ```

3. **Update Factory Class**
   ```python
   # Uncomment corresponding code in factory.py
   if backend_type == "local":
       from jiuwenclaw.storage.local_backend import LocalStorageBackend
       local_config = storage_config.get("local", {})
       return LocalStorageBackend(local_config)
   ```

4. **Update Exports**
   ```python
   # Add exports in __init__.py
   from jiuwenclaw.storage.local_backend import LocalStorageBackend

   __all__ = [..., "LocalStorageBackend"]
   ```

#### Custom Storage Backend

```python
from jiuwenclaw.storage import BaseStorageBackend

class MyStorageBackend(BaseStorageBackend):
    """My storage backend"""

    def _validate_config(self, config: dict) -> dict:
        """Configuration validation"""
        super()._validate_config(config)
        if not config.get("my_field"):
            raise ValueError("Missing my_field")
        return config

    def _create_client(self):
        """Create client"""
        return MyClient(...)

    # Implement core business methods
    async def upload_file(self, local_path, user_id, chat_id, channel_type) -> str:
        pass

    async def download_file(self, uri, local_path) -> None:
        pass

    async def delete_chat_files(self, user_id, chat_id, channel_type, older_than_hours=None) -> int:
        pass

    async def delete_user_files(self, user_id, older_than_hours=24) -> int:
        pass
```

## Related Documentation

## Feedback & Support

For questions or suggestions, please:
- Submit issues to GitHub repository
- Send emails to the development team
- Join community discussions
