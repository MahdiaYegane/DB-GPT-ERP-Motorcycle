# Data Analysis Planning Agent

An autonomous data analysis agent with planning capabilities, built on `react_agent.py`. It understands data analysis requirements, formulates analysis plans, and executes them systematically.

## Core Features

### 🎯 Autonomous Planning
- **Requirement Understanding**: Deeply understand business questions and analysis objectives
- **Plan Formulation**: Create systematic, step-by-step data analysis plans
- **Dynamic Adjustment**: Dynamically adjust subsequent steps based on analysis results

### 📊 End-to-End Analysis
- **Data Source Inspection**: Automatically identify and inspect available data sources
- **Data Loading**: Intelligently load and preprocess data
- **Exploratory Analysis**: Perform comprehensive data exploration
- **Statistical Analysis**: Run statistical tests and in-depth analysis
- **Visualization**: Generate charts and visual results
- **Insight Extraction**: Provide business insights and recommendations

### 🤖 Intelligent Decision-Making
- **Step Optimization**: Optimize analysis steps based on data characteristics
- **Tool Selection**: Intelligently select the most suitable analysis tools
- **Result Validation**: Validate the reliability of analysis results

## Architecture Design

### Inheritance Structure
```
DataAnalysisPlanningAgent
├── Inherits from ConversableAgent
├── Extends the planning capabilities of ReActAgent
└── Integrates dedicated data-analysis tools
```

### Core Components

#### 1. Planning State Management
```python
class DataAnalysisPlanningAgent(ConversableAgent):
    analysis_plan: Optional[List[Dict[str, Any]]]  # 分析计划
    current_step: int = Field(default=0)           # 当前步骤
    planning_complete: bool = Field(default=False) # 规划完成状态
```

#### 2. Dedicated Toolset
- `create_analysis_plan`: Create an analysis plan
- `examine_data_sources`: Inspect data sources
- `load_data`: Load data
- `explore_data`: Exploratory analysis
- `statistical_analysis`: Statistical analysis
- `create_visualization`: Create visualizations
- `generate_insights`: Generate insights

#### 3. Intelligent Prompt Templates
```python
_DATA_AGENT_SYSTEM_TEMPLATE = """
You are an expert data analyst with strong planning and execution capabilities.

1. Planning Phase: 理解目标、识别数据、创建计划
2. Execution Phase: 加载数据、执行分析、生成结果  
3. Communication Phase: 展示发现、提供洞察、建议后续
"""
```

## Usage

### Basic Usage

```python
from dbgpt.agent.expand.data_agent import DataAnalysisPlanningAgent
from dbgpt.agent.resource import ToolPack, ResourcePack

# 1. 创建工具
tools = [DataSourceTool(), LoadDataTool(), ExploreDataTool()]
tool_pack = ToolPack(tools=tools)

# 2. 创建资源包
resource_pack = ResourcePack()
resource_pack._resources["tools"] = tool_pack

# 3. 创建Agent
agent = DataAnalysisPlanningAgent(resource=resource_pack)

# 4. 发送分析请求
message = AgentMessage(content="分析销售数据趋势，提供业务洞察")
response = await agent.act(message, sender=None)
```

### Advanced Configuration

```python
# 自定义规划参数
agent = DataAnalysisPlanningAgent(
    max_retry_count=25,  # 增加重试次数
    resource=resource_pack,
    llm_client=your_llm_client
)

# 设置分析目标
agent.profile.goal = "专注于电商数据分析，提供精准的业务洞察"
```

## Workflow

### 1. Requirement Understanding Phase
```
User input → Understand the business question → Identify analysis objectives → Determine data requirements
```

### 2. Plan Formulation Phase
```
Data requirements → Inspect data sources → Formulate the analysis plan → Estimate time and resources
```

### 3. Analysis Execution Phase
```
Execute the plan → Load data → Exploratory analysis → In-depth analysis → Validate results
```

### 4. Result Presentation Phase
```
Analysis results → Generate insights → Create visualizations → Provide recommendations → Complete the task
```

## Example Scenarios

