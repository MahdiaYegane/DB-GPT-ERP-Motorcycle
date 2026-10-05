import json

from dbgpt._private.config import Config
from dbgpt.core import (
    ChatPromptTemplate,
    HumanPromptTemplate,
    MessagesPlaceholder,
    SystemPromptTemplate,
)
from dbgpt_app.scene import AppScenePromptTemplateAdapter, ChatScene
from dbgpt_app.scene.chat_db.auto_execute.out_parser import DbChatOutputParser

CFG = Config()


_PROMPT_SCENE_DEFINE_EN = "You are a database expert. "
_PROMPT_SCENE_DEFINE_ZH = "You are a database expert. "

_DEFAULT_TEMPLATE_EN = """
Please answer the user's question based on the database selected by the user and some \
of the available table structure definitions of the database.
Database name:
     {db_name}
Table structure definition:
     {table_info}

Constraint:
    1.Please understand the user's intention based on the user's question, and use the \
    given table structure definition to create a grammatically correct {dialect} sql. \
    If sql is not required, answer the user's question directly.. 
    2.Always limit the query to a maximum of {top_k} results unless the user specifies \
    in the question the specific number of rows of data he wishes to obtain.
    3.You can only use the tables provided in the table structure information to \
    generate sql. If you cannot generate sql based on the provided table structure, \
    please say: "The table structure information provided is not enough to generate \
    sql queries." It is prohibited to fabricate information at will.
    4.Please be careful not to mistake the relationship between tables and columns when\
     generating SQL.
    5.Please check the correctness of the SQL and ensure that the query performance is\
     optimized under correct conditions.
    6.Please choose the best one from the display methods given below for data \
    rendering, and put the type name into the name parameter value that returns \
    the required format. If you cannot find the most suitable one, use 'Table' as \
    the display method. , the available data display methods are as follows: \
    {display_type}
    
User Question:
    {user_input}
Please think step by step and respond according to the following JSON format:
    {response}
Ensure the response is correct json and can be parsed by Python json.loads.

"""

_DEFAULT_TEMPLATE_ZH = """
Please answer the user's question based on the database selected by the user and some \
of the available table definitions in that database.
Database name:
    {db_name}
Table definitions:
    {table_info}

Constraints:
    1. Understand the user's intent from the question and use the given table definitions \
    to create a grammatically correct {dialect} SQL statement. If no SQL is needed, answer the question directly.
    2. Unless the user specifies the exact number of rows desired, \
     always limit the query to at most {top_k} results.
    3. Only use tables from the provided schema to generate SQL. If SQL cannot be generated \
    from the provided schema, please say: "The provided schema information is not sufficient to generate a SQL query." Do not fabricate information.
    4. Take care not to confuse table-column relationships when generating SQL
    5. Verify SQL correctness and optimize query performance once correctness is ensured
    6.Please select the best display method from those listed below for data rendering, \
    put the type name in the name parameter of the required return format, and use \
    'Table' if no suitable option exists. Available display methods: {display_type}
User question:
    {user_input}
Please think step by step and reply in the following JSON format:
      {response}
Ensure the response is valid JSON parseable by Python json.loads.

"""

_DEFAULT_TEMPLATE = (
    _DEFAULT_TEMPLATE_EN if CFG.LANGUAGE == "en" else _DEFAULT_TEMPLATE_ZH
)

PROMPT_SCENE_DEFINE = (
    _PROMPT_SCENE_DEFINE_EN if CFG.LANGUAGE == "en" else _PROMPT_SCENE_DEFINE_ZH
)

RESPONSE_FORMAT_SIMPLE = {
    "thoughts": "thoughts summary to say to user",
    "direct_response": "If the context is sufficient to answer user, reply directly "
    "without sql",
    "sql": "SQL Query to run",
    "display_type": "Data display method",
}


# Temperature is a configuration hyperparameter that controls the randomness of
# language model output.
# A high temperature produces more unpredictable and creative results, while a low
# temperature produces more common and conservative output.
# For example, if you adjust the temperature to 0.5, the model will usually generate
# text that is more predictable and less creative than if you set the temperature to
# 1.0.
PROMPT_TEMPERATURE = 0

prompt = ChatPromptTemplate(
    messages=[
        SystemPromptTemplate.from_template(
            _DEFAULT_TEMPLATE,
            response_format=json.dumps(
                RESPONSE_FORMAT_SIMPLE, ensure_ascii=False, indent=4
            ),
        ),
        MessagesPlaceholder(variable_name="chat_history"),
        HumanPromptTemplate.from_template("{user_input}"),
    ]
)

prompt_adapter = AppScenePromptTemplateAdapter(
    prompt=prompt,
    template_scene=ChatScene.ChatWithDbExecute.value(),
    stream_out=True,
    output_parser=DbChatOutputParser(),
    temperature=PROMPT_TEMPERATURE,
)
CFG.prompt_template_registry.register(prompt_adapter, is_default=True)
