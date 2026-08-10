# CJK 引号紧邻加粗边界的改写选择映射设计

## 状态

- 设计已在 2026-08-10 的问题分析中获得方向确认。
- 本文用于实现前的书面复核。
- 实现分支：`codex/fix-cjk-bold-quote-selection`
- 基线提交：`7041c9f31666de274f0cf92b89cfcbc9f15121b1`

## 问题

OfficeClaw 前端用 `remark-cjk-friendly` 渲染 Markdown 并计算选择范围。当前
JiuwenClaw 后端用 markdown-it 的 CommonMark 规则重建可见文本。当中文正文中的
引号或其他 Unicode 标点紧贴 `**` 内侧时，两端会对同一段 Markdown 作出不同解释。

例如：

```markdown
这是**“重点”**内容
```

前端把两个 `**` 识别为加粗语法，发送的 `selected_text` 不含星号；后端可能把星号
当作字面文本。原始字节范围及其 SHA-256 校验能够通过，但后续可见文本校验会以
`SELECTION_MAPPING_CONFLICT` 拒绝请求。因此本问题不是哈希计算错误，而是解析语义
不一致。

## 目标

1. 后端在受影响的 CJK 加粗边界上与前端得到相同的可见文本和格式拓扑。
2. `**` 继续作为受保护语法锚点，不能进入模型可编辑文本。
3. 保持原始 UTF-8 字节范围、SHA-256、Protocol v2 和防篡改校验不变。
4. 将生产修改限制在 Markdown 改写映射器中，不修改前端生产代码和报告正文。

## 非目标

- 不改变 Markdown 文件内容或自动插入空格。
- 不放宽 `selected_text`、SHA-256、revision 或来源文件校验。
- 不改变代码块、链接、引用、图片和 HTML 的映射规则。
- 不在本次修改中对齐 `~~` 删除线；它使用独立的 markdown-it 规则。
- 不手工修改 OfficeClaw 中被忽略的 `vendor/jiuwenclaw` 目录。

## 方案

### 局部 CJK-friendly emphasis 规则

只在
`jiuwenclaw/agentserver/tools/deepresearch_plugin/markdown_rewrite_map.py`
中增加一个局部 emphasis tokenizer。它先沿用 markdown-it 的标准分隔符扫描结果，
再为前端 CJK-friendly 规则覆盖的边界补充开闭能力：

- 分隔符左侧是 CJK 字符、右侧是 Unicode 标点或符号时，可作为开始边界。
- 分隔符左侧是 Unicode 标点或符号、右侧是 CJK 字符时，可作为结束边界。
- 空白、转义、分隔符长度、三的倍数规则和下划线的词内限制继续使用 markdown-it
  的既有处理。

Unicode 标点或符号使用 Python 标准库 `unicodedata.category()` 的 `P*`/`S*`
分类；CJK 判断使用文件内私有帮助函数，不新增第三方依赖。实现只注册到
`build_rewrite_map()` 创建的 MarkdownIt 实例，不修改全局解析器状态。

markdown-it 现有 emphasis 后处理保持不变。匹配成功后，两个标记序列仍生成
`syntax` 类型的 `ProtectedAnchor`，中间可见内容生成带 `strong` 或 `em` 格式的
`RewriteSlot`。

### 数据流

1. 前端继续发送原始 UTF-8 半开区间、该区间 SHA-256 和渲染后的可见文本。
2. 后端先执行现有的字节边界和 SHA-256 校验。
3. `build_rewrite_map()` 用局部 CJK-friendly emphasis 规则解析当前 Markdown。
4. 后端按现有逻辑重建选择区间的可见文本并比较 `selected_text`。
5. 比较通过后只把可编辑 slot 交给模型；Markdown 标记仍不可编辑。

## 错误处理与安全约束

- 未闭合或无法与源字节对齐的标记继续降级为 `unsupported_inline`，保持 fail closed。
- 只有成对且满足边界条件的 emphasis 标记会改变解释；不会在比较阶段删除任意
  `**`，也不会信任客户端替代后端映射。
- 篡改 `source_sha256` 或 `selected_text` 仍返回 `SELECTION_MAPPING_CONFLICT`。
- 解析器扩展不读取外部数据，不改变持久化、运行配置或网络行为。

## 测试设计

### 映射器单元测试

覆盖以下输入，并断言可见 slot、`strong` 格式、两个语法锚点及 UTF-8 字节范围：

```markdown
这是“**重点**”内容
这是**“重点**”内容
这是“**重点”**内容
这是**“重点”**内容
```

同时包含 ASCII 引号、中文弯引号和已有数学符号边界用例。当前
`每周**≤2次**。` 的字面星号预期应调整为 CJK-friendly 加粗拓扑；
`x**≤**y` 等不满足 CJK 外侧条件的控制用例保持字面量行为。

### prepare 链路测试

使用真实原始字节切片计算 SHA-256，选择包含完整句子或跨越两个加粗标记的范围，
验证 prepare 成功且返回的 slot 不包含 `**`。同时保留现有哈希和可见文本篡改用例，
证明安全校验没有被放宽。

### 回归范围

实现前的相关基线为：

```text
272 passed
```

完成后必须重新运行：

```text
tests/unit/agentserver/test_markdown_rewrite_map.py
tests/unit/agentserver/test_deepresearch_document_rewrite.py
```

## 验收标准

1. 四种引号/加粗组合都能建立一致的后端 rewrite map。
2. prepare 不再因可见文本中的 `**` 差异拒绝合法选择。
3. 原始 SHA-256 和 UTF-8 范围计算代码零修改。
4. 加粗标记保持受保护，slot 保持正确的 `strong` 格式。
5. 现有相关测试和新增回归测试全部通过。
6. OfficeClaw 前端生产代码无改动；正式交付来源是 JiuwenClaw 分支，不是忽略的
   vendor 副本。

## 备选方案及否决理由

- 移除前端 CJK-friendly 插件：会恢复中文加粗显示问题。
- 比较前删除星号或直接信任客户端可见文本：会削弱防篡改与格式拓扑保护。
- 生成报告时插入空格：影响正文且不能覆盖旧文件、导入文件和手写 Markdown。
- 只枚举几种引号：无法覆盖其他全角标点及同类符号边界。
