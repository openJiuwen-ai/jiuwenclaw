import asyncio


from openjiuwen.core.foundation.llm import ModelClientConfig, Model, UserMessage, ModelRequestConfig

model_client_config = ModelClientConfig(
    client_id="jk0009",
    client_provider="DashScope",
    api_key="sk-9e049f37b7ea42a287601fbc2054b566",
    api_base="https://dashscope.aliyuncs.com/api/v1",
    verify_ssl=False
)

model_config = ModelRequestConfig(
    model="qwen-image-max",
)

model1 = Model(
    model_config=model_config,
    model_client_config=model_client_config
)

messages1 = [
    UserMessage(content="小姑娘在花丛中的照片")
]


async def async_astream():
    response = await model1.generate_image(messages=messages1, model="qwen-image-max")
    print(response)


async def main():
    await async_astream()


if __name__ == "__main__":
    asyncio.run(main())
