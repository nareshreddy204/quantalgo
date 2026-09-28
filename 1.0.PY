from openai import OpenAI

client = OpenAI(
    api_key="hKVl_h7zFrfHHP59ddpYk",
    base_url="https://api.ashna.ai/v1/api",
)

response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[
        {"role": "user", "content": "Hello, can you help me?"}
    ],
    temperature=0.7,
    max_tokens=500,
)

print(response.choices[0].message.content)