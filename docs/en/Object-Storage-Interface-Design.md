# Object Storage Interface Design

## Overview

The JiuwenClaw Object Storage Interface is an **AgentServer internal component** that facilitates file interactions between AgentServer and various object storage services (Huawei Cloud OBS, Alibaba Cloud OSS, local filesystem, etc.).

### Core Positioning

**Important Notes**:
- ✅ **This is an AgentServer internal storage interface** that only handles file upload/download between AgentServer and object storage
- ❌ **Not Involved**: How Web clients upload or download files (handled by frontend team)
- ❌ **Not Provided**: HTTP endpoints or direct connections to Web clients
- ❌ **Not Changed**: Existing message flow (User → Channel → Gateway → AgentServer)

### Responsibility Boundary

```
┌─────────────────────────────────────────────────────────────┐
│                 Responsibility Boundaries                      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  [Frontend Team]              [AgentServer Storage Interface]│
│                                                             │
│  Web client uploads files      Download files from          │
│  to object storage            object storage to local       │
│  Web client downloads files   workspace                     │
│  from object storage          Upload local files to         │
│  Manage object storage        object storage                │
│  connections & auth                                          │
│                                                             │
│  [Existing System]            [Not Involved]                 │
│  - Message flow: User→Channel→Gateway→AgentServer           │
│  - E2AEnvelope message protocol                            │
│  - WebChannel's _process_files()                            │
│  - Gateway's FileTransferHandler                            │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Core Features

### 1. File Download Flow (Input Files)

```
Web Client (upload file to object storage, handled by frontend team)
         ↓
    Send file URI via WebSocket
         ↓
Gateway → AgentServer (receive E2AEnvelope.params.files)
         ↓
AgentServer Storage Interface (download to local workspace)
         ↓
Agent (use local file path)
```

**Use Cases**:
- Users upload images, documents, and other files for Agent analysis
- Support multiple files in a single conversation
- Support all file types

**Detailed Flow**:

```
┌─────────────────────────────────────────────────────────────┐
│          Input Flow: Object Storage → Agent Workspace          │
└─────────────────────────────────────────────────────────────┘

