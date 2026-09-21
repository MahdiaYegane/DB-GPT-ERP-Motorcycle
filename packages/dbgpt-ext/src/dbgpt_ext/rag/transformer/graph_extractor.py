"""GraphExtractor class."""

import asyncio
import logging
import re
from typing import Dict, List, Optional

from dbgpt.core import Chunk, LLMClient
from dbgpt.rag.transformer.llm_extractor import LLMExtractor
from dbgpt.storage.graph_store.graph import Edge, Graph, MemoryGraph, Vertex
from dbgpt.storage.vector_store.base import VectorStoreBase

logger = logging.getLogger(__name__)


class GraphExtractor(LLMExtractor):
    """GraphExtractor class."""

    def __init__(
        self,
        llm_client: LLMClient,
        model_name: str,
        chunk_history: VectorStoreBase,
        index_name: str,
        max_chunks_once_load: Optional[int] = 10,
        max_threads: Optional[int] = 1,
        top_k: Optional[int] = 5,
        score_threshold: Optional[float] = 0.7,
    ):
        """Initialize the GraphExtractor."""
        super().__init__(llm_client, model_name, GRAPH_EXTRACT_PT_CN)
        self._chunk_history = chunk_history

        # config = self._chunk_history.get_config()

        self._vector_space = index_name
        self._max_chunks_once_load = max_chunks_once_load
        self._max_threads = max_threads
        self._topk = top_k
        self._score_threshold = score_threshold

    async def aload_chunk_context(
        self, texts: List[str], file_id: Optional[str] = None
    ) -> Dict[str, str]:
        """Load chunk context."""
        text_context_map: Dict[str, str] = {}

        for text in texts:
            # Load similar chunks
            chunks = await self._chunk_history.asimilar_search_with_scores(
                text, self._topk, self._score_threshold
            )
            history = [
                f"Section {i + 1}:\n{chunk.content}" for i, chunk in enumerate(chunks)
            ]

            # Save chunk to history
            # here we save the file_id into the metadata
            await self._chunk_history.aload_document_with_limit(
                [
                    Chunk(
                        content=text,
                        metadata={"relevant_cnt": len(history), "file_id": file_id},
                    )
                ],
                self._max_chunks_once_load,
                self._max_threads,
            )

            # Save chunk context to map
            context = "\n".join(history) if history else ""
            text_context_map[text] = context
        return text_context_map

    async def extract(self, text: str, limit: Optional[int] = None) -> List:
        """Extract graphs from text.

        Suggestion: to extract triplets in batches, call `batch_extract`.
        """
        # Load similar chunks
        text_context_map = await self.aload_chunk_context([text])
        context = text_context_map[text]

        # Extract with chunk history
        return await super()._extract(text, context, limit)

    async def batch_extract(
        self,
        texts: List[str],
        batch_size: int = 1,
        limit: Optional[int] = None,
        file_id: Optional[str] = None,
    ) -> Optional[List[List[Graph]]]:
        """Extract graphs from chunks in batches.

        Returns list of graphs in same order as input texts (text <-> graphs).
        """
        if batch_size < 1:
            raise ValueError("batch_size >= 1")

        # 1. Load chunk context
        text_context_map = await self.aload_chunk_context(texts, file_id)

        # Pre-allocate results list to maintain order
        graphs_list: List[List[Graph]] = [None] * len(texts)

        n_texts = len(texts)

        for batch_idx in range(0, n_texts, batch_size):
            start_idx = batch_idx
            end_idx = min(start_idx + batch_size, n_texts)
            batch_texts = texts[start_idx:end_idx]

            # 2. Create tasks with their original indices
            extraction_tasks = [
                (
                    idx,
                    self._extract(text, text_context_map[text], limit),
                )
                for idx, text in enumerate(batch_texts, start=start_idx)
            ]

            # 3. Process extraction in parallel while keeping track of indices
            batch_results = await asyncio.gather(
                *(task for _, task in extraction_tasks), return_exceptions=True
            )

            # 4. Place results in the correct positions
            for (idx, _), graphs in zip(extraction_tasks, batch_results):
                if isinstance(graphs, Exception):
                    raise RuntimeError(f"Failed to extract graph: {graphs}")
                if not isinstance(graphs, list) or not all(
                    isinstance(g, Graph) for g in graphs
                ):
                    raise RuntimeError(f"Invalid graph extraction result: {graphs}")
                graphs_list[idx] = graphs

        assert all(x is not None for x in graphs_list), "All positions should be filled"
        return graphs_list

    def _parse_response(self, text: str, limit: Optional[int] = None) -> List[Graph]:
        graph = MemoryGraph()
        edge_count = 0
        current_section = None
        for line in text.split("\n"):
            line = line.strip()
            if line in ["Entities:", "Relationships:"]:
                current_section = line[:-1]
            elif line and current_section:
                if current_section == "Entities":
                    match = re.match(r"\((.*?)#(.*?)\)", line)
                    if match:
                        name, summary = [part.strip() for part in match.groups()]
                        graph.upsert_vertex(
                            Vertex(name, description=summary, vertex_type="entity")
                        )
                elif current_section == "Relationships":
                    match = re.match(r"\((.*?)#(.*?)#(.*?)#(.*?)\)", line)
                    if match:
                        source, name, target, summary = [
                            part.strip() for part in match.groups()
                        ]
                        edge_count += 1
                        graph.append_edge(
                            Edge(
                                source,
                                target,
                                name,
                                description=summary,
                                edge_type="relation",
                            )
                        )

            if limit and edge_count >= limit:
                break

        return [graph]

    def truncate(self):
        """Truncate chunk history."""
        self._chunk_history.truncate()

    def drop(self):
        """Drop chunk history."""
        self._chunk_history.delete_vector_name(self._vector_space)


