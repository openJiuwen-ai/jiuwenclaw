# Connection Identity Extension Module Design

## Requirements Summary

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      Requirements Summary                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ✓ Trigger: AgentWebSocketServer._connection_handler() (AgentServer)  │
│  ✓ Failure handling: Allow connection to continue, log fields null    │
│  ✓ Multi-tenancy: Sandbox mode, one AgentServer per sandbox           │
│  ✓ Field names: user_id / domain_id / app_id                          │
│  ✓ Extension directory: jiuwenclaw/extensions/identity_provider/      │
│  ✓ Business layer: No extension.yaml, direct code integration          │
│  ✓ Log format: Both text and JSON supported                            │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

## File Structure

```
jiuwenclaw/
├── extensions/
│   └── identity_provider/           ← New directory
│       ├── __init__.py               ← Module exports
│       ├── base.py                   ← IdentityProviderBase abstract class
│       ├── store.py                  ← IdentityStore singleton
│       └── types.py                  ← IdentityInfo data structure
│
├── utils.py                          ← Modified: Add IdentityFieldFilter, IdentityTextFormatter
│                                     ← Modified: JsonUserVisibleFormatter extension
│
├── agentserver/
│   └── agent_ws_server.py            ← Modified: Trigger identity fetch on connection
│
└── extensions/
│   └── registry.py                   ← Modified: Add extension registration methods
```

## Module Design Details

### 1. extensions/identity_provider/types.py - Data Structure

```python
from dataclasses import dataclass, field
from typing import Any

@dataclass
class IdentityInfo:
    """Identity information data structure"""
    user_id: str | None = None
    domain_id: str | None = None
    app_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict, only non-None fields included"""
        result = {}
        if self.user_id is not None:
            result["user_id"] = self.user_id
        if self.domain_id is not None:
            result["domain_id"] = self.domain_id
        if self.app_id is not None:
            result["app_id"] = self.app_id
        if self.extra:
            result["extra"] = self.extra
        return result
```

### 2. extensions/identity_provider/base.py - Abstract Base Class

```python
from abc import ABC, abstractmethod

class IdentityProviderBase(ABC):
    """Abstract base class for identity providers
    
    Business layer must inherit this class and implement fetch_identity().
    Register via IdentityStore.register_provider() before starting jiuwenclaw.
    """
    
    @abstractmethod
    async def fetch_identity(self) -> IdentityInfo:
        """Fetch identity information
        
        Business layer implements this method to return User ID, Domain ID, App ID.
        Implementation is flexible:
        - Call external API
        - Read config file
        - Get from environment variables
        - Query database
        
        Returns:
            IdentityInfo: Identity object, fields can be None
        """
        ...
    
    async def on_fetch_failed(self, error: Exception) -> IdentityInfo | None:
        """Callback on fetch failure (optional)
        
        Default returns None (allows connection to continue).
        Business layer can override for custom failure handling or fallback identity.
        
        Args:
            error: Exception from fetch_identity
            
        Returns:
            IdentityInfo | None: None allows connection to continue
        """
        return None
```

### 3. extensions/identity_provider/store.py - Singleton Store

```python
import logging

class IdentityStore:
    """Global identity store (singleton pattern)
    
    Stores identity fetched by AgentServer, read by logging system.
    """
    
    _instance: IdentityStore | None = None
    
    def __init__(self) -> None:
        self._identity: IdentityInfo | None = None
        self._provider: IdentityProviderBase | None = None
        self._fetched: bool = False
    
    @classmethod
    def get_instance(cls) -> IdentityStore:
        """Get singleton instance"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (for testing)"""
        cls._instance = None
    
    def register_provider(self, provider: IdentityProviderBase) -> None:
        """Register identity provider"""
        self._provider = provider
    
    def unregister_provider(self) -> None:
        """Unregister identity provider"""
        self._provider = None
        self._identity = None
        self._fetched = False
    
    def get_identity(self) -> IdentityInfo | None:
        """Get current identity (for logging system)"""
        return self._identity
    
    def is_fetched(self) -> bool:
        """Whether identity has been fetched"""
        return self._fetched
    
    async def fetch_and_store(self) -> IdentityInfo | None:
        """Fetch identity via provider and store
        
        On failure, calls on_fetch_failed callback.
        If fallback identity returned, stores it; otherwise continues connection.
        """
        if self._provider is None:
            self._fetched = True
            return None
        
        try:
            identity = await self._provider.fetch_identity()
            self._identity = identity
            self._fetched = True
            return identity
        except Exception as e:
            self._fetched = True
            fallback = await self._provider.on_fetch_failed(e)
            if fallback is not None:
                self._identity = fallback
            return self._identity
```

### 4. utils.py Modifications - Logging Components

#### IdentityFieldFilter - Log Filter

```python
class IdentityFieldFilter(logging.Filter):
    """Identity field filter
    
    Automatically adds user_id, domain_id, app_id fields to each log record.
    Reads from IdentityStore singleton.
    """
    
    def filter(self, record: logging.LogRecord) -> bool:
        identity = IdentityStore.get_instance().get_identity()
        if identity is not None:
            record.user_id = identity.user_id
            record.domain_id = identity.domain_id
            record.app_id = identity.app_id
        else:
            record.user_id = None
            record.domain_id = None
            record.app_id = None
        return True
```

#### IdentityTextFormatter - Text Format Formatter

