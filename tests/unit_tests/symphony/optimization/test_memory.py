from jiuwenswarm.symphony.optimization.memory.base import JsonlPromptMemory, NullPromptMemory
from jiuwenswarm.symphony.optimization.models import PromptRecord, TaskSpec


def _record(prompt, reward, objective, chars=""):
    return PromptRecord(
        prompt=prompt, reward=reward, objective=objective,
        task_characteristics=chars or objective,
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
