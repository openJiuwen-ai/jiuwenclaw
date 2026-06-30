"""LLM-backed schema extraction."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from jiuwenswarm.symphony.llm import (
    LLMConfig,
    create_llm_client,
    llm_usage_context,
)
from jiuwenswarm.symphony.fingerprint.models import (
    ArtifactSpec,
    ExtractedSkillSchema,
    ParameterSpec,
    RawSkillManifest,
)

_LOW_REASONING_REQUEST_OVERRIDES = {
    "extra_body": {"thinking": {"type": "disabled"}},
}


class LLMSchemaExtractor:
    """Extract Skill IO schema using LLM."""

    def __init__(
        self,
        config: LLMConfig,
        *,
        body_limit: int | None = None,
        batch_size: int = 1,
    ) -> None:
        self.config = config
        self.client = create_llm_client(config)
        self.batch_size = max(1, int(batch_size))
        self.use_batch = self.batch_size > 1
        self.body_limit = _normalize_body_limit(body_limit)

    async def extract(self, manifest: RawSkillManifest) -> ExtractedSkillSchema:
        with llm_usage_context("fingerprint_extraction", "schema_extraction"):
            content = await self.client.complete_json_async(
                system_prompt=_SCHEMA_EXTRACTION_PROMPT,
                user_content=json.dumps(
                    _build_llm_context(manifest, body_limit=self.body_limit),
                    ensure_ascii=False,
                    indent=2,
                ),
                error_context="LLM schema extraction",
                request_overrides=_LOW_REASONING_REQUEST_OVERRIDES,
            )
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "LLM response is not valid JSON. "
                f"content_prefix={content[:1000]!r}"
            ) from exc
        return schema_from_llm_payload(payload)

    async def extract_many(
        self,
        manifests: List[RawSkillManifest],
    ) -> List[ExtractedSkillSchema]:
        if self.batch_size > 1:
            return await self._extract_many_prompt_batch(manifests)

        with llm_usage_context("fingerprint_extraction", "schema_extraction_batch"):
            contents = await self.client.complete_json_many_async(
                [
                    {
                        "system_prompt": _SCHEMA_EXTRACTION_PROMPT,
                        "user_content": json.dumps(
                            _build_llm_context(manifest, body_limit=self.body_limit),
                            ensure_ascii=False,
                            indent=2,
                        ),
                    }
                    for manifest in manifests
                ],
                error_context="LLM schema extraction batch",
                request_overrides=_LOW_REASONING_REQUEST_OVERRIDES,
            )
        schemas: List[ExtractedSkillSchema] = []
        for index, content in enumerate(contents, start=1):
            try:
                payload = json.loads(content)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "LLM batch response item is not valid JSON. "
                    f"item={index} content_prefix={content[:1000]!r}"
                ) from exc
            schemas.append(schema_from_llm_payload(payload))
        return schemas

    async def _extract_many_prompt_batch(
        self,
        manifests: List[RawSkillManifest],
    ) -> List[ExtractedSkillSchema]:
        if not manifests:
            return []
        if len(manifests) == 1:
            return [await self.extract(manifests[0])]

        try:
            return await self._extract_many_prompt_batch_once(manifests)
        except RuntimeError:
            midpoint = len(manifests) // 2
            if midpoint <= 0:
                return [await self.extract(manifests[0])]
            return (
                await self._extract_many_prompt_batch(manifests[:midpoint])
                + await self._extract_many_prompt_batch(manifests[midpoint:])
            )

    async def _extract_many_prompt_batch_once(
        self,
        manifests: List[RawSkillManifest],
    ) -> List[ExtractedSkillSchema]:
        with llm_usage_context(
            "fingerprint_extraction",
            "schema_extraction_prompt_batch",
        ):
            content = await self.client.complete_json_async(
                system_prompt=_SCHEMA_EXTRACTION_BATCH_PROMPT,
                user_content=json.dumps(
                    {
                        "skills": [
                            _build_llm_context(
                                manifest,
                                body_limit=self.body_limit,
                            )
                            for manifest in manifests
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                error_context="LLM schema extraction prompt batch",
                request_overrides=_LOW_REASONING_REQUEST_OVERRIDES,
            )
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "LLM prompt batch response is not valid JSON. "
                f"content_prefix={content[:1000]!r}"
            ) from exc

        schemas_payload = payload.get("schemas")
        if not isinstance(schemas_payload, list):
            raise RuntimeError("LLM prompt batch response must contain schemas array.")

        expected_refs = [manifest.folder.relative_path for manifest in manifests]
        by_ref: Dict[str, Dict[str, Any]] = {}
        for item in schemas_payload:
            if not isinstance(item, dict):
                raise RuntimeError("LLM prompt batch schema items must be objects.")
            skill_ref = str(item.get("skill_ref") or "")
            if skill_ref in by_ref:
                raise RuntimeError(
                    f"LLM prompt batch returned duplicate skill_ref: {skill_ref!r}."
                )
            by_ref[skill_ref] = item

        missing_refs = [
            skill_ref for skill_ref in expected_refs if skill_ref not in by_ref
        ]
        extra_refs = sorted(set(by_ref) - set(expected_refs))
        if missing_refs or extra_refs:
            raise RuntimeError(
                "LLM prompt batch response skill_ref mismatch. "
                f"missing={missing_refs!r} extra={extra_refs!r}"
            )

        return [schema_from_llm_payload(by_ref[skill_ref]) for skill_ref in expected_refs]


def schema_from_llm_payload(payload: Dict[str, Any]) -> ExtractedSkillSchema:
    """Convert a raw LLM JSON payload into ExtractedSkillSchema."""

    warnings = [str(item) for item in payload.get("warnings", [])]
    raw_output_notes = payload.get("raw_output_notes", [])
    if isinstance(raw_output_notes, str):
        raw_output_notes = [raw_output_notes]
    warnings.extend(str(item) for item in raw_output_notes if str(item).strip())

    return ExtractedSkillSchema(
        description=str(payload.get("description") or ""),
        inputs=[_parameter_from_payload(item) for item in payload.get("inputs", [])],
        outputs=[_artifact_from_payload(item) for item in payload.get("outputs", [])],
        confidence=payload.get("confidence"),
        warnings=warnings,
    )


def _parameter_from_payload(payload: Dict[str, Any]) -> ParameterSpec:
    return ParameterSpec(
        name=str(payload.get("name") or "input"),
        type=_combined_type_from_payload(payload, "text"),
        required=bool(payload.get("required", True)),
        description=str(payload.get("description") or ""),
        default=payload.get("default"),
    )


def _artifact_from_payload(payload: Dict[str, Any]) -> ArtifactSpec:
    return ArtifactSpec(
        name=str(payload.get("name") or "result"),
        type=_combined_type_from_payload(payload, "unknown"),
        description=str(payload.get("description") or ""),
    )


def _combined_type_from_payload(payload: Dict[str, Any], default: str) -> str:
    return str(payload.get("format") or payload.get("type") or default)


def _build_llm_context(
    manifest: RawSkillManifest,
    *,
    body_limit: int | None = None,
) -> Dict[str, Any]:
    body_limit = _normalize_body_limit(body_limit)
    body = manifest.body if body_limit is None else manifest.body[:body_limit]
    return {
        "source": {
            "relative_path": manifest.folder.relative_path,
            "entry": "SKILL.md",
        },
        "frontmatter": manifest.frontmatter,
        "body": body,
        "body_truncated": body_limit is not None and len(manifest.body) > body_limit,
    }


def _normalize_body_limit(body_limit: int | None) -> int | None:
    if body_limit is None:
        return None
    parsed = int(body_limit)
    return parsed if parsed > 0 else None

_SCHEMA_EXTRACTION_PROMPT = """You extract structured Skill IO fingerprints from SKILL.md files.

