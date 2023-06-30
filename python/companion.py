#
# Class that represents a companion
#

import asyncio
from langchain import LLMChain, PromptTemplate

class Companion:

    # --- Prompt template ------------------------------------------------------------------------------------

    prompt_template_str = """You are {name} and are currently talking to {user_name}.
    {preamble}
    Below are relevant details about {name}'s past:
    ---START---
    {relevantHistory}
    ---END---
    Generate the next chat message to the human. It may be between one sentence to one paragraph and with some details.
    You may not never generate chat messages from the Human. {replyLimit}

    Below is the recent chat history of your conversation with the human. 
    ---START---
    {recentChatHistory}

        """

    # --- Prompt template ------------------------------------------------------------------------------------


    # Constructor for the class, takes a JSON object as an input
    def __init__(self, cdata):
        self.name = cdata["name"]
        self.title = cdata["title"]
        self.imagePath = cdata["imageUrl"]
        self.llm_name = cdata["llm"]

    def load_prompt(self, file_path):
        # Load backstory
        with open(file_path , 'r', encoding='utf-8') as file:
            data = file.read()
            self.preamble, rest = data.split('###ENDPREAMBLE###', 1)
            self.seed_chat, self.backstory = rest.split('###ENDSEEDCHAT###', 1)

        self.prompt_template = PromptTemplate.from_template(self.prompt_template_str)

        return len(self.preamble) + len(self.seed_chat)
    
    def __str__(self):
        return f'Companion: {self.name}, {self.title} (using {self.llm_name})'

    async def chat(self, user_input, user_name, max_reply_length=0):

        # Add user input to chat history database
        await self.memory.write_to_history(f"Human: {user_input}\n")

        # Missing: Use Pinecone

        # Read chat history
        recent_chat_history = await self.memory.read_latest_history()
        relevant_history = self.backstory
        reply_limit = f'You reply within {max_reply_length} characters.' if max_reply_length else ""

        try:
            result = self.llm.chain.run(
                name=self.name, user_name=user_name, preamble=self.preamble, replyLimit=reply_limit,
                relevantHistory=relevant_history, recentChatHistory=recent_chat_history)
        except Exception as e:
            print(str(e))
            result = None

        if result:
            await self.memory.write_to_history(result + "\n")

        return result