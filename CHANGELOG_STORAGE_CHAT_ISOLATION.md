# CHANGELOG - Storage Chat ID Isolation

## [2.0.0] - 2026-05-13

### 🚀 新增功能 - Storage Chat ID 隔离

#### 核心变更

- ✨ **新增**: 按 chat_id 隔离文件存储，支持会话级别文件管理
- ✨ **新增**: `delete_chat_files()` 方法，支持删除指定会话的文件
- ✨ **新增**: `delete_user_files()` 方法，支持批量清理用户过期文件
- ✨ **新增**: Channel 特定的 chat_id 提取逻辑
- ✨ **新增**: 文件路径工具函数模块 (`storage/utils.py`)

#### 支持的渠道

- 🌐 **WebChannel**: 使用 session_id 作为 chat_id
- 💬 **DingTalk**: 使用 conversation_id 作为 chat_id  
- 🤖 **XiaoYi**: 使用 session_id 作为 chat_id
- 💼 **Wecom**: 优先使用 metadata["wecom_chat_id"]

#### 文件路径格式变更

**旧格式**: `files/{user_id}/{timestamp}/{filename}`

**新格式**: `files/{user_id}/{channel_type}_{chat_id}/{timestamp}/{filename}`

**示例**:
- Web: `files/user123/web_sess_abc123/20250513_120000/document.pdf`
- DingTalk: `files/user123/dingtalk_cid456/20250513_130000/file.docx`

---

### 💔 破坏性变更

#### StorageBackend 接口变更

**`upload_file()` 方法签名变更**:

```python
# 旧接口 (已废弃)
async def upload_file(self, local_path: str, user_id: str) -> str

# 新接口
async def upload_file(
    self,
    local_path: str,
    user_id: str,
    chat_id: str,      # 新增必需参数
    channel_type: str  # 新增必需参数
) -> str
```

**影响范围**:
- `jiuwenclaw.storage.backend.StorageBackend`
- `jiuwenclaw.storage.local_backend.LocalStorageBackend`
- `jiuwenclaw.storage.oss_backend.OssStorageBackend`
- `jiuwenclaw.storage.obs_backend.ObsStorageBackend`
- `jiuwenclaw.agentserver.interface.JiuWenClaw.upload_agent_files()`

**迁移要求**:
- 所有调用 `upload_file()` 的代码必须更新为新接口
- 使用 `_extract_chat_id()` 和 `_extract_channel_type()` 辅助方法

#### 文件路径结构变更

- **旧文件**: 仍然可访问，但新接口不会创建旧格式路径
- **新文件**: 必须使用包含 chat_id 的新路径格式
- **兼容性**: 无自动迁移，保持双格式共存

---

### 🔧 工具函数

新增 `jiuwenclaw.storage.utils` 模块：

- **`sanitize_chat_id(chat_id, channel_type)`**: 清理 chat_id 特殊字符
- **`build_chat_prefix(channel_type, chat_id)`**: 构建路径前缀
- **`build_object_key(...)`**: 构建对象存储 Key

---

### 🧪 测试改进

- ✅ 新增 27 个工具函数单元测试
- ✅ 新增 8 个 Channel 验证集成测试  
- ✅ 新增 Backend mock 测试
- ✅ 所有现有测试通过，无回归

---

### 📚 文档更新

- ✅ 新增 [Storage Backend README](jiuwenclaw/storage/README.md)
- ✅ 新增 [迁移指南](docs/migration-guides/storage-chat-id-isolation.md)
- ✅ 更新 API 文档，说明新接口签名
- ✅ 新增最佳实践和使用示例

---

### ⚠️ 重要提示

#### 必需操作

1. **更新所有 `upload_file()` 调用**
   ```python
   # 旧代码会失败
   uri = await storage.upload_file(path, user_id)
   
   # 新代码
   uri = await storage.upload_file(path, user_id, chat_id, channel_type)
   ```

2. **实现 chat_id 提取逻辑**
   ```python
   chat_id = agent._extract_chat_id(request)
   channel_type = agent._extract_channel_type(request)
   ```

3. **更新相关测试代码**
   - 所有测试必须使用新接口签名
   - 添加 chat_id 和 channel_type 参数

#### 兼容性说明

- **向后兼容**: ❌ 否（破坏性变更）
- **数据迁移**: ❌ 不需要（旧文件保持原样）
- **服务依赖**: ❌ 无新增外部依赖

---

### 🐛 Bug 修复

- 修复 `interface.py` 中的 Python 语法错误
- 修复测试文件中的接口调用
- 改进错误处理和参数验证

---

### 🔄 升级建议

#### 开发环境

1. 更新代码到最新版本
2. 运行测试验证功能
3. 检查日志确认文件路径格式

#### 生产环境

1. **备份数据**: 确保重要文件已备份
2. **灰度发布**: 建议先在测试环境验证
3. **监控日志**: 关注文件操作异常
4. **回滚准备**: 准备回滚方案（如需要）

---

### 📞 获取帮助

- 📖 查看 [迁移指南](docs/migration-guides/storage-chat-id-isolation.md)
- 🔍 参考 [测试用例](tests/integration/test_channel_storage_isolation.py)
- 📋 阅读 [API 文档](jiuwenclaw/storage/README.md)

---

### 🙏 贡献者

感谢所有参与此次开发和测试的贡献者！

---

## [1.x] - 之前版本

### 旧版功能

- 基础文件上传/下载功能
- Local/OSS/OBS Backend 支持
- 用户级别文件隔离

---

**注意**: 这是一个重要的功能升级，包含破坏性变更。请仔细阅读迁移指南并充分测试后再部署到生产环境。