import openai

import base64
from openai import OpenAI

# 初始化客户端
client = OpenAI(api_key="sk-npxxpaouzfsastakoefksfmczszyltaohxcoxafhwzqdxnlw", base_url="https://api.siliconflow.cn/v1")
# client = OpenAI(api_key="sk-npxxpaouzfsastakoefksfmczszyltaohxcoxafhwzqdxnlw", base_url="https://api.siliconflow.cn/v1/video/submit")
# client = OpenAI(api_key="sk-9e049f37b7ea42a287601fbc2054b566", base_url="https://dashscope.aliyuncs.com/api/v1")

# 生成图像
response = client.images.generate(
    model="Qwen/Qwen-Image",
    prompt="一只可爱的猫咪在阳光明媚的窗台上打盹，写实风格",
    n=1,
    size="1024x1024",
    quality="high",
    response_format="b64_json"
)
# print(response)


# 音频生成
response_audio = client.audio.speech.create(
    model="FunAudioLLM/CosyVoice2-0.5B",  # 支持 fishaudio / GPT-SoVITS / CosyVoice2-0.5B 系列模型
    voice="FunAudioLLM/CosyVoice2-0.5B:alex",  # 系统预置音色
    # 用户输入信息
    input="你能用高兴的情感说吗？<|endofprompt|>今天真是太开心了，马上要放假了！I'm so happy, Spring Festival is coming!",
    response_format="mp3"  # 支持 mp3, wav, pcm, opus 格式
)
print(type(response_audio.content))

print(response_audio)
# import datetime, pathlib
# out = pathlib.Path(f"tts_{datetime.datetime.now():%Y%m%d_%H%M%S}.mp3")
# out.write_bytes(response_audio.content)
# print("已保存 →", out.resolve())


#
# response_mp4 = client.videos.create(
#     model="Wan-AI/Wan2.2-I2V-A14B",
#     prompt="生成小猫小狗打架的视频"
# )
#
# print(response_mp4.status)


# import requests
#
# url = "https://api.siliconflow.cn/v1/video/submit"
#
# payload = {
#     "model": "Wan-AI/Wan2.2-T2V-A14B",
#     "prompt": "帮我生成小兔子追老虎的视频",
#     "image_size": "1280x720"
# }
# headers = {
#     "Authorization": "Bearer sk-npxxpaouzfsastakoefksfmczszyltaohxcoxafhwzqdxnlw",
#     "Content-Type": "application/json"
# }
#
# response = requests.post(url, json=payload, headers=headers)
#
# print(response.text)

# import requests
#
# url = "https://api.siliconflow.cn/v1/video/status"
#
# payload = { "requestId": "w83bkek4x9v0" }
# headers = {
#     "Authorization": "Bearer sk-npxxpaouzfsastakoefksfmczszyltaohxcoxafhwzqdxnlw",
#     "Content-Type": "application/json"
# }
#
# response = requests.post(url, json=payload, headers=headers)
#
# print(response.text)