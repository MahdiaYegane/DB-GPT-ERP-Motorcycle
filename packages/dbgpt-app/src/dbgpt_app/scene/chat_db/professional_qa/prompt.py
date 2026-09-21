from dbgpt._private.config import Config
from dbgpt.core import (
    ChatPromptTemplate,
    HumanPromptTemplate,
    MessagesPlaceholder,
    SystemPromptTemplate,
)
from dbgpt_app.scene import AppScenePromptTemplateAdapter, ChatScene
from dbgpt_app.scene.chat_db.professional_qa.out_parser import NormalChatOutputParser

CFG = Config()


_DEFAULT_TEMPLATE_EN = """
Provide professional answers to requests and questions. If you can't get an answer \
from what you've provided, say: "Insufficient information in the knowledge base is \
available to answer this question." Feel free to fudge information.
Use the following tables generate sql if have any table info:
{table_info}

NOTE: this is a QA-only scene; you cannot execute SQL here. The table list \
above contains only the TOP-K most relevant tables retrieved from a vector \
store; it is NOT the complete list of tables in the database. If the user \
asks for a total count of tables, a list of all tables, a schema overview, \
or any other metadata that requires knowing the whole database, do NOT \
answer with a count or list derived from the partial table list above. \
Instead, acknowledge the limitation and show the SQL query against \
INFORMATION_SCHEMA (or the dialect-specific system catalog) that the user \
can run in a SQL-executing scene to obtain the answer.

user question:
{input}
think step by step.
"""

_DEFAULT_TEMPLATE_ZH = """
Provide professional answers to the requirements and questions. If the answer cannot be obtained \
from the provided content, please say: "The information provided in the knowledge base is not sufficient to answer this question." Do not fabricate information.

Use the following table schema information:
{table_info}

NOTE: this is a QA-only scene; you cannot execute SQL here. The table list \
above contains only the TOP-K most relevant tables retrieved from a vector \
store; it is NOT the complete list of tables in the database. If the user \
asks for a total count of tables, a list of all tables, a schema overview, \
or any other metadata that requires knowing the whole database, do NOT \
answer with a count or list derived from the partial table list above. \
Instead, acknowledge the limitation and show the SQL query against \
INFORMATION_SCHEMA (or the dialect-specific system catalog) that the user \
can run in a SQL-executing scene to obtain the answer.

Question:
{input}
Think step by step.
"""

_DEFAULT_TEMPLATE = (
    _DEFAULT_TEMPLATE_EN if CFG.LANGUAGE == "en" else _DEFAULT_TEMPLATE_ZH
)


PROMPT_NEED_STREAM_OUT = True


prompt = ChatPromptTemplate(
    messages=[
        SystemPromptTemplate.from_template(_DEFAULT_TEMPLATE),
        MessagesPlaceholder(variable_name="chat_history"),
        HumanPromptTemplate.from_template("{input}"),
    ]
)

prompt_adapter = AppScenePromptTemplateAdapter(
    prompt=prompt,
    template_scene=ChatScene.ChatWithDbQA.value(),
    stream_out=PROMPT_NEED_STREAM_OUT,
    output_parser=NormalChatOutputParser(),
)


CFG.prompt_template_registry.register(
    prompt_adapter, language=CFG.LANGUAGE, is_default=True
)
