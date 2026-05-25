import os
import requests
from dotenv import load_dotenv

# 加载密钥
load_dotenv()
API_KEY = os.getenv("DEEPSEEK_API_KEY")
BASE_URL = os.getenv("DEEPSEEK_BASE_URL")


# ==============================
# 这就是：Minimal LLM Agent 内核
# 最干净、最简单、面试能讲
# ==============================
class MinimalLLMAgent:
    def __init__(self, api_key, base_url, model="deepseek-chat"):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.messages = []  # 对话历史

    def chat(self, user_input):
        # 把用户输入加入历史
        self.messages.append({"role": "user", "content": user_input})

        # 调用大模型
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "messages": self.messages,
            "temperature": 0.1
        }

        response = requests.post(f"{BASE_URL}/chat/completions", headers=headers, json=payload)
        res = response.json()
        reply = res["choices"][0]["message"]["content"]

        # 把回复加入历史
        self.messages.append({"role": "assistant", "content": reply})
        return reply


# ==============================
# 运行第一步：测试 Agent
# ==============================
if __name__ == "__main__":
    print("===== 第一步：Minimal LLM Agent 启动成功 =====")

    # 初始化智能体
    agent = MinimalLLMAgent(API_KEY, BASE_URL)

    # 测试对话
    res = agent.chat("你好，你是商务智能助手，请介绍自己")
    print("\nAI 回复：")
    print(res)