```python
class IdentityTextFormatter(logging.Formatter):
    """Text formatter supporting identity fields
    
    Adds identity fields to text logs, only outputs when values exist.
    Format: user_id=xxx domain_id=xxx app_id=xxx
    """
    
    def format(self, record: logging.LogRecord) -> str:
        # Build identity string
        identity_parts = []
        if record.user_id is not None:
            identity_parts.append(f"user_id={record.user_id}")
        if record.domain_id is not None:
            identity_parts.append(f"domain_id={record.domain_id}")
        if record.app_id is not None:
            identity_parts.append(f"app_id={record.app_id}")
        
        # For format string %(identity_str)s
        record.identity_str = " " + " ".join(identity_parts) + " " if identity_parts else ""
        return super().format(record)
```

#### JsonUserVisibleFormatter Extension

Modified `add_fields()` method to handle identity fields:

```python
# Add identity fields (user_id, domain_id, app_id)
user_id = getattr(record, 'user_id', None)
domain_id = getattr(record, 'domain_id', None)
app_id = getattr(record, 'app_id', None)
# Only add non-None fields, keep JSON clean
if user_id is not None:
    ordered_record['user_id'] = user_id
if domain_id is not None:
    ordered_record['domain_id'] = domain_id
if app_id is not None:
    ordered_record['app_id'] = app_id

# Filter null identity fields when adding other fields
identity_keys = {'user_id', 'domain_id', 'app_id'}
for key, value in log_record.items():
    if key not in ordered_record:
        if key in identity_keys and value is None:
            continue  # Skip null identity fields
        ordered_record[key] = value
```

### 5. agent_ws_server.py Modification - Trigger Point

```python
async def _connection_handler(self, ws: Any) -> None:
    """Handle Gateway WebSocket connection"""
    # ... initialization code ...
    
    # Trigger identity fetch (before sending connection.ack)
    try:
        from jiuwenclaw.extensions.identity_provider import IdentityStore
        identity = await IdentityStore.get_instance().fetch_and_store()
        if identity is not None:
            logger.info(
                "[AgentWebSocketServer] Identity fetched: user_id=%s domain_id=%s app_id=%s",
                identity.user_id, identity.domain_id, identity.app_id,
            )
    except Exception as e:
        logger.warning("[AgentWebSocketServer] Identity fetch exception: %s", e)
    
    # Send connection.ack
    ack_frame = {"type": "event", "event": "connection.ack", "payload": {"status": "ready"}}
    await ws.send(json.dumps(ack_frame, ensure_ascii=False))
    
    # ... subsequent processing ...
```

### 6. extensions/registry.py Modification - Extension Registration

```python
def register_identity_provider(self, provider: IdentityProviderBase) -> None:
    """Register identity provider"""
    from jiuwenclaw.extensions.identity_provider import IdentityStore
    IdentityStore.get_instance().register_provider(provider)

def get_identity_provider(self) -> IdentityProviderBase | None:
    """Get registered identity provider"""
    from jiuwenclaw.extensions.identity_provider import IdentityStore
    return IdentityStore.get_instance()._provider
```

## Business Layer Integration Example

```python
# Business layer code (no jiuwenclaw modification)

from jiuwenclaw.extensions.identity_provider import (
    IdentityProviderBase,
    IdentityInfo,
    IdentityStore,
)
import httpx

class MyIdentityProvider(IdentityProviderBase):
    """Business layer identity provider implementation"""
    
    def __init__(self, api_url: str):
        self._api_url = api_url
        self._client = httpx.AsyncClient()
    
    async def fetch_identity(self) -> IdentityInfo:
        """Call internal API to fetch identity"""
        resp = await self._client.get(self._api_url, timeout=5.0)
        data = resp.json()
        return IdentityInfo(
            user_id=data.get("user_id"),
            domain_id=data.get("domain_id"),
            app_id=data.get("app_id"),
        )
    
    async def on_fetch_failed(self, error: Exception) -> IdentityInfo | None:
        """Return default value or None on failure"""
        return IdentityInfo()  # All fields None


# Register before starting jiuwenclaw
provider = MyIdentityProvider("https://internal-api.example.com/identity")
IdentityStore.get_instance().register_provider(provider)

# Start AgentServer
# provider.fetch_identity() is called automatically when Gateway connects
```

## Log Output Examples

### JSON Format (with identity)

```json
{
  "timestamp": "2026-05-21 10:30:22.537",
  "process": 12345,
  "level": "INFO",
  "user_tag": "[USER] ",
  "user_id": "user-123",
  "domain_id": "domain-abc",
  "app_id": "app-xyz",
  "logger": "jiuwenclaw.gateway.message_handler",
  "lineno": 125,
  "message": "Message dispatched",
  "component": "gateway"
}
```

### JSON Format (without identity)

```json
{
  "timestamp": "2026-05-21 10:30:22.537",
  "process": 12345,
  "level": "INFO",
  "user_tag": "[USER] ",
  "logger": "jiuwenclaw.gateway.message_handler",
  "lineno": 125,
  "message": "Message dispatched",
  "component": "gateway"
}
```

Note: Without identity, user_id, domain_id, app_id fields are not included, keeping JSON clean.

### TEXT Format (with identity)

```
2026-05-21 10:30:22.537 [12345] INFO  user_id=user-123 domain_id=domain-abc app_id=app-xyz [USER] test.logger:125: Message dispatched
```

Note: Identity fields are between INFO level and user_tag. Empty when no identity.

## OpenSpec Change

Design recorded in OpenSpec Change: `connection-identity-extension`

Location: `openspec/changes/connection-identity-extension/`

Files:
- `proposal.md` - Motivation and scope
- `design.md` - Technical design details
- `specs/identity-provider/spec.md` - Identity extension specification
- `specs/logging-system/spec.md` - Logging system delta specification
- `tasks.md` - Implementation task list