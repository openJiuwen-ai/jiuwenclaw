import asyncio

from openjiuwen.core.foundation.llm import ModelClientConfig, ModelConfig, Model, SystemMessage, UserMessage, \
    ModelRequestConfig

model_client_config = ModelClientConfig(
    client_provider="ICBC",
    api_key="sk-9e049f37b7ea42a287601fbc2054b566",
    api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
    verify_ssl=False
)

model_config = ModelRequestConfig(
    model="qwen3-max",
    temperature=0.7,      # 控制输出的随机性
    top_p=0.9,            # nucleus采样参数
    max_tokens=2000,      # 最大生成token数
)

model1 = Model(
    model_config=model_config,
    model_client_config=model_client_config
)

messages1 = [
    SystemMessage(content="你是一个AI助手").model_dump(exclude_none=True),
    UserMessage(content="杭州天气").model_dump(exclude_none=True),
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


async def async_astream():
    print("=== 示例1: 使用配置的默认参数 ===")
    response = await model1.invoke(messages=messages1, tools=tools)
    print(response)
    print()



async def main():
    await async_astream()


if __name__ == "__main__":
    asyncio.run(main())
