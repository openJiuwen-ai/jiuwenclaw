from openai import OpenAI

base_url = "https://api.modelarts-maas.com/openai/v1"  # API地址
# api_key = "uJLPh06kUoTQY1hO8ro5akpUahU_FWxriKkfv_siTq6TtJnhw_pmEkyE_UODkOIoY6QLHpAvPP05U_dygoCaow"  # 把MAAS_API_KEY替换成已获取的API Key
api_key = "qESMUfOO8W3j_nEoYetX-9Kp6tyStClpqugHBTY-doouqOLo5lmPc6saGzqs-pWyWRl6bPCwvRpiSlyEl5vVzw"  # 把MAAS_API_KEY替换成已获取的API Key

client = OpenAI(api_key=api_key, base_url=base_url)

response = client.chat.completions.create(
    # model="deepseek-v3.2",  # model参数
    model="glm-5",  # model参数
    messages=[
        {"role": "system", "content": "You are a helpful assistant"},
        {"role": "user", "content": "你好"},
    ]
)

print(response.choices[0].message.content)