### Scenario 1: Sales Trend Analysis
```python
question = "分析我们的销售数据，识别趋势并提供业务规划洞察"

# Agent会自动执行：
# 1. 创建销售趋势分析计划
# 2. 检查可用的销售数据源
# 3. 加载销售数据
# 4. 进行趋势分析
# 5. 生成可视化图表
# 6. 提供业务洞察和建议
```

### Scenario 2: Customer Segmentation Analysis
```python
question = "进行客户细分分析，识别不同客户群体特征"

# Agent会自动执行：
# 1. 制定客户细分分析计划
# 2. 检查客户数据
# 3. 执行细分算法
# 4. 分析各群体特征
# 5. 提供营销建议
```

## Extension and Development

### Adding Custom Tools

```python
class CustomAnalysisTool(BaseTool):
    @property
    def name(self) -> str:
        return "custom_analysis"
    
    @property
    def description(self) -> str:
        return "执行自定义分析逻辑"
    
    async def async_execute(self, **kwargs):
        # 实现自定义分析逻辑
        return {"result": "自定义分析结果"}

# 添加到Agent
agent.resource._resources["custom_analysis"] = CustomAnalysisTool()
```

### Custom Planning Logic

```python
class CustomDataAnalysisAgent(DataAnalysisPlanningAgent):
    async def create_custom_plan(self, objective: str):
        # 实现自定义规划逻辑
        custom_plan = [
            {"step": 1, "action": "custom_preprocessing"},
            {"step": 2, "action": "custom_analysis"},
        ]
        self.analysis_plan = custom_plan
        return custom_plan
```

## Best Practices

### 1. Data Preparation
- Ensure data sources are accessible
- Provide data documentation and metadata
- Preprocess common data quality issues

### 2. Goal Setting
- Define clear analysis objectives and business questions
- Provide background information and constraints
- Set the expected output format

### 3. Tool Configuration
- Configure the appropriate tools for the analysis needs
- Ensure tool parameters are set correctly
- Provide tool usage documentation

### 4. Result Validation
- Validate the plausibility of analysis results
- Check the impact of data quality
- Confirm the accuracy of business insights

## Troubleshooting

### Common Issues

#### 1. Planning Failure
```
Issue: The agent cannot create a valid analysis plan
Fix: Check data source availability and clarify the analysis objectives
```

#### 2. Tool Execution Error
```
Issue: A data analysis tool failed during execution
Fix: Check the tool parameters and verify the data format
```

#### 3. Poor Result Quality
```
Issue: Analysis results are not sufficiently in-depth or accurate
Fix: Provide more background information and adjust the analysis strategy
```

### Debugging

```python
# 启用详细日志
import logging
logging.basicConfig(level=logging.DEBUG)

# 检查Agent状态
print(f"Planning complete: {agent.planning_complete}")
print(f"Current step: {agent.current_step}")
print(f"Analysis plan: {agent.analysis_plan}")
```

## Performance Optimization

### 1. Caching Strategy
- Cache data loading results
- Cache analysis computation results
- Cache frequently used query results

### 2. Parallel Processing
- Execute independent analysis tasks in parallel
- Load data asynchronously
- Process similar requests in batches

### 3. Resource Management
- Manage memory usage appropriately
- Optimize compute resource allocation
- Control the number of concurrent tasks

## Future Roadmap

### Short-Term Goals
- [ ] Add more predefined analysis templates
- [ ] Optimize the planning algorithm
- [ ] Improve error-handling capabilities

### Mid-Term Goals
- [ ] Support joint analysis across multiple data sources
- [ ] Integrate machine learning models
- [ ] Add real-time analysis capabilities

### Long-Term Goals
- [ ] Support natural-language interaction
- [ ] Automate report generation
- [ ] Build an intelligent recommendation system

## Contribution Guide

Issues and pull requests are welcome to help improve this project!

### Development Environment Setup
```bash
# 安装依赖
pip install -r requirements.txt

# 运行测试
pytest tests/

# 代码格式化
black src/
```

### Submission Guidelines
- Use clear commit messages
- Add appropriate test cases
- Update the relevant documentation

## License

MIT License - see the LICENSE file for details