# SKILL System - DB-GPT Agent Skill Loading System

## Overview

The SKILL system is an advanced feature of the DB-GPT Agent framework that allows agents to load and manage predefined skill packages, enabling modular and reusable agent capabilities.

## Core Files

```
packages/dbgpt-core/src/dbgpt/agent/skill/
├── __init__.py           # Module entry point, exports main classes
├── base.py              # Skill base class definition
├── parameters.py        # Skill parameter classes
├── manage.py           # Skill manager
└── loader.py           # Skill loader and builder
```

## Key Features

### 1. Skill Definition

A Skill consists of the following components:
- **Metadata**: skill metadata (name, description, version, type, tags)
- **Prompt Template**: system prompt template
- **Required Tools**: list of required tools
- **Required Knowledge**: list of required knowledge bases
- **Actions**: executable actions
- **Config**: skill-specific configuration parameters

### 2. Skill Types

| Type | Description |
|------|------|
| `Coding` | Coding skill |
| `DataAnalysis` | Data analysis skill |
| `WebSearch` | Web search skill |
| `KnowledgeQA` | Knowledge Q&A skill |
| `Chat` | Conversation skill |
| `Custom` | Custom skill |

## Quick Start

### 1. Create a Skill

```python
from dbgpt.agent.skill import SkillBuilder, SkillType

skill = (
    SkillBuilder(name="my_skill", description="My awesome skill")
    .with_version("1.0.0")
    .with_author("Your Name")
    .with_skill_type(SkillType.Coding)
    .with_tags(["coding", "python"])
    .with_prompt_template(
        "You are a coding assistant. Help users write clean, efficient code."
    )
    .with_required_tool("python_interpreter")
    .build()
)
```

### 2. Register the Skill

```python
from dbgpt.agent.skill import get_skill_manager, initialize_skill
from dbgpt.component import SystemApp

system_app = SystemApp()
initialize_skill(system_app)
skill_manager = get_skill_manager(system_app)

skill_manager.register_skill(
    skill_instance=skill,
    name="my_awesome_skill",
)
```

### 3. Create a Skill-based Agent

```python
from dbgpt.agent import ConversableAgent
from dbgpt.agent.skill import Skill

class SkillBasedAgent(ConversableAgent):
    def __init__(self, skill: Skill, **kwargs):
        super().__init__(**kwargs)
        self._skill = skill
        self._apply_skill_to_profile()

    @property
    def skill(self) -> Skill:
        return self._skill
```

### 4. Use the Agent

```python
agent = SkillBasedAgent(skill=skill)
await agent.bind(context).bind(llm_config).bind(memory).build()
```

## API Reference

### SkillBuilder

| Method | Parameters | Description |
|------|------|------|
| `with_version(version)` | version: str | Set the version |
| `with_author(author)` | author: str | Set the author |
| `with_skill_type(type)` | type: SkillType | Set the skill type |
| `with_tags(tags)` | tags: List[str] | Set tags |
| `with_prompt_template(template)` | template: str | Set the prompt template |
| `with_required_tool(name)` | name: str | Add a required tool |
| `with_required_knowledge(name)` | name: str | Add a required knowledge base |
| `with_action(action)` | action: Any | Add an action |
| `with_config(config)` | config: Dict | Set the configuration |
| `build()` | - | Build the Skill |

### SkillManager

| Method | Parameters | Return Value | Description |
|------|------|--------|------|
| `register_skill()` | skill_cls, skill_instance, name, metadata | None | Register a skill |
| `get_skill()` | name, skill_type, version | SkillBase | Get a skill |
| `get_skills_by_type()` | skill_type | List[SkillBase] | Get skills by type |
| `list_skills()` | - | List[Dict] | List all skills |

### SkillLoader

