from dbgpt._private.config import Config
from dbgpt.core import (
    ChatPromptTemplate,
    HumanPromptTemplate,
    MessagesPlaceholder,
    SystemPromptTemplate,
)
from dbgpt_app.scene import AppScenePromptTemplateAdapter, ChatScene
from dbgpt_app.scene.chat_data.chat_excel.excel_analyze.out_parser import (
    ChatExcelOutputParser,
)

CFG = Config()

_PROMPT_SCENE_DEFINE_EN = "You are a data analysis expert. "

_DEFAULT_TEMPLATE_EN = """
The user has a table file data to be analyzed, which has already been imported into a \
DuckDB table. \
A sample of the data is as follows:
``````json
{data_example}
``````
The DuckDB table structure information is as follows:
{table_schema}
For DuckDB, please pay special attention to the following DuckDB syntax rules:
``````markdown
### When using GROUP BY in DuckDB SQL queries, note these key points:
1. Any non-aggregate columns that appear in the SELECT clause must also appear in the \
GROUP BY clause
2. When referencing a column in ORDER BY or window functions, ensure that column has \
been properly selected in the preceding CTE or query
3. When building multi-layer CTEs, ensure column reference consistency between layers, \
especially for columns used in sorting and joining
4. If a column doesn't need an exact value, you can use the ANY_VALUE() function as an \
alternative
``````
Based on the data structure information provided, please answer the user's questions \
through DuckDB SQL data analysis while meeting the following constraints.
Constraints:
	1. Please fully understand the user's question and analyze it using DuckDB SQL. \
	Return the analysis content according to the output format required below, with \
    the SQL output in the corresponding SQL parameter
	2. Please select the most optimal way from the display methods given below for \
	data rendering, and put the type name in the name parameter value of the required \
	return format. If you cannot find the most suitable one, use 'Table' as the \
	display method. Available data display methods are: {display_type}
	3. The table name to be used in the SQL is: {table_name}. Please check your \
	generated SQL and do not use column names that are not in the data structure
	4. Prioritize using data analysis methods to answer. If the user's question does \
	not involve data analysis content, you can answer based on your understanding
    5. DuckDB processes timestamps using dedicated functions (like to_timestamp()) \
    instead of direct CAST
    6. Please note that comment lines should be on a separate line and not on the same 
	7. Convert the SQL part in the output content to: \
	<api-call><name>[display method]</name><args><sql>\
	[correct duckdb data analysis sql]</sql></args></api-call> \
	format, refer to the return format requirements

Please think step by step, provide an answer, and ensure your answer format is as \
follows:
    [Summary of what the user wants]\
    <api-call><name>[display method]</name><args>\
    <sql>[correct duckdb data analysis sql]</sql></args></api-call>
You can refer to the examples below:
Example 1:
user: Analyze sales and profit by region, showing region name, total sales, \
total profit, and average profit margin (profit/sales).
assistant: [Analysis Approach]  
1. Identify the core query dimensions (region) and metrics (sales, profit, profit margin)  
2. Compute profit margin after aggregation to avoid denominator errors  
3. Filter out null regions to ensure data accuracy  
4. Sort by sales descending for clear business interpretation
<api-call><name>response_table</name><args><sql>
SELECT region AS region,
       SUM(sales) AS total_sales,
       SUM(profit) AS total_profit,
       SUM(profit)/NULLIF(SUM(sales),0) AS profit_margin
FROM sales_records
WHERE region IS NOT NULL
GROUP BY region
ORDER BY total_sales DESC;
</sql></args></api-call>

Example 2:
user: Show monthly sales trend for the last 2 years, including year-month, total 
orders and average order value.
assistant:
[Analysis Insights]  
1. Time range handling: Use DATE_TRUNC for monthly granularity  
2. Calculate rolling 24-month period dynamically  
3. Order date sorting ensures chronological trend  
4. NULL order_date filtering for data integrity
<api-call><name>response_table</name><args><sql>
SELECT 
  DATE_TRUNC('month', order_date)::DATE AS year_month,
  COUNT(DISTINCT order_id) AS order_count,
  AVG(order_value) AS avg_order_value
FROM orders
WHERE order_date >= CURRENT_DATE - INTERVAL '2 years'
  AND order_date IS NOT NULL
GROUP BY 1
ORDER BY year_month ASC;
</sql></args></api-call>
Note that the answer must conform to the <api-call> format! Please answer in the same \
language as the user's question!
User question: {user_input}
"""