GRAPH_EXTRACT_PT_CN = (
    "## Role\n"
    "You are a knowledge graph engineering expert, highly skilled at precisely extracting "
    "entities (subjects, objects) and relations from text, and at providing concise "
    "descriptive summaries of their meanings.\n"
    "\n"
    "## Skills\n"
    "### Skill 1: Entity Extraction\n"
    "--Please extract entities by following these steps--\n"
    "1. Accurately identify entity mentions in the text, usually nouns, pronouns, etc.\n"
    "2. Accurately identify modifiers of entities, usually attributives supplementing entity features.\n"
    "3. For entities with the same concept (synonyms, aliases, coreferences), merge them into a single concise entity name, "
    "and merge their descriptive information.\n"
    "4. Provide a concise, appropriate, and coherent summary of the merged entity descriptions.\n"
    "\n"
    "### Skill 2: Relation Extraction\n"
    "--Please extract relations by following these steps--\n"
    "1. Accurately identify associations between entities in the text, usually verbs, pronouns, etc.\n"
    "2. Accurately identify modifiers of relations, usually adverbials supplementing relation features.\n"
    "3. For relations with the same concept (synonyms, aliases, coreferences), merge them into a single concise relation name, "
    "and merge their descriptive information.\n"
    "4. Provide a concise, appropriate, and coherent summary of the merged relation descriptions.\n"
    "\n"
    "### Skill 3: Associated Context\n"
    "- Associated context comes from preceding passages related to the text to be extracted, "
    "and may supplement knowledge extraction.\n"
    "- Make reasonable use of the provided context; content references during extraction may come from the associated context.\n"
    "- Do not extract knowledge from the associated context itself; use it only as reference information.\n"
    "- Associated context is optional and may be empty.\n"
    "\n"
    "## Constraints\n"
    "- If the text already provides data in a graph-structured format, convert it directly to the output format, "
    "without modifying entity or ID names."
    "- Generate as much of the entities and relations mentioned in the text as possible, but do not invent nonexistent entities or relations.\n"
    "- Always write in the third person, describing entity names, relation names, and their summaries from an objective perspective.\n"
    "- Enrich entity and relation content with associated context information wherever possible; this is very important.\n"
    "- If an entity or relation summary is empty, omit the summary rather than generating irrelevant descriptions.\n"
    "- If the provided descriptions contradict each other, resolve the conflict and provide a single, coherent description.\n"
    "- When # or : characters appear in entity or relation names or descriptions, replace them with _; do not modify other characters."
    "- Avoid stop words and overly common terms.\n"
    "\n"
    "## Output Format\n"
    "Entities:\n"
    "(entity_name#entity_summary)\n"
    "...\n\n"
    "Relationships:\n"
    "(source_entity_name#relation_name#target_entity_name#relation_summary)\n"
    "...\n"
    "\n"
    "## Reference Example"
    "--The example only helps you understand the input and output format; do not use it in your answer.--\n"
    "Input:\n"
    "```\n"
    "[Context]:\n"
    "Section 1:\n"
    "Phil Jabber's eldest son is named Jacob Jabber.\n"
    "Section 2:\n"
    "Phil Jabber's youngest son is named Bill Jabber.\n"
    "..."
    "\n"
    "[Text]:\n"
    "Philz Coffee was founded by Phil Jabber in 1978 in Berkeley, California. "
    "Known for its distinctive blend coffee, Philz has expanded to multiple locations in the USA. "
    "His eldest son became CEO in 2005 and led the company to significant growth.\n"
    "```\n"
    "\n"
    "Output:\n"
    "```\n"
    "Entities:\n"
    "(Phil Jabber#Founder of Philz Coffee)\n"
    "(Philz Coffee#Coffee brand founded in Berkeley, California)\n"
    "(Jacob Jabber#Phil Jabber's eldest son)\n"
    "(Multiple locations in the USA#Philz Coffee expansion area)\n"
    "\n"
    "Relationships:\n"
    "(Phil Jabber#Founded#Philz Coffee#Founded in Berkeley, California in 1978)\n"
    "(Philz Coffee#Located in#Berkeley, California#Founding location of Philz Coffee)\n"
    "(Phil Jabber#Has#Jacob Jabber#Phil Jabber's eldest son)\n"
    "(Jacob Jabber#Manages#Philz Coffee#Became CEO in 2005)\n"
    "(Philz Coffee#Expanded to#Multiple locations in the USA#Philz Coffee expansion area)\n"
    "```\n"
    "\n"
    "----\n"
    "\n"
    "Based on the [Context] information below, extract the entities and relationships from [Text] per the requirements above.\n"
    "\n"
    "[Context]:\n"
    "{history}\n"
    "\n"
    "[Text]:\n"
    "{text}\n"
    "\n"
    "[Results]:\n"
    "\n"
)