| Method | Parameters | Return Value | Description |
|------|------|--------|------|
| `load_skill_from_file()` | file_path | Optional[SkillBase] | Load a skill from a file |
| `load_skill_from_module()` | module_path | Optional[SkillBase] | Load a skill from a module |
| `load_skills_from_directory()` | directory, recursive | List[SkillBase] | Load all skills from a directory |

## File Formats

### JSON Format

```json
{
  "metadata": {
    "name": "web_search_assistant",
    "description": "Web search assistant",
    "version": "1.0.0",
    "author": "DB-GPT Team",
    "skill_type": "web_search",
    "tags": ["web", "search"]
  },
  "prompt_template": "You are a web search assistant.",
  "required_tools": ["google_search"],
  "required_knowledge": [],
  "config": {}
}
```

### Python Format

```python
from dbgpt.agent.skill import Skill, SkillMetadata, SkillType
from dbgpt.core import PromptTemplate

class CustomSkill(Skill):
    def __init__(self):
        metadata = SkillMetadata(
            name="custom_skill",
            description="A custom skill",
            version="1.0.0",
            skill_type=SkillType.Custom,
        )
        prompt = PromptTemplate.from_template("You are a custom assistant.")
        super().__init__(
            metadata=metadata,
            prompt_template=prompt,
        )
```

## Examples

### Full Example

See `examples/agents/skill_agent_example.py` for a complete usage example.

### Skill Files

- `skills/web_search_skill.json` - Web search skill example
- `skills/data_analysis_skill.json` - Data analysis skill example

### Implementation Guide

- `skills/skill_implementation_guide.py` - Detailed implementation guide
- `skills/INTEGRATION_GUIDE.md` - Guide for integrating into existing agents

## Integration Steps

1. **Import the SKILL module**
   ```python
   from dbgpt.agent.skill import Skill, SkillBuilder, get_skill_manager
   ```

2. **Modify the Agent class**
   ```python
   class MyAgent(ConversableAgent):
       def __init__(self, skill: Optional[Skill] = None, **kwargs):
           super().__init__(**kwargs)
           self._skill = skill
           if self._skill:
               self._apply_skill_to_profile()
   ```

3. **Initialize the Skill Manager**
   ```python
   from dbgpt.component import SystemApp
   system_app = SystemApp()
   initialize_skill(system_app)
   ```

4. **Register and use the Skill**
   ```python
   skill_manager = get_skill_manager(system_app)
   skill_manager.register_skill(skill_instance=skill)
   agent = MyAgent(skill=skill)
   ```

## Advanced Usage

### Dynamic Skill Switching

```python
class DynamicSkillAgent(ConversableAgent):
    def switch_skill(self, skill_name: str):
        self._skill = self._skills[skill_name]
        self._apply_skill_to_profile()
```

### Combining Multiple Skills

```python
class CompositeSkillAgent(ConversableAgent):
    def __init__(self, skills: List[Skill], **kwargs):
        super().__init__(**kwargs)
        self._skills = skills

    def get_all_tools(self) -> List[str]:
        all_tools = []
        for skill in self._skills:
            all_tools.extend(skill.required_tools)
        return list(set(all_tools))
```

## Best Practices

1. **Modular design**: each Skill focuses on a single domain
2. **Versioning**: use semantic version numbers (e.g. 1.0.0)
3. **Dependency declaration**: clearly declare required tools and knowledge bases
4. **Documentation**: write thorough documentation for each Skill
5. **Test coverage**: write unit tests for each Skill

## Troubleshooting

### FAQ

**Q: Skill fails to load?**
A: Check that the file path and JSON format are correct

**Q: Required tool not found?**
A: Make sure all required tools are provided when binding the Agent

**Q: Prompt template has no effect?**
A: Make sure `bind_prompt` is set correctly in `_apply_skill_to_profile`

## Contribution Guide

Contributions of new Skills are welcome! Please follow these steps:

1. Fork the project
2. Create a new Skill file
3. Write tests
4. Submit a Pull Request

## License

MIT License

## Contact

If you have questions or suggestions, please submit an Issue or Pull Request.
