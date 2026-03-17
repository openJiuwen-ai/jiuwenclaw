import asyncio

from openjiuwen.core.foundation.llm.output_parsers.json_output_parser import JsonOutputParser
from openjiuwen.core.foundation.llm import ModelClientConfig, ModelConfig, Model, SystemMessage, UserMessage, \
    ModelRequestConfig

model_client_config = ModelClientConfig(
    client_id="jk00091",
    client_provider="SiliconFlow",
    api_key="sk-kydadvndkobrybgdizatijrxmvzeuvycfoqlsbkofinpkhnd",
    api_base="https://api.siliconflow.cn/v1",
    verify_ssl=False
)

model_config = ModelRequestConfig(
    model="Pro/zai-org/GLM-4.7",
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
    response = await model1.invoke(messages=messages1, tools=tools, output_parser=j_parser)
    print(response)


async def main():
    await async_astream()


if __name__ == "__main__":
    asyncio.run(main())