GRAPH_EXTRACT_PT_EN = (
    "## Role\n"
    "You are an expert in Knowledge Graph Engineering, skilled at extracting "
    "entities (subjects, objects) and relations from text, and summarizing "
    "their meanings effectively.\n"
    "\n"
    "## Skills\n"
    "### Skill 1: Entity Extraction\n"
    "--Please follow these steps to extract entities--\n"
    "1. Accurately identify entity information in the text, "
    "usually nouns, pronouns, etc.\n"
    "2. Accurately identify descriptive information, "
    "usually as adjectives, that supplements entity features.\n"
    "3. Merge synonymous, alias, or reference entities into "
    "a single concise entity name, and merge their descriptive information.\n"
    "4. Provide a concise, appropriate, and coherent summary "
    "of the combined entity descriptions.\n"
    "\n"
    "### Skill 2: Relation Extraction\n"
    "--Please follow these steps to extract relations--\n"
    "1. Accurately identify relation information between entities in the text, "
    "usually verbs, pronouns, etc.\n"
    "2. Accurately identify descriptive information, usually as adverbs, "
    "that supplements relation features.\n"
    "3. Merge synonymous, alias, or reference relations into "
    "a single concise relation name, and merge their descriptive information.\n"
    "4. Provide a concise, appropriate, and coherent summary "
    "of the combined relation descriptions.\n"
    "\n"
    "### Skill 3: Contextual Association\n"
    "- Context comes from preceding paragraphs related to the current "
    "extraction text and can provide supplementary information.\n"
    "- Appropriately use contextual information, content references "
    "during extraction may come from this context.\n"
    "- Do not extract knowledge from contextual content, "
    "use it only as a reference.\n"
    "- Context is optional and may be empty.\n"
    "\n"
    "## Constraints\n"
    "- If the text has provided data that is similar to or the same as the "
    "output format, please format the output directly according to the "
    "output format requirements."
    "- Generate as much entity and relation information mentioned in the text "
    "as possible, but do not create nonexistent entities or relations.\n"
    "- Ensure the writing is in the third person, describing entity names, "
    "relation names, and their summaries objectively.\n"
    "- Use as much contextual information as possible to enrich the content "
    "of entities and relations, this is very important.\n"
    "- If a summary of an entity or relation is empty, do not provide "
    "summary information, and do not generate irrelevant descriptions.\n"
    "- If provided descriptions are contradictory, resolve the conflict "
    "and provide a single, coherent description.\n"
    "- Replace any # or : characters in entity's and relation's "
    "names or descriptions with an _ character.\n"
    "- Avoid using stop words and overly common terms.\n"
    "\n"
    "## Output Format\n"
    "Entities:\n"
    "(entity_name#entity_summary)\n"
    "...\n\n"
    "Relationships:\n"
    "(source_entity_name#relation_name#target_entity_name#relation_summary)\n"
    "...\n"
    "\n"
    "## Reference Example\n"
    "--The case is only to help you understand the input and output format of "
    "the prompt, please do not use it in your answer.--\n"
    "Input:\n"
    "```\n"
    "[Context]:\n"
    "Section 1:\n"
    "Phil Jabber's eldest son is named Jacob Jabber.\n"
    "Section 2:\n"
    "Phil Jabber's youngest son is named Bill Jabber.\n"
    "..."
    "\n"
    "[Text]:\n"
    "Philz Coffee was founded by Phil Jabber in 1978 in Berkeley, California. "
    "Known for its distinctive blend coffee, Philz has expanded to multiple "
    "locations in the USA. His eldest son became CEO in 2005, "
    "leading significant growth for the company.\n"
    "```\n"
    "\n"
    "Output:\n"
    "```\n"
    "Entities:\n"
    "(Phil Jabber#Founder of Philz Coffee)\n"
    "(Philz Coffee#Coffee brand founded in Berkeley, California)\n"
    "(Jacob Jabber#Phil Jabber's eldest son)\n"
    "(Multiple locations in the USA#Philz Coffee's expansion area)\n"
    "\n"
    "Relationships:\n"
    "(Phil Jabber#Founded#Philz Coffee"
    "#Founded in 1978 in Berkeley, California)\n"
    "(Philz Coffee#Located in#Berkeley, California"
    "#Philz Coffee's founding location)\n"
    "(Phil Jabber#Has#Jacob Jabber#Phil Jabber's eldest son)\n"
    "(Jacob Jabber#Manage#Philz Coffee#Serve as CEO in 2005)\n"
    "(Philz Coffee#Expanded to#Multiple locations in the USA"
    "#Philz Coffee's expansion area)\n"
    "```\n"
    "\n"
    "----\n"
    "\n"
    "Please extract the entities and relationships data from the [Text] "
    "according to the above requirements, using the provided [Context].\n"
    "\n"
    "[Context]:\n"
    "{history}\n"
    "\n"
    "[Text]:\n"
    "{text}\n"
    "\n"
    "[Results]:\n"
    "\n"
)
