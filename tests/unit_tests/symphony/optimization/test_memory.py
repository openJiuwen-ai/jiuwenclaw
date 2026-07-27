from jiuwenswarm.symphony.optimization.memory.base import JsonlPromptMemory, NullPromptMemory
from jiuwenswarm.symphony.optimization.models import PromptRecord, TaskSpec


def _record(prompt, reward, objective, chars="", baseline_reward=None):
    return PromptRecord(
        prompt=prompt, reward=reward, objective=objective,
        task_characteristics=chars or objective,
        baseline_reward=baseline_reward,
    )


def test_jsonl_memory_persists_and_reloads(tmp_path):
    mem = JsonlPromptMemory(tmp_path)
    mem.add(_record("p1", 0.8, "summarize tickets into action items"))

    reloaded = JsonlPromptMemory(tmp_path)
    hits = reloaded.search_similar(TaskSpec(objective="summarize tickets into action items"))
    assert hits and hits[0].prompt == "p1"


def test_jsonl_memory_ranks_by_relevance_and_reward(tmp_path):
    mem = JsonlPromptMemory(tmp_path)
    mem.add(_record("translate", 0.9, "translate english to french"))
    mem.add(_record("summarize-weak", 0.3, "summarize a support ticket"))
    mem.add(_record("summarize-strong", 0.9, "summarize a support ticket"))

    hits = mem.search_similar(TaskSpec(objective="summarize a support ticket"), top_k=2)
    prompts = [h.prompt for h in hits]
    # relevant summarize records rank above the unrelated translate record
    assert "summarize-strong" in prompts
    assert prompts[0] == "summarize-strong"
    assert "translate" not in prompts


def test_null_memory_is_noop(tmp_path):
    mem = NullPromptMemory()
    mem.add(_record("p", 1.0, "x"))
    assert mem.search_similar(TaskSpec(objective="x")) == []
    assert mem.best_for_objective("x") is None
    assert mem.pending(0.0) == []
    assert mem.mark_applied("anything") is False


def test_best_for_objective_picks_highest_reward_exact_match(tmp_path):
    mem = JsonlPromptMemory(tmp_path)
    mem.add(_record("weak", 0.4, "summarize a support ticket"))
    mem.add(_record("strong", 0.9, "summarize a support ticket"))
    mem.add(_record("unrelated", 1.0, "translate english to french"))

    best = mem.best_for_objective("summarize a support ticket")
    assert best is not None
    assert best.prompt == "strong"


def test_pending_filters_by_threshold_and_applied_flag(tmp_path):
    mem = JsonlPromptMemory(tmp_path)
    first = _record("p1", 0.5, "summarize tickets", baseline_reward=None)  # gain 0.5
    second = _record("p2", 0.55, "summarize tickets", baseline_reward=0.5)  # gain 0.05
    mem.add(first)
    mem.add(second)

    # both clear a low threshold, ranked by gain descending
    pending = mem.pending(threshold=0.01)
    assert [r.record_id for r in pending] == [first.record_id, second.record_id]

    # a higher threshold excludes the marginal second record
    pending_strict = mem.pending(threshold=0.1)
    assert [r.record_id for r in pending_strict] == [first.record_id]


def test_mark_applied_removes_from_pending_and_persists_across_reload(tmp_path):
    mem = JsonlPromptMemory(tmp_path)
    record = _record("p1", 0.8, "summarize tickets")
    mem.add(record)
    assert len(mem.pending(0.0)) == 1

    assert mem.mark_applied(record.record_id) is True
    assert mem.pending(0.0) == []

    # unknown id is a no-op that reports failure
    assert mem.mark_applied("does-not-exist") is False

    reloaded = JsonlPromptMemory(tmp_path)
    assert reloaded.pending(0.0) == []
    hits = reloaded.search_similar(TaskSpec(objective="summarize tickets"))
    assert hits and hits[0].applied is True
