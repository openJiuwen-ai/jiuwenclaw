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
    model="wan2.6-t2v",
)

model1 = Model(
    model_config=model_config,
    model_client_config=model_client_config
)

messages1 = [
    UserMessage(content="生成一个小白兔的视频")
]


async def async_astream():
    response = await model1.generate_video(messages=messages1)
    print(response)


async def main():
    await async_astream()


if __name__ == "__main__":
    asyncio.run(main())