Frontend Team                  Our Code                 Agent
  │                               │                       │
  │ 1. Upload file to object       │                       │
  │    storage                    │                       │
  │    → Huawei Cloud OBS          │                       │
  │    → Alibaba Cloud OSS         │                       │
  │    → Local storage service     │                       │
  │                               │                       │
  │ 2. Send WebSocket message     │                       │
  │    {                           │                       │
  │      type: "req",             │                       │
  │      method: "chat.send",        │                       │
  │      params: {                 │                       │
  │        content: "Analyze image",│                       │
  │        files: [{                │                       │
  │          uri: "https://obs.../img.jpg" ← Object storage URI │
  │        }]                       │                       │
  │      }                          │                       │
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
                        │  │ Receive E2AEnvelope  │  │
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
Agent (generate file to local workspace)
         ↓
AgentServer Storage Interface (upload to object storage)
         ↓
AgentServer Response (return E2AResponse.files with URI)
         ↓
Gateway → Web Client (receive URI, download handled by frontend team)
```

**Use Cases**:
- Agent generates charts, reports, and other files to return to users
- Processed files need persistent storage

**Detailed Flow**:

```
┌─────────────────────────────────────────────────────────────┐
│           Output Flow: Agent Workspace → Object Storage        │
└─────────────────────────────────────────────────────────────┘

Agent                          Our Code              Frontend Team
  │                               │                       │
  │ 1. Generate file to local     │                       │
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
                        │  │ object storage       │  │
                        │  │ uri: "https://obs...  │  │
                        │  │     chart.png"        │  │
                        │  └─────────────────────┘  │
                        │                             │
                        │  ┌─────────────────────┐  │
                        │  │ Build E2AResponse    │  │
                        │  │   body.files: [{     │  │
                        │  │     uri: "https://obs...│  │
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
                        │   response              │
                        │   .files = [{uri: "..."}] │
                        │                           │
                        │  ┌─────────────────────┐ │
                        │  │ Frontend team       │ │
                        │  │  downloads file     │ │
                        │  │  from uri           │ │
                        │  └─────────────────────┘ │
                        └─────────────────────────┘
```

## Storage Backend Support

| Storage Backend | Type | Use Case | Config Key |
|----------------|------|----------|------------|
| LocalStorage | Local filesystem | Open-source version, development | `local` |
| ObsStorage | Huawei Cloud OBS | Commercial version, production | `huawei_obs` |
| OssStorage | Alibaba Cloud OSS | Commercial version, production | `aliyun_oss` |
| CosStorage | Tencent Cloud COS | Commercial version, production (optional) | `tencent_cos` |

## File Isolation Mechanism

### USER_ID Level Isolation

**Design Principle**: Only use USER_ID for file isolation, not SESSION_ID or CHAT_ID.

**Reasons**:
- Object storage URIs in historical messages already contain unique file identifiers
- Even if session_id changes, file URIs from old sessions remain accessible
- Simplify local path organization, avoiding complexity from session_id

```
Local file structure:
~/.jiuwenclaw/agent/jiuwenclaw_workspace/
└── files/
    └── {user_id}/          # User unique identifier
        ├── input/          # Input files (downloaded from object storage)
        │   ├── image_001.jpg
        │   └── document.pdf
        └── output/         # Output files (generated by Agent)
            ├── chart_001.png
            └── report.pdf

Object storage path structure (managed by frontend):
bucket: jiuwenclaw-data
└── files/
    └── {user_id}/
        └── {YYYYMMDD_HHMMSS}/  # Upload timestamp
            └── filename.ext

Example URI:
https://obs.../files/alice/20250511_143052/image.jpg
                    ↑       ↑
                  user_id  Timestamp
```

**Benefits**:
- Complete isolation between different users
- Simplified local file management
- Historical file URIs permanently accessible
- No dependency on session_id or chat_id

## Configuration Guide

### Basic Configuration

Add `storage` section to `config.yaml`:

```yaml
storage:
  # ============================================================
  # Object Storage Configuration (AgentServer Internal Use)
  # ============================================================
  # Storage type: local | huawei-obs | aliyun-oss
  type: ${STORAGE_TYPE:-local}

  # Local filesystem storage configuration (open-source version, development environment)
  local:
    base_dir: ${STORAGE_LOCAL_BASE_DIR:-~/.jiuwenclaw/storage/local}
    upload_dir: ${STORAGE_LOCAL_UPLOAD_DIR:-~/.jiuwenclaw/uploads}

  # Huawei Cloud OBS configuration (commercial version, production environment)
  huawei_obs:
    access_key: ${OBS_ACCESS_KEY:-}
    secret_key: ${OBS_SECRET_KEY:-}
    endpoint: ${OBS_ENDPOINT:-obs.cn-north-4.myhuaweicloud.com}
    bucket: ${OBS_BUCKET:-}

  # Alibaba Cloud OSS configuration (commercial version, production environment)
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
# === Storage Type Selection ===
# Options: local, huawei-obs, aliyun-oss
STORAGE_TYPE=local

# === Local Storage Configuration ===
# STORAGE_LOCAL_BASE_DIR=~/.jiuwenclaw/storage/local
# STORAGE_LOCAL_UPLOAD_DIR=~/.jiuwenclaw/uploads

# === Huawei Cloud OBS Configuration (Commercial Version) ===
# STORAGE_TYPE=huawei-obs
# OBS_ACCESS_KEY=your_access_key_id
# OBS_SECRET_KEY=your_secret_access_key
# OBS_ENDPOINT=obs.cn-north-4.myhuaweicloud.com
# OBS_BUCKET=your-bucket-name

# === Alibaba Cloud OSS Configuration (Commercial Version) ===
# STORAGE_TYPE=aliyun-oss
# OSS_ACCESS_KEY=your_access_key_id
# OSS_SECRET_KEY=your_secret_access_key
# OSS_ENDPOINT=oss-cn-hangzhou.aliyuncs.com
# OSS_BUCKET=your-bucket-name
```

**Configuration Priority**: Environment variables > config.yaml configuration > default values

**Environment Variable Template Location**: `jiuwenclaw/resources/.env.template`

**Configuration File Location**: `jiuwenclaw/resources/config.yaml`

## Core Interface

### StorageBackend Abstract Interface

```python
# jiuwenclaw/storage/backend.py

from abc import ABC, abstractmethod

class StorageBackend(ABC):
    """
    Object storage backend abstract interface

    Core responsibilities:
    1. download_file(): Download files from object storage URI to local workspace
    2. upload_file(): Upload local files to object storage, return URI

    Design principles:
    - Simple: Only two core methods
    - Internal: Only used within AgentServer
    - Stateless: No session state maintained
    """

    @abstractmethod
    async def download_file(
        self,
        uri: str,           # Object storage URI (from E2AEnvelope.params.files)
        local_path: str,    # Local save path (Agent workspace)
    ) -> None:
        """
        Download file from object storage to local workspace

        Supported URI formats:
            - https://obs... (Huawei Cloud OBS)
            - https://oss... (Alibaba Cloud OSS)
            - http://... (Local storage service)
            - file://... (Local filesystem)

        Use case:
            After AgentServer receives E2AEnvelope.params.files,
            needs to download files to workspace for Agent to use
        """
        pass

    @abstractmethod
    async def upload_file(
        self,
        local_path: str,    # Local file generated by Agent
        user_id: str,       # User ID (for path isolation)
    ) -> str:              # Return object storage URI
        """
        Upload local file to object storage

        Returns:
            Object storage URI (https://obs:// or https://oss://)

        Use case:
            After Agent generates files, needs to upload to object storage
            URI returned to frontend via E2AResponse
        """
        pass
```

### Factory Class

```python
# jiuwenclaw/storage/factory.py

class StorageService:
    """Storage service factory class (singleton)"""

    _instance = None

    @classmethod
    async def get_instance(cls) -> StorageBackend:
        """Get storage service instance (singleton)"""
        if cls._instance is None:
            cls._instance = await cls._create_backend()
        return cls._instance

    @classmethod
    async def _create_backend(cls) -> StorageBackend:
        """Create backend based on configuration"""
        from jiuwenclaw.config import get_config

        config = get_config()["storage"]
        backend_type = config.get("type", "local")

        if backend_type == "local":
            from jiuwenclaw.storage.local_backend import LocalStorageBackend
            return LocalStorageBackend(config["local"])

        elif backend_type == "huawei-obs":
            from jiuwenclaw.storage.obs_backend import ObsStorageBackend
            return ObsStorageBackend(config["huawei_obs"])

        elif backend_type == "aliyun-oss":
            from jiuwenclaw.storage.oss_backend import OssStorageBackend
            return OssStorageBackend(config["aliyun_oss"])

        else:
            raise ValueError(f"Unknown storage type: {backend_type}")
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
        Pre-process input files: Download from object storage to local workspace

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

            # Build local path (only user_id)
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
    ):
        """
        Post-process output files: Upload to object storage

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
                # Call storage interface to upload (only user_id)
                uri = await self._storage.upload_file(
                    local_path=local_path,
                    user_id=user_id
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
            'uri': 'https://obs.../files/alice/20250511_143052/input/image.jpg',
            'name': 'image.jpg'
        }]
    },
    context={
        'user_id': 'alice'
    }
)

# 1. Input file processing (download)
await self._prepare_input_files(envelope, workspace_dir)
# Result: params.files[0]['path'] = '~/.jiuwenclaw/agent/jiuwenclaw_workspace/files/alice/input/image.jpg'

# 2. Agent processing (use local file)
response = await self._agent.process(envelope.params)

# 3. Output file processing (upload)
if response.contains_files():
    await self._handle_output_files(response, 'alice')
    # Result: response.files = [{'path': '...', 'uri': 'https://obs.../files/alice/20250511_143105/output/result.png'}]

# 4. Return response to Gateway, Gateway passes to Web client
return response
```

## Developer Guide

### Extending New Storage Backends

To support a new object storage service, inherit from `StorageBackend` interface:

```python
# jiuwenclaw/storage/my_backend.py

from jiuwenclaw.storage.backend import StorageBackend

class MyStorageBackend(StorageBackend):
    """Custom storage backend"""

    def __init__(self, config: dict):
        # Initialize storage client
        self.client = MyStorageClient(
            key=config["access_key"],
            secret=config["secret_key"]
        )

    async def download_file(self, uri: str, local_path: str) -> None:
        """Implement download logic"""
        # 1. Parse object key from URI
        # 2. Download file to local path
        pass

    async def upload_file(
        self,
        local_path: str,
        user_id: str
    ) -> str:
        """Implement upload logic"""
        # 1. Upload local file to object storage
        # 2. Generate access URI
        # 3. Return URI
        pass
```

Then register in the factory class:

```python
# jiuwenclaw/storage/factory.py

async def _create_backend(cls) -> StorageBackend:
    config = get_config()["storage"]
    backend_type = config.get("type", "local")

    if backend_type == "my-storage":
        from jiuwenclaw.storage.my_backend import MyStorageBackend
        return MyStorageBackend(config["my_storage"])

    # ... other backends
```

### Unit Testing Example

```python
# tests/unit/test_storage_backend.py

import pytest
from jiuwenclaw.storage.local_backend import LocalStorageBackend

class TestLocalStorageBackend:

    @pytest.fixture
    def backend(self, tmp_path):
        config = {
            "base_dir": str(tmp_path / "storage"),
        }
        return LocalStorageBackend(config)

    @pytest.mark.asyncio
    async def test_download_file(self, backend):
        """Test file download"""
        # Prepare test files
        # ...

        # Execute download
        await backend.download_file(
            uri="file:///path/to/test.jpg",
            local_path="/tmp/test.jpg"
        )

        # Verify results
        assert Path("/tmp/test.jpg").exists()

    @pytest.mark.asyncio
    async def test_upload_file(self, backend):
        """Test file upload"""
        # Prepare local files
        # ...

        # Execute upload
        uri = await backend.upload_file(
            local_path="/tmp/test.jpg",
            user_id="test_user"
        )

        # Verify results
        assert uri is not None
        assert uri.startswith("http://") or uri.startswith("file://")
```

## Troubleshooting

### Common Issues

#### 1. File Download Failed

**Symptoms**: Agent cannot access uploaded files

**Troubleshooting Steps**:
1. Check if file URI is correct
2. Check if file exists
3. View AgentServer logs
4. Verify storage backend configuration

#### 2. File Upload Failed

**Symptoms**: Agent generated files cannot be returned to user

**Troubleshooting Steps**:
1. Check local file path
2. Check storage backend permissions
3. Check file size limit
4. View AgentServer logs

#### 3. Signed URL Not Accessible

**Symptoms**: Web client cannot download file via URL

**Troubleshooting Steps**:
1. Check if URL is expired
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
  max_concurrent_transfers: 5  # Concurrent upload/download count
```

### 2. File Size Limit

Configure file size limit:

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
- Show progress indication
- Support resumable transfers

## Security Recommendations

### 1. Access Key Management

- ✅ Use environment variables for keys
- ❌ Don't hardcode keys in config files
- ✅ Rotate keys regularly
- ✅ Follow principle of least privilege

### 2. Network Security

- ✅ Use HTTPS in production
- ✅ Configure bucket policies to restrict access
- ✅ Use signed URLs (temporary access)

### 3. File Validation

- ✅ Validate file types
- ✅ Limit file sizes
- ✅ Scan for malicious files

### 4. Access Control

- Only process files belonging to that user/session
- Verify URI legitimacy
- Limit file sizes

## Version Compatibility

| JiuwenClaw Version | Storage Interface Version | Compatibility Notes |
|-------------------|-------------------------|-------------------|
| v0.1.x            | -                       | Not supported |
| v0.2.0+           | 1.0                     | First release |

### Backward Compatibility

- Don't modify existing file processing logic
- New features enabled through configuration
- Keep fallback options

### Migration Path

```
Phase 1: Add storage module (optional)
├─ Configure storage.type=local
└─ Disabled by default, enabled via configuration

Phase 2: Gradually integrate into AgentServer
├─ Call storage interface in process_message
├─ Keep original logic as fallback
└─ Migrate gradually

Phase 3: Commercial version enables OBS/OSS
├─ Configure storage.type=huawei-obs
└─ Full functionality
```

## Related Documentation

- [Design Document](../../openspec/changes/storage-interface/design.md) - Detailed technical design
- [Implementation Tasks](../../openspec/changes/storage-interface/tasks.md) - Development task list
- [Change Proposal](../../openspec/changes/storage-interface/proposal.md) - Change proposal
- [E2A Protocol](../E2A-protocol.md) - Message protocol specification
- [Configuration Guide](../Configuration.md) - System configuration guide

## Feedback & Support

For questions or suggestions:
- Submit issues to GitHub repository
- Send email to development team
- Join community discussions
