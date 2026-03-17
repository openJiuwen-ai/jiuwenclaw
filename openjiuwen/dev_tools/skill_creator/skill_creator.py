# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
SkillCreator - Use LLM to intelligently generate Skills
"""
import os
from pathlib import Path
from typing import Union

from dotenv import load_dotenv

from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.core.single_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig


class SkillCreator:
    agent: ReActAgent

    def __init__(self):
        pass

    async def create_agent(self):
        load_dotenv()
        skills_dir = Path(os.getenv("SKILLS_DIR", "openjiuwen/dev_tools/skill_creator/skills")).expanduser().resolve()
        files_base_dir = os.getenv("FILES_BASE_DIR", str(Path(__file__).resolve().parent))
        max_iterations = int(os.getenv("MAX_ITERATIONS", 25))

        api_base = os.getenv("API_BASE", "")
        api_key = os.getenv("API_KEY", "")
        model_name = os.getenv("MODEL_NAME", "")
        model_provider = os.getenv("MODEL_PROVIDER", "")
        verify_ssl = os.getenv("LLM_SSL_VERIFY", "False")

        # Construct agent instance
        self.agent = ReActAgent(card=AgentCard(name="skill_creator_agent", description="Skill Creator Agent"))

        # Create & configure agent
        system_prompt = (
            "You are an intelligent assistant.\n"
            f"All user-provided files are located at '{files_base_dir}'\n"
        )

        sysop_card = SysOperationCard(
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(work_dir=None),
        )
        Runner.resource_mgr.add_sys_operation(sysop_card)

        cfg = (ReActAgentConfig()
            .configure_model_client(
                    provider=model_provider,
                    api_key=api_key,
                    api_base=api_base,
                    model_name=model_name,
                    verify_ssl=verify_ssl,
                )
            .configure_prompt_template([{"role": "system", "content": system_prompt}])
            .configure_max_iterations(max_iterations)
            .configure_context_engine(
                    max_context_message_num=None,
                    default_window_round_num=None
            )
        )
        cfg.sys_operation_id = sysop_card.id
        self.agent.configure(cfg)

        # Add skills to agent.
        if skills_dir.exists():
            await self.agent.register_skill(str(skills_dir))
        else: 
            raise FileNotFoundError(f"Directory {skills_dir} does not exist.")
        
    async def generate(self, requirement: str, output_path: Union[str, Path]):
        output_path = Path(output_path)
        query = requirement + f"\nPut all generated files at {output_path}"
        
        res = await Runner.run_agent(
            agent=self.agent,
            inputs={"query": query, "conversation_id": "013"},
        )
        return res