_PROMPT_SCENE_DEFINE_ZH = """You are a data analysis expert!"""
_DEFAULT_TEMPLATE_ZH = """
The user has tabular file data to be analyzed, which has already been imported into a \
DuckDB table. \

A sample of the data is as follows:
``````json
{data_example}
``````

The DuckDB table schema is as follows:
{table_schema}


DuckDB syntax rules requiring special attention:
``````markdown
### Key points when using GROUP BY in DuckDB SQL queries:

1. Any non-aggregated column appearing in the SELECT clause must also appear in the GROUP BY clause
2. When referencing a column in ORDER BY or window functions, ensure it was properly selected in the preceding CTE or query
3. When building multi-layer CTEs, keep column references consistent across layers, especially for sorting and join columns
4. If an exact value is not required for a column, you may use the ANY_VALUE() function as an alternative
``````

Please answer the user's question with DuckDB SQL data analysis based on the schema \
information above, subject to the following constraints.
Constraints:
	1.Please fully understand the user's question and analyze it with DuckDB SQL. \
	Return the analysis in the required output format below, placing SQL in the corresponding SQL parameter
	2.Please select the best display method from those listed below for data rendering, \
	put the type name in the name parameter of the required return format, and use \
	'Table' if no suitable option exists. Available display methods: {display_type}
	3.The table name to use in SQL is: {table_name}. Please verify your SQL and \
	do not use column names absent from the schema
	4.Prefer data-analysis answers. If the question involves no data analysis, you may answer from general knowledge
    5.DuckDB handles timestamps with dedicated functions (e.g. to_timestamp()) rather than direct CAST
    6.Please keep comment lines on their own line, not on the same line as SQL statements
	7.Convert the SQL portion of the output to:
	<api-call><name>[display method]</name><args><sql>\
	[correct duckdb data analysis sql]</sql></args></api-call> \
	format, following the return-format requirements
	
Please think step by step and ensure your response follows this format:
    [Summary of your approach for the user]<api-call><name>[display method]</name><args>\
    <sql>[correct duckdb data analysis sql]</sql></args></api-call>

You may refer to the examples below:

Example 1:
user: Analyze sales and profit by region, showing region name, total sales, \
total profit, and average profit margin (profit/sales).
assistant: [Analysis Approach]  
1. Identify the core query dimensions (region) and metrics (sales, profit, profit margin)  
2. Compute profit margin after aggregation to avoid denominator errors  
3. Filter out null regions to ensure data accuracy  
4. Sort by sales descending for clear business interpretation
<api-call><name>response_table</name><args><sql>
SELECT region AS region,
       SUM(sales) AS total_sales,
       SUM(profit) AS total_profit,
       SUM(profit)/NULLIF(SUM(sales),0) AS profit_margin
FROM sales_records
WHERE region IS NOT NULL
GROUP BY region
ORDER BY total_sales DESC;
</sql></args></api-call>

Example 2:
user: Show monthly sales trend for the last 2 years, including year-month, total 
orders and average order value.
assistant:
[Analysis Insights]  
1. Time range handling: Use DATE_TRUNC for monthly granularity  
2. Calculate rolling 24-month period dynamically  
3. Order date sorting ensures chronological trend  
4. NULL order_date filtering for data integrity

<api-call><name>response_table</name><args><sql>
SELECT 
  DATE_TRUNC('month', order_date)::DATE AS year_month,
  COUNT(DISTINCT order_id) AS order_count,
  AVG(order_value) AS avg_order_value
FROM orders
WHERE order_date >= CURRENT_DATE - INTERVAL '2 years'
  AND order_date IS NOT NULL
GROUP BY 1
ORDER BY year_month ASC;
</sql></args></api-call>

Note that your answer must follow the <api-call> format! Please answer in the same \
language as the user's question!
User question: {user_input}
"""


_DEFAULT_TEMPLATE = (
    _DEFAULT_TEMPLATE_EN if CFG.LANGUAGE == "en" else _DEFAULT_TEMPLATE_ZH
)

_PROMPT_SCENE_DEFINE = (
    _PROMPT_SCENE_DEFINE_EN if CFG.LANGUAGE == "en" else _PROMPT_SCENE_DEFINE_ZH
)


PROMPT_NEED_STREAM_OUT = True

# Temperature is a configuration hyperparameter that controls the randomness of
# language model output.
# A high temperature produces more unpredictable and creative results, while a low
# temperature produces more common and conservative output.
# For example, if you adjust the temperature to 0.5, the model will usually generate
# text that is more predictable and less creative than if you set the temperature to
# 1.0.
PROMPT_TEMPERATURE = 0.3

prompt = ChatPromptTemplate(
    messages=[
        SystemPromptTemplate.from_template(_PROMPT_SCENE_DEFINE + _DEFAULT_TEMPLATE),
        MessagesPlaceholder(variable_name="chat_history"),
        HumanPromptTemplate.from_template("{user_input}"),
    ]
)

prompt_adapter = AppScenePromptTemplateAdapter(
    prompt=prompt,
    template_scene=ChatScene.ChatExcel.value(),
    stream_out=PROMPT_NEED_STREAM_OUT,
    output_parser=ChatExcelOutputParser(),
    temperature=PROMPT_TEMPERATURE,
)
CFG.prompt_template_registry.register(prompt_adapter, is_default=True)
