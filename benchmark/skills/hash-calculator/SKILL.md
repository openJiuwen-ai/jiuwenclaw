---
name: hash-calculator
description: >-
  Calculate cryptographic hash values for files or text strings.
  Supports MD5, SHA-1, SHA-256, SHA-512 algorithms.
  Use when user asks to compute file checksums, verify file integrity,
  or generate hash values. NOT for password hashing or encryption.
allowed_tools: [bash]
---

# Hash Calculator

计算文件或文本的哈希值。

## 执行方式

### 计算文件哈希

```bash
python scripts/hash_calc.py file <file_path> [--algorithm <algo>]
```

### 计算文本哈希

```bash
python scripts/hash_calc.py text <text_string> [--algorithm <algo>]
```

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `file` / `text` | 是 | 输入类型：文件或文本 |
| `file_path` / `text_string` | 是 | 文件路径或文本内容 |
| `--algorithm` | 否 | 哈希算法：`md5`、`sha1`、`sha256`（默认）、`sha512` |
| `--all` | 否 | 同时输出所有算法的结果 |

### 示例

```bash
# 计算文件的 SHA-256
python scripts/hash_calc.py file document.pdf

# 计算文本的 MD5
python scripts/hash_calc.py text "hello world" --algorithm md5

# 输出所有算法结果
python scripts/hash_calc.py file archive.tar.gz --all
```

## 输出格式

### 单算法

```
文件: document.pdf
SHA-256: a3f2b8c1d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0
大小: 1,024,576 bytes
```

### 全部算法

```
文件: archive.tar.gz
MD5:    a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6
SHA-1:  a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0
SHA-256: a3f2b8c1d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0
SHA-512: (128-char hex string)
大小: 10,485,760 bytes
```

## 校验模式

比对两个文件的哈希是否一致：

```bash
python scripts/hash_calc.py verify <file_a> <file_b> [--algorithm <algo>]
```

输出：
```
✅ 文件一致 (SHA-256: a3f2b8c1...)
```
或
```
❌ 文件不一致
  file_a: a3f2b8c1...
  file_b: b4c3d2e1...
```

## 注意事项

- 大文件（>1GB）使用分块读取（8KB chunk），内存友好
- 哈希输出为小写十六进制
- 默认算法 SHA-256 是当前的推荐标准
