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
    model="qwen3-tts-flash",
)

model1 = Model(
    model_config=model_config,
    model_client_config=model_client_config
)

messages1 = [
    UserMessage(content="那我来给大家推荐一款T恤，这款呢真的是超级好看，这个颜色呢很显气质，而且呢也是搭配的绝佳单品，大家可以闭眼入，真的是非常好看，对身材的包容性也很好，不管啥身材的宝宝呢，穿上去都是很好看的。推荐宝宝们下单哦。")
]


async def async_astream():
    response = await model1.generate_speech(messages=messages1, model="qwen3-tts-flash")
    print(response)


async def main():
    await async_astream()


if __name__ == "__main__":
    asyncio.run(main())
