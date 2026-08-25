"""Markdown AIGC decorator."""
import json
import os
import re
import stat

from decorators.common import get_aigc_signature, is_aigc_complete, parse_aigc_json


class MdAigcDecorator:
    """MD AIGC decorator - adds AIGC mark to Markdown files."""

    def __init__(self):
        self.name = "md_aigc_decorator"

    def _get_aigc_data(self, file_path: str) -> dict | None:
        """Read and parse AIGC metadata from Markdown YAML frontmatter."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
            if not match:
                return None
            frontmatter = match.group(1)
            if 'AIGC:' not in frontmatter:
                return None
            result = {}
            in_aigc = False
            for line in frontmatter.split('\n'):
                if line.strip() == 'AIGC:':
                    in_aigc = True
                    continue
                if in_aigc:
                    if not line.startswith('  ') or line.startswith('    '):
                        break
                    if ':' in line:
                        key, val = line.strip().split(':', 1)
                        result[key.strip()] = val.strip()
            return result if result else None
        except Exception:
            return None

    def decorate(self, file_path: str, content: str, add_visible_mark: bool = True):
        """Add AIGC mark to Markdown file."""
        existing = self._get_aigc_data(file_path)
        if existing and is_aigc_complete(existing):
            print(f"  [MD] AIGC mark complete, skipping")
            return
        try:
            aigc_signature = get_aigc_signature(content)
            metadata = json.loads(aigc_signature)
            self._add_aigc_mark(file_path, metadata, add_visible_mark)
            print(f"  [MD] AIGC mark added successfully")
        except Exception as e:
            print(f"  [MD] Warning: Failed to add AIGC mark: {str(e)}")

    def _add_aigc_mark(self, file_path, metadata: dict, add_visible_mark: bool = True):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                markdown_content = f.read()

            label = metadata.get("Label", "")
            content_producer = metadata.get("ContentProducer", "")
            produce_id = metadata.get("ProduceID", "")
            reserved_code1 = metadata.get("ReservedCode1", "")
            content_propagator = metadata.get("ContentPropagator", "")
            propagator_id = metadata.get("PropagateID", "")
            aigc_signature = (f"---\nAIGC:\n"
                              f"  Label: {label}\n"
                              f"  ContentProducer: {content_producer}\n"
                              f"  ProduceID: {produce_id}\n"
                              f"  ReservedCode1: {reserved_code1}\n"
                              f"  ContentPropagator: {content_propagator}\n"
                              f"  PropagateID: {propagator_id}\n---")

            # Remove existing AIGC frontmatter if present
            content_without_frontmatter = re.sub(
                r'^---\s*\nAIGC:\s*(?:\n\s+.*?:.*)*?\n---\s*\n',
                '',
                markdown_content,
                count=1,
                flags=re.MULTILINE,
            )

            if add_visible_mark:
                new_content = f"{aigc_signature}\n\n{content_without_frontmatter.lstrip()}\n\n内容由AI生成"
            else:
                new_content = f"{aigc_signature}\n\n{content_without_frontmatter.lstrip()}"

            file_flags = os.O_WRONLY | os.O_CREAT
            file_mode = stat.S_IWUSR | stat.S_IRUSR
            with os.fdopen(os.open(file_path, file_flags, file_mode), 'w', encoding='utf-8') as f:
                f.write(new_content)
        except Exception as e:
            print(f' [MD] add aigc mark error due to: {str(e)}')