Return only a valid JSON object. Do not include markdown fences, explanations,
analysis, or reasoning text.

Required JSON object fields:
- description: concise string
- inputs: array of {name, type, required, description}
- outputs: array of {name, type, description}
- confidence: number between 0 and 1
- warnings: array of strings
Optional JSON object fields:
- raw_output_notes: array of strings describing raw API or script return fields
  that helped your reasoning but should not be recorded as output artifacts.

Read the entire SKILL.md content provided in this request, not only
input/output tables. Sections such as
"when to use", "output example", "return format", "notes", "summary", and final
instructions may enrich or override formal API field tables.
If body_truncated is true and important sections appear missing, add a warning
instead of inventing missing inputs or outputs.

Extract only capabilities, inputs, and outputs supported by the provided
SKILL.md/frontmatter. Do not infer tools, formats, or deliverables that are not
stated or strongly implied by the document.

Outputs must represent user-facing/downstream deliverables: artifacts the Skill
promises to hand to the user or to another Skill. Do not emit raw API/control fields as outputs.
This includes errorCode, errorMsg, status, logs, debug fields, or internal JSON
containers such as raw result, imageResult, or textResult, unless the document
says that raw structure is the actual deliverable.

If a raw API response contains a useful deliverable inside a nested field,
extract the deliverable as a semantic output instead of the raw container. For
example, textResult[].translateText may become translated_text with type text.
If the document says to send, show, return, or provide something in markdown,
markdown delivery instructions must be represented as markdown outputs.

