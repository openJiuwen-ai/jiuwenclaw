from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from jiuwenclaw.agentserver.replan_agent.plan_node import PlanNode
from jiuwenclaw.agentserver.replan_agent.skill_codes.ppt.pipeline_init import PipelineInitNode
from jiuwenclaw.agentserver.replan_agent.skill_codes.ppt.intent_classify import IntentClassifyNode
from jiuwenclaw.agentserver.replan_agent.skill_codes.ppt.requirement_collect import RequirementCollectNode
from jiuwenclaw.agentserver.replan_agent.skill_codes.ppt.document_parse import DocumentParseNode
from jiuwenclaw.agentserver.replan_agent.skill_codes.ppt.template_context import TemplateContextNode
from jiuwenclaw.agentserver.replan_agent.skill_codes.ppt.content_plan import ContentPlanNode
from jiuwenclaw.agentserver.replan_agent.skill_codes.ppt.outline_review import OutlineReviewNode
from jiuwenclaw.agentserver.replan_agent.skill_codes.ppt.deep_research import DeepResearchNode
from jiuwenclaw.agentserver.replan_agent.skill_codes.ppt.style_prepare import StylePrepareNode
from jiuwenclaw.agentserver.replan_agent.skill_codes.ppt.image_prepare import ImagePrepareNode
from jiuwenclaw.agentserver.replan_agent.skill_codes.ppt.ppt_page_gen import PPTPageGenNode
from jiuwenclaw.agentserver.replan_agent.skill_codes.ppt.ppt_export import PPTExportNode
from jiuwenclaw.agentserver.replan_agent.skill_codes.ppt.delivery import DeliveryNode

_P3_SKIP_MESSAGE = "未检测到待解析文档，跳过文档解析"
_P3_SKIP_FIELDS = {
    "doc_parse_ok": False,
    "doc_parse_error": None,
    "topic": "",
    "topic_inferred": False,
}


def _merge_subplan_result(inputs: dict[str, Any], result: Any) -> None:
    if not isinstance(result, dict):
        return
    inputs.update(
        {
            key: value
            for key, value in result.items()
            if key not in {"node", "status", "message", "content"}
        }
    )


def _result_status(result: Any) -> str:
    if isinstance(result, dict):
        return str(result.get("status", "ok"))
    return "ok"


