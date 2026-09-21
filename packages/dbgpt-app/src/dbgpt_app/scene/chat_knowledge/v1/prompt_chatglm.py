from dbgpt._private.config import Config
from dbgpt.core import (
    ChatPromptTemplate,
    HumanPromptTemplate,
    MessagesPlaceholder,
    SystemPromptTemplate,
)
from dbgpt_app.scene import AppScenePromptTemplateAdapter, ChatScene
from dbgpt_app.scene.chat_normal.out_parser import NormalChatOutputParser

CFG = Config()

PROMPT_SCENE_DEFINE = """A chat between a curious user and an artificial intelligence \
assistant, who very familiar with database related knowledge. 
    The assistant gives helpful, detailed, professional and polite answers to the \
    user's questions. """


_DEFAULT_TEMPLATE_ZH = """ Based on the known information below, answer the user's question \
professionally and concisely.
If the answer cannot be obtained from the provided content, please say: "The content provided in the knowledge base \
is not sufficient to answer this question." Do not fabricate information. 
            Known information: 
            {context}
            Question:
            {question}
"""
_DEFAULT_TEMPLATE_EN = """ Based on the known information below, provide users with \
professional and concise answers to their questions. If the answer cannot be obtained \
from the provided content, please say: "The information provided in the knowledge base \
is not sufficient to answer this question." It is forbidden to make up information \
randomly. 
            known information: 
            {context}
            question:
            {question}
"""

_DEFAULT_TEMPLATE = (
    _DEFAULT_TEMPLATE_EN if CFG.LANGUAGE == "en" else _DEFAULT_TEMPLATE_ZH
)


PROMPT_NEED_STREAM_OUT = True

prompt = ChatPromptTemplate(
    messages=[
        SystemPromptTemplate.from_template(_DEFAULT_TEMPLATE),
        MessagesPlaceholder(variable_name="chat_history"),
        HumanPromptTemplate.from_template("{question}"),
    ]
)

prompt_adapter = AppScenePromptTemplateAdapter(
    prompt=prompt,
    template_scene=ChatScene.ChatKnowledge.value(),
    stream_out=True,
    output_parser=NormalChatOutputParser(),
)

CFG.prompt_template_registry.register(
    prompt_adapter,
    language=CFG.LANGUAGE,
    is_default=False,
    model_names=["chatglm-6b-int4", "chatglm-6b", "chatglm2-6b", "chatglm2-6b-int4"],
)
