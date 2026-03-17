# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Triple Extractor Implementation

Uses LLM for triple extraction.
"""

import asyncio
from typing import Any, List

from json_repair import repair_json

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.common.document import TextChunk
from openjiuwen.core.retrieval.common.triple import Triple
from openjiuwen.core.retrieval.indexing.processor.extractor.base import Extractor


class TripleExtractor(Extractor):
    """Triple extractor implementation using LLM for OpenIE triple extraction"""

    def __init__(
        self,
        llm_client: Any,
        model_name: str,
        temperature: float = 0.0,
        max_concurrent: int = 50,
        **kwargs,
    ):
        """
        Initialize triple extractor

        Args:
            llm_client: LLM client instance
            model_name: Model name
            temperature: Temperature parameter
            max_concurrent: Maximum concurrency, defaults to 50
        """
        self.llm_client = llm_client
        self.model_name = model_name
        self.temperature = temperature
        self.limiter = asyncio.Semaphore(max_concurrent)

    async def extract(
        self,
        chunks: List[TextChunk],
        **kwargs,
    ) -> List[Triple]:
        """
        Extract triples

        Args:
            chunks: List of text chunks
            **kwargs: Additional parameters

        Returns:
            List of triples
        """

        async def _extract_chunk(chunk: TextChunk) -> tuple[List[Triple], bool]:
            """Process triple extraction for a single chunk

            Returns:
                Tuple of (triples, success): triples extracted and whether extraction succeeded
            """
            async with self.limiter:
                try:
                    # Build prompt
                    prompt = self._build_prompt(chunk.text, chunk.metadata.get("title", ""))
                    messages = [{"role": "user", "content": prompt}]

                    # Call LLM
                    completion = await self.llm_client.invoke(
                        messages=messages,
                        temperature=self.temperature,
                    )

                    # Parse result
                    triples, parse_success = self._parse_triples(completion.content, chunk.doc_id, chunk.id_)
                    return triples, parse_success

                except Exception as e:
                    logger.error(f"Failed to extract triples from chunk {chunk.id_}: {e}")
                    return [], False

        # Create parallel tasks using create_task
        tasks = [asyncio.create_task(_extract_chunk(chunk)) for chunk in chunks]

        # Wait for all tasks to complete and collect results
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Merge all results and check for failures
        all_triples = []
        total_chunks = len(chunks)
        failed_chunks = []

        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                # Task-level exception (e.g., from asyncio)
                chunk_id = chunks[idx].id_ if idx < len(chunks) else f"chunk_{idx}"
                logger.error(f"Task failed with exception for chunk {chunk_id}: {result}")
                failed_chunks.append(chunk_id)
            elif isinstance(result, tuple):
                # Result from _extract_chunk: (triples, success)
                triples, success = result
                if not success:
                    chunk_id = chunks[idx].id_ if idx < len(chunks) else f"chunk_{idx}"
                    failed_chunks.append(chunk_id)
                all_triples.extend(triples)
            else:
                # Unexpected result type
                chunk_id = chunks[idx].id_ if idx < len(chunks) else f"chunk_{idx}"
                logger.warning(f"Unexpected result type from extraction task for chunk {chunk_id}: {type(result)}")
                failed_chunks.append(chunk_id)

        # If any chunk extraction failed, raise exception immediately
        if failed_chunks:
            error_msg = (
                f"Triple extraction failed for {len(failed_chunks)}/{total_chunks} chunks. "
                f"Recent Failed chunks: {', '.join(failed_chunks[:5])}{'...' if len(failed_chunks) > 5 else ''}. "
                f"This may be due to rate limiting, API errors, or model issues."
            )
            raise build_error(StatusCode.RETRIEVAL_KB_TRIPLE_EXTRACTION_PROCESS_ERROR, error_msg=error_msg)

        return all_triples

    def _build_prompt(self, passage: str, title: str = "") -> str:
        """Build prompt for triple extraction"""
        prompt_template = """Extract entities and relationships from the following passage. 
Return the results in JSON format with a list of triples, where each triple is represented as [subject, predicate, object].

Passage:
{passage}

Title: {title}

Please extract all meaningful triples from the passage. Return only the JSON array, no additional text.
Format: [["subject1", "predicate1", "object1"], ["subject2", "predicate2", "object2"], ...]
"""
        return prompt_template.format(passage=passage, title=title or "Untitled")

    def _parse_triples(self, content: str, doc_id: str, chunk_id: str) -> tuple[List[Triple], bool]:
        """Parse triples returned by LLM

        Returns:
            Tuple of (triples, parse_success):
            - triples: List of extracted triples
            - parse_success: True if parsing succeeded (even if no triples found),
                            False if parsing failed (JSON decode error, etc.)
        """
        triples = []

        try:
            # Try to parse JSON
            # Remove possible markdown code block markers
            content = content.strip()
            if content.startswith("```"):
                # Remove code block markers
                lines = content.split("\n")
                content = "\n".join(lines[1:-1]) if len(lines) > 2 else content

            # Parse JSON
            try:
                triple_list = repair_json(content, return_objects=True)
            except Exception as e:
                logger.error("Failed to parse triples from content: %s. Content: %s", e, content[:200])
                return [], False

            # Convert to Triple objects
            # Note: If triple_list is an empty array [], this is valid (no triples found)
            # and parse_success should be True
            for triple_data in triple_list:
                if isinstance(triple_data, list) and len(triple_data) >= 3:
                    triple = Triple(
                        subject=str(triple_data[0]),
                        predicate=str(triple_data[1]),
                        object=str(triple_data[2]),
                        confidence=float(triple_data[3]) if len(triple_data) > 3 else None,
                        metadata={"doc_id": doc_id, "chunk_id": chunk_id},
                    )
                    triples.append(triple)

            # If we successfully parsed JSON (even if it's an empty array), return success=True
            return triples, True

        except Exception as e:
            # Any other exception during parsing is a failure
            logger.error("Failed to parse triples: %s", e)
            return [], False
