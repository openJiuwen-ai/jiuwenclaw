from jiuwenswarm.symphony.optimization.environment.callable_env import CallableEnvironment
from jiuwenswarm.symphony.optimization.models import PromptCandidate, TaskCase, TaskSpec


async def test_callable_environment_runs_all_cases():
    env = CallableEnvironment(lambda sp, ci: f"{sp}:{ci}", attribute_tokens=False)
    task = TaskSpec(objective="o", cases=[TaskCase(input="a"), TaskCase(input="b", hidden=True)])
    ex = await env.execute(PromptCandidate(prompt="P"), task)

    assert len(ex.case_results) == 2
    assert ex.combined_output() == "P:a"          # visible only
    assert ex.combined_output(hidden=True) == "P:b"
    assert ex.error is None


async def test_callable_environment_isolates_case_errors():
    def runner(sp, ci):
        if ci == "boom":
            raise ValueError("bad case")
        return "ok"

    env = CallableEnvironment(runner, attribute_tokens=False)
    task = TaskSpec(objective="o", cases=[TaskCase(input="fine"), TaskCase(input="boom")])
    ex = await env.execute(PromptCandidate(prompt="P"), task)

    outputs = {r.case_input: (r.output, r.error) for r in ex.case_results}
    assert outputs["fine"] == ("ok", None)
    assert outputs["boom"][0] == ""
    assert "bad case" in outputs["boom"][1]
    # not all cases failed -> execution-level error stays None
    assert ex.error is None


async def test_async_runner_supported():
    async def runner(sp, ci):
        return "async-ok"

    env = CallableEnvironment(runner, attribute_tokens=False)
    task = TaskSpec(objective="o", cases=[TaskCase(input="a")])
    ex = await env.execute(PromptCandidate(prompt="P"), task)
    assert ex.case_results[0].output == "async-ok"
