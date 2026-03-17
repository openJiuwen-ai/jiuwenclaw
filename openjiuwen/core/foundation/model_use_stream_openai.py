import asyncio

from openjiuwen.core.foundation.llm.output_parsers.json_output_parser import JsonOutputParser
from openjiuwen.core.foundation.llm import ModelClientConfig, ModelConfig, Model, SystemMessage, UserMessage, \
    ModelRequestConfig

model_client_config = ModelClientConfig(
    client_id="jk0009",
    client_provider="OpenAI",
    api_key="jntqX_0W58WyYNiCBhWuaEswGOMZOzAfa8f-dnQ2svyqb6zQy5NLTgyuhwpZdS-Sn3zFlJvMYjEvKesDcnx1Sw",
    api_base="https://api.modelarts-maas.com/v2",
    verify_ssl=False
)

model_config = ModelRequestConfig(
    model="DeepSeek-V3-UOxkd3",
)

model1 = Model(
    model_config=model_config,
    model_client_config=model_client_config
)

messages1 = [
    SystemMessage(content="你是一个AI助手").model_dump(exclude_none=True),
    UserMessage(content="你好").model_dump(exclude_none=True)
]
#
#
# messages1 = [
#     SystemMessage(content="你是一个AI助手").model_dump(exclude_none=True),
#     UserMessage(content="帮我生成json格式：name：张三， age： 18").model_dump(exclude_none=True)
# ]


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
        print(res, end="", flush=True)
        print()  # 换行

    print()  # 换行


async def main():
    await async_astream()


if __name__ == "__main__":
    asyncio.run(main())
