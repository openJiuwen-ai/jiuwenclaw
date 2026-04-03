# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""CLI 文件服务 - 处理 /view 和 /ls 命令"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from jiuwenclaw.utils import get_agent_root_dir


@dataclass
class FileResult:
    success: bool
    content: str = ""
    error: str = ""
    metadata: Optional[dict] = None


class CLIFileService:
    """AgentService 端的文件操作服务"""
    
    MAX_FILE_SIZE = 1024 * 1024
    MAX_DISPLAY_LINES = 500
    ALLOWED_EXTENSIONS = {
        '.txt', '.md', '.mdx', '.json', '.yaml', '.yml',
        '.py', '.js', '.ts', '.tsx', '.jsx', '.html', '.css',
        '.sh', '.bash', '.zsh', '.bat', '.ps1',
        '.xml', '.toml', '.ini', '.cfg', '.conf',
        '.log', '.csv', '.env', '.markdown'
    }
    
    def __init__(self, session_id: Optional[str] = None):
        self.session_id = session_id
        self.agent_root = get_agent_root_dir()
    
    def resolve_path(self, rel_path: str) -> Path:
        """解析相对路径为绝对路径"""
        rel_path = rel_path.strip()
        norm = rel_path.replace("\\", "/").strip()

        # 允许绝对路径（最终仍会被 is_path_allowed() 限制）
        try:
            p = Path(rel_path)
            if p.is_absolute():
                return p.resolve()
        except Exception:
            # Path 解析异常时退回到后续拼接逻辑
            pass

        if not norm or norm == ".":
            return self.agent_root.resolve()

        # 统一去掉开头的 "./" 与多余的前导 "/"
        if norm.startswith("./"):
            norm = norm[2:]
        norm = norm.lstrip("/")

        return (self.agent_root / norm).resolve()
    
    def is_path_allowed(self, path: Path) -> bool:
        """检查路径是否允许访问"""
        try:
            path_str = str(path.resolve())
            agent_root_str = str(self.agent_root.resolve())
            
            return path_str.startswith(agent_root_str)
        except Exception:
            return False
    
    def handle_view_command(self, path: str, params: Optional[dict] = None) -> FileResult:
        """处理 /view 命令"""
        if params is None:
            params = {}
        
        from_line = params.get('from_line', 1)
        lines = params.get('lines')
        
        try:
            full_path = self.resolve_path(path)
        except Exception as e:
            return FileResult(success=False, error=f"路径解析失败: {e}")
        if not self.is_path_allowed(full_path):
            return FileResult(success=False, error="路径不在允许访问范围内")
        
        if not full_path.exists():
            return FileResult(success=False, error=f"文件不存在: {path}")
        
        if not full_path.is_file():
            return FileResult(success=False, error=f"不是文件: {path}")
        
        file_size = full_path.stat().st_size
        if file_size > self.MAX_FILE_SIZE:
            return FileResult(
                success=False,
                error=f"文件过大 ({file_size / 1024:.1f}KB)，超过限制 (1MB)"
            )
        
        ext = full_path.suffix.lower()
        if ext not in self.ALLOWED_EXTENSIONS:
            return FileResult(success=False, error=f"不支持的文件类型: {ext or '(无扩展名)'}")
        
        try:
            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                all_lines = f.readlines()
        except Exception as e:
            return FileResult(success=False, error=f"读取文件失败: {e}")
        
        total_lines = len(all_lines)
        
        start_idx = max(0, from_line - 1)
        if lines:
            end_idx = min(total_lines, start_idx + lines)
        else:
            end_idx = min(total_lines, start_idx + self.MAX_DISPLAY_LINES)
        
        content_lines = all_lines[start_idx:end_idx]
        
        numbered = []
        for i, line in enumerate(content_lines, start=start_idx + 1):
            numbered.append(f"{i:4d} | {line.rstrip()}")
        
        content = '\n'.join(numbered)
        
        summary = (
            f"\n\n---\n"
            f"📄 文件: `{path}`\n"
            f"📊 总行数: {total_lines}, 显示: {len(content_lines)} 行 "
            f"(第 {start_idx + 1}-{end_idx} 行)"
        )
        
        return FileResult(
            success=True,
            content=f"```\n{content}\n```{summary}",
            metadata={
                'total_lines': total_lines,
                'displayed_lines': len(content_lines),
                'start_line': start_idx + 1
            }
        )
    
    def handle_ls_command(self, path: str) -> FileResult:
        """处理 /ls 命令"""
        try:
            full_path = self.resolve_path(path)
        except Exception as e:
            return FileResult(success=False, error=f"路径解析失败: {e}")
        if not self.is_path_allowed(full_path):
            return FileResult(success=False, error="路径不在允许访问范围内")
        
        if not full_path.exists():
            return FileResult(success=False, error=f"目录不存在: {path}")
        
        if not full_path.is_dir():
            return FileResult(success=False, error=f"不是目录: {path}")
        
        try:
            entries = list(full_path.iterdir())
        except PermissionError:
            return FileResult(success=False, error=f"无权限访问目录: {path}")
        
        dirs = sorted([e for e in entries if e.is_dir()], key=lambda x: x.name.lower())
        files = sorted([e for e in entries if e.is_file()], key=lambda x: x.name.lower())
        
        lines = [f"📁 目录: `{path}`\n"]
        
        for d in dirs:
            lines.append(f"  📁 **{d.name}/**")
        
        for f in files:
            size = f.stat().st_size
            size_str = self._format_size(size)
            lines.append(f"  📄 {f.name} _({size_str})_")
        
        lines.append(f"\n📊 共 {len(dirs)} 个目录, {len(files)} 个文件")
        
        return FileResult(
            success=True,
            content='\n'.join(lines)
        )
    
    @staticmethod
    def _format_size(size: int) -> str:
        """格式化文件大小"""
        if size < 1024:
            return f"{size}B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f}KB"
        else:
            return f"{size / 1024 / 1024:.1f}MB"
