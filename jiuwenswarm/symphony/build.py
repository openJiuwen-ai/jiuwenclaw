"""Symphony Score build APIs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from jiuwenswarm.symphony.config import (
    SymphonyBuildConfig,
    SymphonyConfig,
    default_symphony_config,
)
from jiuwenswarm.symphony.fingerprint import (
    FingerprintExtractor,
    LLMConfig,
    LLMIONameResolver,
    LLMSchemaExtractor,
    NormalizationConfig,
    SkillFolderScanner,
    SkillManifestParser,
    SkillFingerprintNormalizer,
    SkillSchemaExtractor,
    write_extraction_result,
)
from jiuwenswarm.symphony.graph.matcher import (
    OntologyMatcher,
    OpenAICompatibleOntologyMatcher,
)
from jiuwenswarm.symphony.graph.pipeline import GraphBuilder
from jiuwenswarm.symphony.graph.writer import write_graph_build_result
from jiuwenswarm.symphony.llm import reset_llm_token_usage
from jiuwenswarm.symphony.score_state import (
    SCORE_STATE_FILENAME,
    ScoreStateBuilder,
    load_score_state,
    write_score_state,
)


@dataclass(frozen=True)
class ScoreStatus:
    success: bool
    score_dir: str
    exists: bool
    stale: bool
    skill_count: int
    changed_count: int
    added_count: int
    removed_count: int
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "score_dir": self.score_dir,
            "exists": self.exists,
            "stale": self.stale,
            "skill_count": self.skill_count,
            "changed_count": self.changed_count,
            "added_count": self.added_count,
            "removed_count": self.removed_count,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ScoreBuildResult:
    success: bool
    score_dir: str
    skill_count: int
    reused_count: int
    extracted_count: int
    removed_count: int
    edge_count: int
    diagnostics_count: int
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "score_dir": self.score_dir,
            "skill_count": self.skill_count,
            "reused_count": self.reused_count,
            "extracted_count": self.extracted_count,
            "removed_count": self.removed_count,
            "edge_count": self.edge_count,
            "diagnostics_count": self.diagnostics_count,
            "detail": self.detail,
        }


class ScoreBuildRuntimeFactory:
    """Create default runtime adapters for a Symphony Score build."""

    @staticmethod
    def schema_extractor(
        llm_config: LLMConfig | None,
        extraction_config: Any,
    ) -> SkillSchemaExtractor:
        if llm_config is None:
            raise ValueError("llm_config is required when schema_extractor is not provided")
        return LLMSchemaExtractor(
            llm_config,
            body_limit=extraction_config.body_limit,
            batch_size=extraction_config.batch_size,
        )

    @staticmethod
    def io_name_resolver(
        llm_config: LLMConfig | None,
        normalization_config: Any,
    ) -> Any:
        if llm_config is None:
            raise ValueError("llm_config is required when io_name_resolver is not provided")
        return LLMIONameResolver(
            llm_config,
            batch_size=normalization_config.batch_size,
        )

    @staticmethod
    def matcher(
        llm_config: LLMConfig | None,
        *,
        build_config: SymphonyBuildConfig,
        build_log: Callable[..., None] | None = None,
    ) -> OntologyMatcher:
        if llm_config is None:
            raise ValueError("llm_config is required when matcher is not provided")
        return OpenAICompatibleOntologyMatcher(
            llm_config,
            batch_size=build_config.batch_size,
            max_workers=build_config.workers,
            require_consensus=build_config.require_consensus,
            thresholds={"can_feed": build_config.min_edge_confidence},
            progress=(
                lambda event, current, total, details: _record_build_log(
                    build_log,
                    "graph.resolve.progress",
                    event=event,
                    current=current,
                    total=total,
                    details=details,
                )
            ),
        )


class SymphonyScoreBuilder:
    """Build and refresh the offline Symphony Score."""

    def __init__(
        self,
        *,
        scanner: SkillFolderScanner | None = None,
        parser: SkillManifestParser | None = None,
        runtime_factory: ScoreBuildRuntimeFactory | None = None,
        state_builder: ScoreStateBuilder | None = None,
    ) -> None:
        self.scanner = scanner or SkillFolderScanner()
        self.parser = parser or SkillManifestParser()
        self.runtime_factory = runtime_factory or ScoreBuildRuntimeFactory()
        self.state_builder = state_builder or ScoreStateBuilder()

    def status(
        self,
        skills_root: str | Path,
        score_dir: str | Path,
        *,
        symphony_config: SymphonyConfig | None = None,
    ) -> ScoreStatus:
        runtime_config = symphony_config or default_symphony_config()
        output_dir = Path(score_dir).resolve()
        folders = self.scanner.scan(
            skills_root,
            max_depth=runtime_config.fingerprint.scan.max_depth,
        )
        current_hashes = self.state_builder.folder_hashes(folders)
        state = load_score_state(output_dir)
        active_entries = state.active_entries()
        exists = (output_dir / "score_manifest.json").is_file()
        added = [path for path in current_hashes if path not in active_entries]
        changed = [
            path
            for path, digest in current_hashes.items()
            if path in active_entries and active_entries[path].skill_md_sha256 != digest
        ]
        removed = [path for path in active_entries if path not in current_hashes]
        stale = (not exists) or bool(added or changed or removed)
        detail = "score is fresh"
        if not exists:
            detail = "Symphony Score is missing"
        elif stale:
            detail = "Symphony Score is stale"

        return ScoreStatus(
            success=True,
            score_dir=str(output_dir),
            exists=exists,
            stale=stale,
            skill_count=len(folders),
            changed_count=len(changed),
            added_count=len(added),
            removed_count=len(removed),
            detail=detail,
        )

    async def build(
        self,
        skills_root: str | Path,
        score_dir: str | Path,
        llm_config: LLMConfig | None = None,
        *,
        force: bool = False,
        schema_extractor: SkillSchemaExtractor | None = None,
        matcher: OntologyMatcher | None = None,
        io_name_resolver: Any | None = None,
        build_log: Callable[..., None] | None = None,
        symphony_config: SymphonyConfig | None = None,
    ) -> ScoreBuildResult:
        runtime_config = symphony_config or default_symphony_config()
        output_dir = Path(score_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        reset_llm_token_usage()

        normalization_runtime_config = runtime_config.fingerprint.normalization
        normalization_config = NormalizationConfig(
            max_vocab_size=normalization_runtime_config.max_vocab_size,
            possible_duplicate_name_similarity_threshold=(
                normalization_runtime_config.duplicate_name_similarity_threshold
            ),
        )
        normalizer = SkillFingerprintNormalizer(
            config=normalization_config,
            io_name_resolver=io_name_resolver
            or self.runtime_factory.io_name_resolver(
                llm_config,
                runtime_config.fingerprint.normalization,
            ),
        )
        schema_extractor = schema_extractor or self.runtime_factory.schema_extractor(
            llm_config,
            runtime_config.fingerprint.extraction,
        )

        fingerprint_extractor = FingerprintExtractor(
            schema_extractor=schema_extractor,
            scanner=self.scanner,
            parser=self.parser,
            normalizer=normalizer,
            progress=_fingerprint_progress_adapter(build_log),
            event_log=build_log,
            max_workers=runtime_config.fingerprint.extraction.workers,
            normalization_workers=runtime_config.fingerprint.normalization.workers,
            normalization_batch_size=runtime_config.fingerprint.normalization.batch_size,
        )
        _record_build_log(build_log, "scan.start", skills_root=str(skills_root))
        extraction_result = await fingerprint_extractor.extract_from_root(
            skills_root,
            output_dir=output_dir,
            max_depth=runtime_config.fingerprint.scan.max_depth,
            force=force,
        )
        _record_build_log(
            build_log,
            "artifact.fingerprints.write.start",
            fingerprint_count=len(extraction_result.fingerprints),
            diagnostics_count=len(extraction_result.diagnostics),
        )
        write_extraction_result(extraction_result, output_dir)
        _record_build_log(build_log, "artifact.fingerprints.write.done")

        _record_build_log(
            build_log,
            "graph.build.start",
            fingerprint_count=len(extraction_result.fingerprints),
            workers=runtime_config.build.workers,
        )
        matcher = matcher or self.runtime_factory.matcher(
            llm_config,
            build_config=runtime_config.build,
            build_log=build_log,
        )
        graph_builder = GraphBuilder(matcher=matcher)
        graph_result = await graph_builder.build(
            extraction_result.fingerprints,
            progress=build_log,
        )
        _record_build_log(
            build_log,
            "graph.build.done",
            candidate_count=len(graph_result.candidates),
            match_count=len(graph_result.llm_matches),
            edge_count=len(graph_result.graph.edges),
            diagnostics_count=len(graph_result.diagnostics),
        )
        _record_build_log(build_log, "artifact.graph.write.start")
        write_graph_build_result(graph_result, output_dir)
        _write_io_vocab(extraction_result.io_name_vocab, output_dir)
        _record_build_log(build_log, "artifact.graph.write.done")

        new_state = self.state_builder.next_state(
            folders=extraction_result.folders,
            current_hashes=extraction_result.current_hashes,
            fingerprints_by_path=extraction_result.fingerprints_by_path,
            old_state=load_score_state(output_dir),
            removed_paths=extraction_result.removed_paths,
        )
        _record_build_log(
            build_log,
            "state.write.start",
            path=str(output_dir / SCORE_STATE_FILENAME),
        )
        write_score_state(new_state, output_dir)
        _record_build_log(build_log, "state.write.done")

        return ScoreBuildResult(
            success=True,
            score_dir=str(output_dir),
            skill_count=len(extraction_result.folders),
            reused_count=extraction_result.reused_count,
            extracted_count=extraction_result.extracted_count,
            removed_count=len(extraction_result.removed_paths),
            edge_count=len(graph_result.graph.edges),
            diagnostics_count=(
                len(extraction_result.diagnostics) + len(graph_result.diagnostics)
            ),
        )


def score_status(
    skills_root: str | Path,
    score_dir: str | Path,
    *,
    scanner: SkillFolderScanner | None = None,
    symphony_config: SymphonyConfig | None = None,
) -> ScoreStatus:
    """Report whether a Score exists and differs from the Skill folders."""

    return SymphonyScoreBuilder(scanner=scanner).status(
        skills_root,
        score_dir,
        symphony_config=symphony_config,
    )


async def build_score(
    skills_root: str | Path,
    score_dir: str | Path,
    llm_config: LLMConfig | None = None,
    *,
    workers: int = 1,
    force: bool = False,
    schema_extractor: SkillSchemaExtractor | None = None,
    matcher: OntologyMatcher | None = None,
    io_name_resolver: Any | None = None,
    scanner: SkillFolderScanner | None = None,
    parser: SkillManifestParser | None = None,
    build_log: Callable[..., None] | None = None,
    symphony_config: SymphonyConfig | None = None,
    runtime_factory: ScoreBuildRuntimeFactory | None = None,
) -> ScoreBuildResult:
    """Build or refresh the offline Symphony Score."""

    del workers
    return await SymphonyScoreBuilder(
        scanner=scanner,
        parser=parser,
        runtime_factory=runtime_factory,
    ).build(
        skills_root,
        score_dir,
        llm_config,
        force=force,
        schema_extractor=schema_extractor,
        matcher=matcher,
        io_name_resolver=io_name_resolver,
        build_log=build_log,
        symphony_config=symphony_config,
    )


def _fingerprint_progress_adapter(
    build_log: Callable[..., None] | None,
) -> Callable[[str, int, int, str], None] | None:
    if build_log is None:
        return None

    stage_map = {
        "parse": "fingerprint.parse.start",
        "extract": "fingerprint.extract.start",
        "normalize": "fingerprint.normalize.start",
    }

    def record(stage: str, current: int, total: int, item: str) -> None:
        build_stage = stage_map.get(stage)
        if build_stage is None:
            return
        build_log(build_stage, current=current, total=total, path=item)

    return record


def _record_build_log(build_log: Callable[..., None] | None, stage: str, **details: Any) -> None:
    if build_log is not None:
        build_log(stage, **details)


def _write_io_vocab(payload: dict[str, Any], score_dir: Path) -> None:
    (score_dir / "io_name_vocab.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
