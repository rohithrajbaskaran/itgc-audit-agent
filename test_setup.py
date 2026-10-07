from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic

load_dotenv()
llm = ChatAnthropic(model="claude-sonnet-5-5")
print(llm.invoke("In one sentence, what is an IT General Control?").content)