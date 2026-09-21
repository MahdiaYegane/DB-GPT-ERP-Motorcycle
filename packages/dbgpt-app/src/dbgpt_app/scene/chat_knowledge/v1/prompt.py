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
The assistant gives helpful, detailed, professional and polite answers to the user's \
questions. """


_DEFAULT_TEMPLATE_ZH = """ Based on the known information below, follow the constraints and answer \
the user's question professionally and concisely.
Constraints:
     1.If the known information contains special markdown elements such as images, links, tables, or code blocks, \
     keep the original images, links, tables, and code tags in the answer without dropping or modifying them, \
     e.g. image format: ![image.png](xxx), link format: [xxx](xxx), \
     table format: |xxx|xxx|xxx|, code format: ```xxx```.
     2.If the answer cannot be obtained from the provided content, please say: "The content provided in the knowledge base is not sufficient to answer this question." \
     Do not fabricate information.
     3.Prefer summarizing the answer in numbered points (1. 2. 3.) displayed in markdown format.
     4.When citing known information, append the reference index as a superscript [1] [2] at the end of the sentence, \
where the number corresponds to the index prefixed before each fragment in the known information, for traceability. \
Multiple fragments can be cited as [1][3].
            Known information:
            {context}
            Question:
            {question},please answer in the same language as the user.
"""
_DEFAULT_TEMPLATE_EN = """ Based on the known information below, provide users with \
professional and concise answers to their questions.
constraints:
    1.Ensure to include original markdown formatting elements such as images, links, \
    tables, or code blocks without alteration in the response if they are present in \
    the provided information.
        For example, image format should be ![image.png](xxx), link format [xxx](xxx), \
        table format should be represented with |xxx|xxx|xxx|, and code format with xxx.
    2.If the information available in the knowledge base is insufficient to answer the \
    question, state clearly: "The content provided in the knowledge base is not enough \
    to answer this question," and avoid making up answers.
    3.When responding, it is best to summarize the points in the order of 1, 2, 3, And \
    displayed in markdown format.
    4.When citing known information, append the reference index as a superscript \
like [1] [2] at the end of the sentence, where the number corresponds to the \
index prefixed before each fragment in the known information, for traceability. \
Multiple fragments can be cited as [1][3].
            known information:
            {context}
            question:
            {question},when answering, use the same language as the "user".
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
    stream_out=PROMPT_NEED_STREAM_OUT,
    output_parser=NormalChatOutputParser(),
)

CFG.prompt_template_registry.register(
    prompt_adapter, language=CFG.LANGUAGE, is_default=True
)
