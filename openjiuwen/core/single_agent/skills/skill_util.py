# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from pathlib import Path
from typing import TYPE_CHECKING

from openjiuwen.core.foundation.prompt import PromptTemplate
from openjiuwen.core.single_agent.skills.remote_skill_util import GitHubTree, RemoteSkillUtil
from openjiuwen.core.single_agent.skills.skill_manager import SkillManager

if TYPE_CHECKING:
    from openjiuwen.core.single_agent import BaseAgent

SKILL_PROMPT_CONTENT = '''
To help you better complete tasks, the following skill knowledge is equipped:
{{skills}}
You can use the read_file tool to read the corresponding SKILL.md file to obtain the relevant skill.
'''
skill_prompt = PromptTemplate(content=SKILL_PROMPT_CONTENT)


class SkillUtil:
    """Utility class for managing and working with skills.

    This class provides a high-level interface for skill registration, tool management,
    and prompt generation. It combines SkillManager and RemoteSkillUtil functionalities.
    """

    def __init__(self, sys_operation_id: str):
        """Initialize the skill utility.

        Args:
            sys_operation_id: The system operation ID used for file and code operations.
        """
        self._skill_manager = SkillManager(sys_operation_id)
        self._remote_skill_util = RemoteSkillUtil(sys_operation_id)

    def set_sys_operation_id(self, sys_operation_id: str) -> None:
        self.skill_manager.set_sys_operation_id(sys_operation_id)
        self.remote_skill_util.set_sys_operation_id(sys_operation_id)

    @property
    def skill_manager(self):
        """Get the skill manager instance.

        Returns:
            SkillManager: The skill manager instance.
        """
        return self._skill_manager

    @property
    def remote_skill_util(self):
        """Get the remote skill util instance.

        Returns:
            RemoteSkillUtil: The remote skill util instance.
        """
        return self._remote_skill_util

    async def register_skills(self, skill_path: str, agent: "BaseAgent", session_id: str = None) -> bool:
        """Register skills.

        This method registers the skill at the given path.
        Skill-related execution tools are expected to be provided by sys_operation
        and added externally (e.g. in main).

        Args:
            skill_path: The path to the skill directory to register.
            agent: The agent instance (kept for compatibility).
            session_id: The session ID for file operations. Defaults to None.

        Returns:
            bool: True if registration was successful.
        """
        await self._skill_manager.register(Path(skill_path), session_id)
        return True

    async def register_remote_skills(self, skills_dir: str, github_tree: GitHubTree, token: str = "") -> None:
        await self._remote_skill_util.upload_skill_from_github(
            tree=github_tree,
            skills_dir=skills_dir,
            token=token
        )

    def has_skill(self):
        """Check if any skills are registered.

        Returns:
            bool: True if at least one skill is registered, False otherwise.
        """
        return True if self._skill_manager.count() > 0 else False

    def get_skill_prompt(self) -> str:
        """Generate a formatted prompt string containing information about all registered skills.

        Returns:
            str: A formatted prompt string with skill information that can be used
                to inform agents about available skills.
        """
        system_prompt = (
            "You are an agent equipped with various skills to solve problems.\n"
            "Before attempting any task, read the relevant skill document (SKILL.md) "
            "using read_file and follow its workflow.\n"
        )
        skills = self._skill_manager.get_all()
        skills_info = []
        for index, skill in enumerate(skills):
            skills_info.append(
                f"{index}.Skill name: {skill.name}; "
                f"Skill description: {skill.description}; "
                f"Skill directory file path: {skill.directory}"
            )
        skill_text = skill_prompt.format({"skills": "\n".join(skills_info)}).content
        return system_prompt + "\n" + skill_text