class PPTGenRootNode(PlanNode):
    def __init__(self) -> None:
        self._p0 = PipelineInitNode()
        self._p1 = IntentClassifyNode()
        self._p2 = RequirementCollectNode()
        self._p3 = DocumentParseNode()
        self._p3_5 = TemplateContextNode()
        self._tail_plans = [
            ContentPlanNode(),
            OutlineReviewNode(),
            DeepResearchNode(),
            StylePrepareNode(),
            ImagePrepareNode(),
            PPTPageGenNode(),
            PPTExportNode(),
            DeliveryNode(),
        ]
        super().__init__(
            plan_name="ppt_gen_root",
            instruction="PPT生成任务流根节点，串联P0-P10全流程",
            # P3 固定保留在任务列表；无附件时运行时 skip 并标记 completed。
            sub_plans=[
                self._p0,
                self._p1,
                self._p3,
                self._p2,
                self._p3_5,
                *self._tail_plans,
            ],
        )

    async def _run_subplan(
        self,
        subplan: PlanNode,
        inputs: dict[str, Any],
        results: list[dict[str, Any]],
    ) -> None:
        result = await self.execute_subplan(subplan, inputs)
        _merge_subplan_result(inputs, result)
        results.append(
            {
                "node": subplan.plan_name,
                "status": _result_status(result),
                "result": result,
            }
        )

    async def _skip_p3_subplan(
        self,
        inputs: dict[str, Any],
        results: list[dict[str, Any]],
    ) -> None:
        result = await self.skip_subplan(
            self._p3,
            inputs,
            message=_P3_SKIP_MESSAGE,
            extra=_P3_SKIP_FIELDS,
        )
        _merge_subplan_result(inputs, result)
        results.append(
            {
                "node": self._p3.plan_name,
                "status": _result_status(result),
                "result": result,
            }
        )

    async def _run_p3_and_p2(self, inputs: dict[str, Any], results: list[dict[str, Any]]) -> None:
        if inputs.get("has_documents"):
            await self._run_subplan(self._p3, inputs, results)
        else:
            await self._skip_p3_subplan(inputs, results)
        await self._run_subplan(self._p2, inputs, results)

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """非流式执行 PPT 生成全流程，并把共享上下文透传给所有子节点。"""
        results: list[dict[str, Any]] = []

        await self._run_subplan(self._p0, inputs, results)
        await self._run_subplan(self._p1, inputs, results)
        await self._run_p3_and_p2(inputs, results)

        # P3.5 模板叙事上下文预处理（条件执行，节点内部判断 style_mode）
        await self._run_subplan(self._p3_5, inputs, results)

        for subplan in self._tail_plans:
            await self._run_subplan(subplan, inputs, results)

        return {
            "node": self.plan_name,
            "status": "ok",
            "message": "PPT生成任务流执行完成",
            "result": inputs,
            "steps": results,
        }

    async def _run_subplan_stream(
        self,
        subplan: PlanNode,
        inputs: dict[str, Any],
        results: list[dict[str, Any]],
        *,
        index: int,
        total_steps: int,
    ) -> AsyncIterator[dict[str, Any]]:
        yield {
            "node": self.plan_name,
            "status": "progress",
            "message": f"开始执行 {subplan.plan_name}（{index}/{total_steps}）",
            "current_node": subplan.plan_name,
            "step": index,
            "total_steps": total_steps,
        }

        last_chunk: Any = None
        async for chunk in self.execute_subplan_stream(subplan, inputs):
            last_chunk = chunk
            yield chunk

        _merge_subplan_result(inputs, last_chunk)
        results.append(
            {
                "node": subplan.plan_name,
                "status": _result_status(last_chunk),
                "result": last_chunk,
            }
        )
        yield {
            "node": self.plan_name,
            "status": "progress",
            "message": f"完成执行 {subplan.plan_name}（{index}/{total_steps}）",
            "current_node": subplan.plan_name,
            "step": index,
            "total_steps": total_steps,
        }

    async def _skip_p3_subplan_stream(
        self,
        inputs: dict[str, Any],
        results: list[dict[str, Any]],
        *,
        index: int,
        total_steps: int,
    ) -> AsyncIterator[dict[str, Any]]:
        yield {
            "node": self.plan_name,
            "status": "progress",
            "message": f"开始执行 {self._p3.plan_name}（{index}/{total_steps}）",
            "current_node": self._p3.plan_name,
            "step": index,
            "total_steps": total_steps,
        }

        last_chunk: Any = None
        async for chunk in self.skip_subplan_stream(
            self._p3,
            inputs,
            message=_P3_SKIP_MESSAGE,
            extra=_P3_SKIP_FIELDS,
        ):
            last_chunk = chunk
            yield chunk

        _merge_subplan_result(inputs, last_chunk)
        results.append(
            {
                "node": self._p3.plan_name,
                "status": _result_status(last_chunk),
                "result": last_chunk,
            }
        )
        yield {
            "node": self.plan_name,
            "status": "progress",
            "message": f"完成执行 {self._p3.plan_name}（{index}/{total_steps}）",
            "current_node": self._p3.plan_name,
            "step": index,
            "total_steps": total_steps,
        }

    async def _run_p3_and_p2_stream(
        self,
        inputs: dict[str, Any],
        results: list[dict[str, Any]],
        *,
        total_steps: int,
    ) -> AsyncIterator[dict[str, Any]]:
        if inputs.get("has_documents"):
            async for chunk in self._run_subplan_stream(
                self._p3, inputs, results, index=3, total_steps=total_steps
            ):
                yield chunk
        else:
            async for chunk in self._skip_p3_subplan_stream(
                inputs, results, index=3, total_steps=total_steps
            ):
                yield chunk

        async for chunk in self._run_subplan_stream(
            self._p2, inputs, results, index=4, total_steps=total_steps
        ):
            yield chunk

    async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """流式执行 PPT 生成全流程，逐个透传子节点进度与结果。"""
        results: list[dict[str, Any]] = []
        total_steps = len(self.sub_plans)

        yield {
            "node": self.plan_name,
            "status": "progress",
            "message": "PPT生成任务流开始执行",
        }

        for index, subplan in enumerate([self._p0, self._p1], start=1):
            async for chunk in self._run_subplan_stream(
                subplan,
                inputs,
                results,
                index=index,
                total_steps=total_steps,
            ):
                yield chunk

        async for chunk in self._run_p3_and_p2_stream(inputs, results, total_steps=total_steps):
            yield chunk

        # P3.5 模板叙事上下文预处理（条件执行，节点内部判断 style_mode）
        async for chunk in self._run_subplan_stream(
            self._p3_5, inputs, results, index=5, total_steps=total_steps,
        ):
            yield chunk

        for index, subplan in enumerate(self._tail_plans, start=6):
            async for chunk in self._run_subplan_stream(
                subplan,
                inputs,
                results,
                index=index,
                total_steps=total_steps,
            ):
                yield chunk

        yield {
            "node": self.plan_name,
            "status": "ok",
            "message": "PPT生成任务流执行完成",
            "result": inputs,
            "steps": results,
        }


root = PPTGenRootNode()
