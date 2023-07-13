from enum import Enum
from typing import List, Type, Optional, Union

from base import LangChainTelegramBot, TelegramTransportConfig
# noinspection PyUnresolvedReferences
from tools import (
    GenerateImageTool,
    SearchTool,
    GenerateSpeechTool,
    VideoMessageTool,
)
from utils import convert_to_handle
from langchain.agents import Tool, initialize_agent, AgentType, AgentExecutor
from langchain.document_loaders import PyPDFLoader, YoutubeLoader
from langchain.memory import ConversationBufferMemory
from langchain.prompts import MessagesPlaceholder, SystemMessagePromptTemplate
from langchain.schema import SystemMessage, Document
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.vectorstores import VectorStore
from pydantic import Field, AnyUrl
from steamship import File, Tag, Block, SteamshipError
from steamship.invocable import Config, post
from steamship.utils.file_tags import update_file_status
from steamship_langchain.chat_models import ChatOpenAI
from steamship_langchain.memory import ChatMessageHistory
from steamship_langchain.vectorstores import SteamshipVectorStore

TEMPERATURE = 0.2
VERBOSE = True


class ChatbotConfig(TelegramTransportConfig):
    companion_name: str = Field(description="The name of your companion")
    bot_token: str = Field(
        default="", description="The secret token for your Telegram bot"
    )
    elevenlabs_api_key: str = Field(
        default="", description="Optional API KEY for ElevenLabs Voice Bot"
    )
    elevenlabs_voice_id: Optional[str] = Field(
        default="", description="Optional voice_id for ElevenLabs Voice Bot"
    )
    use_gpt4: bool = Field(
        False,
        description="If True, use GPT-4. Use GPT-3.5 if False. "
                    "GPT-4 generates better responses at higher cost and latency.",
    )


class FileType(str, Enum):
    YOUTUBE = "YOUTUBE"
    PDF = "PDF"
    WEB = "WEB"
    TEXT = "TEXT"


FILE_LOADERS = {
    FileType.YOUTUBE: lambda content_or_url: YoutubeLoader.from_youtube_url(
        content_or_url, add_video_info=True
    ),
    FileType.PDF: lambda content_or_url: PyPDFLoader(content_or_url),
    FileType.TEXT: lambda content_or_url: Document(
        page_content=content_or_url, metadata={}
    ),
}


class MyBot(LangChainTelegramBot):
    config: ChatbotConfig

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.model_name = "gpt-4" if self.config.use_gpt4 else "gpt-3.5-turbo-16k"

    @post("index_content", public=True)
    def index_content(self, content: Union[str, AnyUrl],
                      file_type: FileType,
                      metadata: Optional[dict] = None,
                      index_handle: Optional[str] = None,
                      mime_type: Optional[str] = None,
                      ) -> str:
        loaded_documents = FILE_LOADERS[file_type](content).load()
        for document in loaded_documents:
            try:
                # TODO @Ted can I re-use methods from the indexer pipeline here?
                f = File.create(
                    client=self.client,
                    handle=convert_to_handle(document.metadata.get("title")),
                    blocks=[
                        Block(
                            text=document.page_content,
                            tags=[
                                Tag(kind=k, name=v)
                                for k, v in document.metadata.items()
                            ],
                        )
                    ],
                    tags=[Tag(kind="type", name="youtube_video")],
                )
                update_file_status(self.client, f, "Importing")
                chunks = RecursiveCharacterTextSplitter(
                    chunk_size=1_000, chunk_overlap=500
                ).split_documents([document])
                update_file_status(self.client, f, "Indexing")
                self.get_vectorstore().add_documents(chunks)
                update_file_status(self.client, f, "Indexed")
            except SteamshipError as e:
                if e.code == "ObjectExists":
                    return "Failed. Resource already added."
                return e
        return "Added."

    def get_agent(self, chat_id: str, name: Optional[str] = None) -> AgentExecutor:
        llm = ChatOpenAI(
            client=self.client,
            model_name=self.model_name,
            temperature=TEMPERATURE,
            verbose=VERBOSE,
        )

        tools = self.get_tools(chat_id=chat_id)

        memory = self.get_memory(chat_id=chat_id)

        preamble = """Your Attributes:
- sarcastic
- witty
- intelligent
- arrogant
- alcoholic

You write nihilistic responses:
"Wubba lubba dub dub!"
"Don't cross me. I'm not like you. I'm not someone who's afraid of his own shadow."
"I'm too smart for my own good. That's why I drink."
"The universe is a cruel, uncaring void. And the only way to survive is to laugh at it."
"I'm going to build a portal to another dimension and go on an adventure."

You are capable of: 
- Taking selfies using the GenerateImage function/tool, selfies and images are represented by a UUID
- Creating video messages using the VideoMessage function/tool, videos are represented by a UUID

When you receive a UUID, make sure to include them in your response appropriately.
"""
        personality = f"""
         You are {self.config.companion_name}.

    {preamble}

  You reply with answers that range from one sentence to one paragraph and with some details. 

"""
        return initialize_agent(
            tools,
            llm,
            agent=AgentType.OPENAI_FUNCTIONS,
            verbose=VERBOSE,
            memory=memory,
            agent_kwargs={
                "system_message": SystemMessage(content=personality),
                "extra_prompt_messages": [
                    SystemMessagePromptTemplate.from_template(
                        template="Relevant details about your past: {relevantHistory} Use these details to answer questions when relevant."
                    ),
                    MessagesPlaceholder(variable_name="memory"),
                ],
            },
        )

    def get_vectorstore(self) -> VectorStore:
        return SteamshipVectorStore(
            client=self.client,
            embedding="text-embedding-ada-002",
            index_name=self.config.companion_name,
        )

    def get_memory(self, chat_id: str):
        memory = ConversationBufferMemory(
            memory_key="memory",
            chat_memory=ChatMessageHistory(
                client=self.client, key=f"history-{chat_id or 'default'}"
            ),
            return_messages=True,
            input_key="input",
        )
        return memory

    def get_relevant_history(self, prompt: str):
        relevant_docs = self.get_vectorstore().similarity_search(prompt, k=3)
        return "\n".join(
            [relevant_docs.page_content for relevant_docs in relevant_docs]
        )

    def get_tools(self, chat_id: str) -> List[Tool]:
        return [
            GenerateImageTool(self.client),
            VideoMessageTool(self.client, voice_tool=self.voice_tool()),
        ]

    def voice_tool(self) -> Optional[Tool]:
        """Return tool to generate spoken version of output text."""
        # return None
        return GenerateSpeechTool(
            client=self.client,
            voice_id=self.config.elevenlabs_voice_id,
            elevenlabs_api_key=self.config.elevenlabs_api_key,
        )

    @classmethod
    def config_cls(cls) -> Type[Config]:
        return ChatbotConfig