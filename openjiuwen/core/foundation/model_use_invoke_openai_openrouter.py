import asyncio

from openjiuwen.core.foundation.llm.output_parsers.json_output_parser import JsonOutputParser
from openjiuwen.core.foundation.llm import ModelClientConfig, ModelConfig, Model, SystemMessage, UserMessage

model_client_config = ModelClientConfig(
    client_id="jk0009",
    client_type="OpenAI",
    api_key="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",
    api_base="https://openrouter.ai/api/v1/",
    verify_ssl=False
)

model_config = ModelConfig(
    model="deepseek/deepseek-v3.2",
)

model1 = Model(
    model_config=model_config,
    model_client_config=model_client_config
)

# messages1 = [
#     SystemMessage(content="你是一个AI助手").model_dump(exclude_none=True),
#     UserMessage(content="杭州天气").model_dump(exclude_none=True)
# ]


messages1 = [
    SystemMessage(content="你是一个AI助手").model_dump(exclude_none=True),
    UserMessage(content="帮我生成json格式：name：张三， age： 18").model_dump(exclude_none=True)
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
    response = await model1.ainvoke(messages=messages1, output_parser=j_parser)
    print(response)


async def main():
    await async_astream()


if __name__ == "__main__":
    asyncio.run(main())
