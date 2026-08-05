from jiuwenswarm.agents.harness.search.llm_client.openai_client import OpenAIClient
from jiuwenswarm.agents.harness.search.llm_client.qwen_client import QwenClient


def create_llm_client(**kwargs):
    model_name = kwargs.get("model", "")
    if "qwen" in model_name.lower():
        return QwenClient(**kwargs)
    # if "gemini" in model_name.lower():
    #     return GeminiClient(**kwargs)
    else:
        return OpenAIClient(**kwargs)