Use name for the semantic role and type for the artifact kind or concrete
format that is passed between Skills. For media resources, prefer the media
artifact type even when the runtime value is a URL, local path, file reference,
base64 string, or bytes string. For example, imageUrl, translated_image_url,
and image base64 should use type=image. audio_url, audioUrl, audio_file_path,
and audio base64 should use type=audio. video_url, videoUrl, video_path, and
mp4_url should use type=video. Use type=url for ordinary links, webpages,
download links, or non-media remote references.

Prefer these type values:
text, markdown, json, csv, table, yaml, xml, pdf, html, docx, pptx, xlsx,
image, png, jpg, svg, webp, gif, audio, video, file, path, url, code,
archive, unknown.

For example, a PDF paper should use name=paper and type=pdf. A markdown
summary should use name=summary and type=markdown. A generated image URL should
use name=image or translated_image_url and type=image, with URL details in the
description. Generated audio/video URLs should similarly use type=audio or
type=video, not type=url.

Use input and output names as canonical semantic vocab terms for graph linking. Prefer
short noun roles such as query, topic, url, paper, summary, report, table,
image, code, file, path, or result when they fit. Put details in description
instead of making highly specific parameter names.

Inputs are runtime caller-provided values. Include content inputs and explicit
control/configuration inputs such as target_language, output_format, limit, or
command when the caller must provide them for normal execution. Do not turn
environment setup, API keys, permissions, installed tools, caches, or persistent
local configuration into inputs; mention them in warnings when relevant.

If an input is a user's natural-language task, request, instruction, topic, or
free-form text to be interpreted by the Skill, name it text, query, or topic
instead of command. Reserve command for true control commands such as CLI
subcommands, action enums, command-line flags, execution switches, or a closed
set of operation names.

Set required=true only when the caller must provide the value for normal
execution. Use required=false for optional preferences, limits, defaults, or
output format choices that the Skill can infer or default.

Do not emit duplicate inputs for the same caller-provided value. Omit logging,
analytics, telemetry, statistics, tracing, debug evidence, or original-copy
fields unless the Skill truly consumes that value as a separate runtime input.

If unsure, use unknown and add a warning.
"""

_SCHEMA_EXTRACTION_BATCH_PROMPT = f"""{_SCHEMA_EXTRACTION_PROMPT}

You will receive a JSON object with a skills array. Each item is one independent
Skill context and includes source.relative_path.

Return only a valid JSON object with this shape:
{{
  "schemas": [
    {{
      "skill_ref": "the exact source.relative_path from the input skill",
      "description": "concise string",
      "inputs": [],
      "outputs": [],
      "confidence": 0.0,
      "warnings": []
    }}
  ]
}}

Return exactly one schema object for each input skill. Do not merge Skills, copy
inputs or outputs across Skills, or add schemas for Skills not present in the
request. Preserve skill_ref exactly so the caller can match results.
"""
