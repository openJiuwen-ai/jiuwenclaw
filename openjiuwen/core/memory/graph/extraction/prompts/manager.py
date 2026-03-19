# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Prompt Manager

Thread-safe prompt template manager for loading and resolving extraction prompts.
"""

import glob
import os
import re
import threading
from typing import Any, Iterable, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import store_logger
from openjiuwen.core.foundation.prompt.template import PromptTemplate
from openjiuwen.core.runner.resources_manager.prompt_manager import PromptMgr

PR_PATTERN = re.compile(r"(?s)`#((?:user)|(?:system)|(?:assistant)|(?:tool))#`")


class ThreadSafePromptManager:
    """Prompt Template Manager"""

    __slots__ = ("_initialized", "_all_prompt_names", "_mgr")
    _instance: Optional["ThreadSafePromptManager"] = None
    thread_lock: threading.RLock = threading.RLock()

    def __new__(cls) -> "ThreadSafePromptManager":
        with cls.thread_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._all_prompt_names: set[str] = set()
        self._mgr = PromptMgr()
        prompt_root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "**", "*.pr.md"))
        language_dirs = set()
        for t_path in glob.glob(prompt_root_dir, recursive=True):
            language_dirs.add(os.path.dirname(t_path))
        for t_dir in language_dirs:
            self.register_in_bulk(t_dir, name=os.path.split(t_dir)[1].strip(os.path.sep))
        self._initialized = True

    def __contains__(self, key: Any) -> bool:
        return key in self._all_prompt_names

    @staticmethod
    def load_pr_content(content: str) -> list[dict[str, str]]:
        """Load prompt template from .pr.md files"""
        roles = {"user", "system", "assistant", "tool"}
        matching_role = True
        current_msg: dict[str, str] = {}
        messages = []
        for line in PR_PATTERN.split(content):
            if not line:
                continue
            if matching_role:
                if line in roles:
                    current_msg = dict(role=line, content="")
                    matching_role = False
            else:
                current_msg["content"] = line
                messages.append(current_msg)
                current_msg = None
                matching_role = True
        return messages

    def get(self, name: str) -> Optional[PromptTemplate]:
        """Get registered prompt template"""
        with self.__class__.thread_lock:
            return self._mgr.get_prompt(name)

    def register_in_bulk(self, prompt_dir: str, name: str = ""):
        """Register prompt templates in bulk

        Args:
            prompt_dir (str): directory containing prompts
            name (str, optional): name for the group of prompt templates to register. Defaults to "".

        Raises error:
            - no .pr.md files found in selected directory
        """
        with self.__class__.thread_lock:
            prompt_root_dir = os.path.abspath(os.path.join(prompt_dir, "*.pr.md"))
            prompt_paths = glob.glob(prompt_root_dir, recursive=True)
            if not prompt_paths:
                raise build_error(
                    StatusCode.MEMORY_GRAPH_PROMPT_FILES_MISSING,
                    prompt_dir=prompt_dir,
                )
            self.__register_templates(prompt_paths)
            if not name:
                name = f"{os.path.basename(prompt_dir)}"
        store_logger.info("Graph Memory: loaded %d prompts from %s", len(prompt_paths), name)

    def __register_templates(self, template_paths: Iterable[str]):
        prompts = []
        for t_path in template_paths:
            t_name = os.path.split(t_path)[1].removesuffix(".pr.md")
            with open(t_path, "r", encoding="utf-8") as prompt_file:
                t_content = self.load_pr_content(prompt_file.read())
            self._all_prompt_names.add(t_name)
            prompts.append((t_name, PromptTemplate(name=t_name, content=t_content)))
        self._mgr.add_prompts(prompts)
