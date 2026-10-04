# DB-GPT Sandbox

Background: AI agents are becoming a powerful tool for using AI to solve diverse problems in real-world environments. However, task isolation and security in real environments are essential considerations for enterprise adoption. DB-GPT Agent currently does not provide a unified, extensible, secure sandbox environment.

#### Objectives

Implement a secure sandbox execution environment for DB-GPT Agent (supporting the execution of agents and tools, as well as multi-language code execution). This consists of three parts:

1. Build a secure code execution environment based on DB-GPT Agent + Docker containers, supporting the execution of Python, Shell, Node.js, and other code, and refactor the existing DB-GPT code-execution agent accordingly.
2. Support a stateful sandbox environment: repeated code executions can run in the same environment, and changes from a previous execution carry over to the next execution (for example, a PyPI dependency installed in the first execution remains available in the second execution).
3. Implement a pluggable secure sandbox environment with a unified sandbox interface, supporting Docker, Podman, local processes (based on Cgroup/Namespace/WebAssembly, etc.), and other sandbox backends.

#### Deliverables

1. Project design document (including architecture diagrams, conceptual diagrams, implementation details, etc.)
2. Core modules of the secure sandbox environment (unified sandbox interface, Docker implementation, and local-process implementation)
3. Complete usage tutorial documentation
4. An agent example built on the sandbox environment that supports the execution of Python and other code