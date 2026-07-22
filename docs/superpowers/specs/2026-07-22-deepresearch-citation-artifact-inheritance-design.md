# DeepResearch 引用关联产物继承设计

## 目标

让初始 DeepResearch Markdown 和后续局部改写产生的 child Markdown 都能通过
`chat.file.metadata.artifactBundle` 找到同一份隐藏 `raw_report.md` 与
`citations.preview.json`，从而在 OfficeClaw 中持续展示片段式引用卡片。

公开文件列表只包含 MD、HTML 等用户产物。完整 `citations.json`、
`raw_report.md` 和 `citations.preview.json` 不进入可见文件列表。

## 已确认根因

DeepResearch Skill 已将 `raw_report_path` 与 `citations_preview_path` 写入完成
marker，但 JiuwenClaw 初始报告的 `chat.file` 只发送可见文件。局部改写的
`deepresearch_commit_rewrite` 同样只发送 child Markdown 路径。OfficeClaw 只能从
`send_file_ready.metadata.artifactBundle.relatedArtifacts` 查找引用预览文件，因此
关联在 JiuwenClaw 交付边界丢失。

## 方案选择

采用 provenance 继承方案：

1. 初始报告写入 provenance 时保存经过白名单过滤的引用关联路径。
2. 初始 `chat.file` 从同一份过滤结果构建 `artifactBundle`。
3. child provenance 继续使用现有的字典继承机制保留这些路径。
4. child 交付时从已生成的同名 provenance 读取路径并构建 `artifactBundle`。

不按 conversation ID 推导 Skill data 目录，也不让前端跨消息猜测父 revision。

## 数据结构

provenance 新增可选字段：

```json
{
  "citation_artifacts": {
    "raw_report_path": "/skill/data/C1.raw_report.md",
    "citations_preview_path": "/skill/data/C1.citations.preview.json"
  }
}
```

只接受非空字符串。完整 `citations_path` 不写入该字段，也不进入交付 metadata。
缺失或非法时保持原有 `chat.file` 形状，不阻断报告交付。

对应交付结构：

```json
{
  "schemaVersion": "1.0",
  "relatedArtifacts": [
    {
      "type": "raw_report",
      "path": "/skill/data/C1.raw_report.md",
      "contentType": "text/markdown",
      "relatedToPathIndex": 0
    },
    {
      "type": "citations_preview",
      "path": "/skill/data/C1.citations.preview.json",
      "contentType": "application/json",
      "schemaVersion": "1.1",
      "relatedToPathIndex": 0
    }
  ]
}
```

外层 bundle 版本保持 `1.0`，引用预览行版本固定为 `1.1`。

## 初始报告数据流

1. `deepresearch_stream` 读取完成 marker。
2. 将两个允许的隐藏路径规范化为 `citation_artifacts`。
3. 报告 writer 把它写入初始 provenance，同时保留当前 MD+HTML 生成行为。
4. 可见文件顺序不变；`relatedToPathIndex` 指向 MD 在 `files` 中的实际索引。
5. `chat.file` 携带 metadata，任务阶段更新和 tool outcome 保持不变。

## child 改写数据流

1. `commit_rewrite` 基于父 provenance 创建 child provenance；现有浅拷贝会继承
   `citation_artifacts`。
2. `deepresearch_commit_rewrite` 使用返回的 `provenance_path` 读取 child sidecar。
3. 只提取并校验 `citation_artifacts`，构建指向 child Markdown 索引 0 的 bundle。
4. `_deliver_report` 发送 child Markdown 与 metadata。
5. provenance 缺失、过大、JSON 非法或字段非法时，引用 metadata 安全降级为空，
   但不影响 child Markdown 交付。

## 安全与兼容边界

- 路径来自本次 Skill 完成 marker，只通过明确字段白名单进入 provenance。
- child 只读取 commit 刚生成的 provenance 路径，限制读取大小并要求 JSON object。
- 不把完整引用内容、URL 清单或 `citations.json` 放进消息 metadata。
- 不修改 Markdown 内容、引用顺序、revision hash 或现有引用完整性校验。
- 没有引用关联的普通报告保持原有 payload，不新增空 metadata。
- 已生成但 provenance 中没有该字段的旧报告不会自动恢复关联。

## 测试范围

JiuwenClaw 测试必须覆盖：

1. 初始报告同时交付 MD+HTML 时，隐藏关联只指向 MD 的实际索引。
2. `citations.json` 不进入 metadata 或可见文件列表。
3. 空白、缺失或非法路径保持旧 payload。
4. 初始 provenance 写入规范化 `citation_artifacts`。
5. child provenance 继承关联，child `chat.file` 携带相同 bundle。
6. child provenance 缺失、过大或非法时仍交付 Markdown，但不携带 metadata。
7. 现有报告生成、改写、任务阶段、MD+HTML 和引用完整性测试继续通过。

