import asyncio

from openjiuwen.core.foundation.llm.output_parsers.json_output_parser import JsonOutputParser
from openjiuwen.core.foundation.llm import ModelClientConfig, ModelConfig, Model, SystemMessage, UserMessage, \
    ModelRequestConfig

model_client_config = ModelClientConfig(
    client_id="jk0009",
    client_provider="O",
    api_key="sk-npxxpaouzfsastakoefksfmczszyltaohxcoxafhwzqdxnlw",
    api_base="https://api.siliconflow.cn/v1/chat/completions",
    verify_ssl=False
)

model_config = ModelRequestConfig(
    model="Qwen/Qwen3-32B"
)

model1 = Model(
    model_config=model_config,
    model_client_config=model_client_config
)

messages1 = [
    SystemMessage(content="你是一个AI助手").model_dump(exclude_none=True),
    UserMessage(content="你好").model_dump(exclude_none=True)
]


tools = [{
    'type': 'function',
    'function': {
        'name': 'get_weather',
        'description': '天气预报',
        'parameters': {
            'type': 'object',
            'properties': {
                'location': {
                    'type': 'string',
                    'description': '地名',
                }
            },
            'required': ['location']
        }
    }
}]

j_parser = JsonOutputParser()

async def async_astream():
    res_tt = None
    async for res in model1.stream(messages="你好", tools=tools):
        print(res.content, end="", flush=True)
        print()  # 换行

    print()  # 换行


async def main():
    await async_astream()


if __name__ == "__main__":
    asyncio.run(main())
