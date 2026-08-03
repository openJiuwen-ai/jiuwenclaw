"""Minimal prompt bank for Demo's tree indexer."""

GROUP_DISCOVERY_PROMPT = """Capability tree planning pass.

Scope note:
{context_section}

Candidate skills ({count} total):
{skills_list}

Return proposed groups only. Do not place skills into groups yet.

Design guidance:
- optimize for retrieval usefulness rather than implementation taxonomy
- make each group a final routing bucket whose member skills can satisfy the same user task
- group skills together only when they are near substitutes; differences may be quality, provider, platform, or modality
- split complementary skills that serve different task intents, workflow stages, or input/output contracts
- avoid broad taxonomy labels when a narrower task-level label is possible
- choose the number of groups from the skill diversity, not from a fixed configured count
- use one group only when all provided skills are genuine substitutes for the same request family
- keep groups distinct enough that a router can tell them apart
- prefer names that remain readable as tree labels
- ids should be lowercase and hyphenated
- description should be a short positive definition
- select_when should state when to route here
- dont_select_when should state the most important boundary

Respond as JSON:
{{
  "groups": {{
    "group-id": {{
      "name": "Short readable label",
      "description": "Short positive definition of what belongs here.",
      "select_when": "Route here when ...",
      "dont_select_when": "Do not route here when ..."
    }}
  }}
}}
"""

SKILL_ASSIGNMENT_PROMPT = """Routing pass for an existing tree layer.

Available groups:
{groups_list}

Skills awaiting placement:
{skills_list}

Rules:
- every skill must appear once
- only use one of the listed group ids
- choose the group where the skill is a near substitute for the other members
- prefer task equivalence over broad taxonomy
- if a skill spans multiple groups, prefer the narrowest group that matches its primary user intent
- avoid putting complementary workflow steps in the same group unless they answer the same user request

Respond as JSON:
{{
  "assignments": {{
    "skill-id-1": "group-id",
    "skill-id-2": "group-id"
  }}
}}
"""

SKILL_PROFILE_PROMPT = """Skill routing profile normalization pass.

Create compact routing profiles for these skills. Use the source description and content to infer capability,
but do not copy long text.

Skills:
{skills_list}

Rules:
- description: one sentence, <= {description_limit} characters, describing what the skill does
- select_when: optional, <= {rule_limit} characters, describing requests where this skill is the right choice
- dont_select_when: optional, <= {rule_limit} characters, describing requests where a nearby skill would be better
- keep wording concrete and retrieval-friendly
- every skill id must appear once

Respond as JSON:
{{
  "profiles": {{
    "skill-id": {{
      "description": "One-sentence capability summary.",
      "select_when": "Use for ...",
      "dont_select_when": "Avoid for ..."
    }}
  }}
}}
"""

NODE_LABEL_REWRITE_PROMPT = """A tree node needs a cleaner label after regrouping.

Current node:
- id: {node_id}
- name: {node_name}
- description: {node_description}

Current children summary:
{children_summary}

Return a replacement routing profile that better summarizes the children now under this node.
Avoid mentioning repair passes or internal mechanics.

Respond as JSON:
{{
  "name": "Updated label",
  "description": "Updated short positive definition",
  "select_when": "Route here when ...",
  "dont_select_when": "Do not route here when ..."
}}
"""

GROUP_MERGE_PROMPT = """Canonicalization pass across several discovery runs.

Candidate group definitions:
{all_groups}

Produce one merged set of canonical groups.
Choose the final count from semantic diversity, not from a fixed configured count.
Merge synonyms where possible, remove duplicate boundaries, and keep labels stable enough for reuse in later indexing runs.

Respond as JSON:
{{
  "canonical_groups": {{
    "canonical-id": {{
      "name": "Canonical label",
      "description": "Short positive definition of what belongs here.",
      "select_when": "Route here when ...",
      "dont_select_when": "Do not route here when ..."
    }}
  }},
  "mapping": {{
    "source-group-id": "canonical-id"
  }}
}}
"""

EQUIVALENCE_GROUPING_PROMPT = """Equivalence regrouping pass for sibling leaves.

Parent:
- id: {parent_id}
- name: {parent_name}
- description: {parent_description}

Leaves:
{leaf_nodes}

Group leaf ids that are near substitutes during retrieval.
Partition all provided leaves into equivalence groups.

Respond as JSON:
{{
  "groups": {{
    "group-id": {{
      "name": "Equivalence label",
      "description": "What these leaves have in common for routing.",
      "select_when": "Route here when ...",
      "dont_select_when": "Do not route here when ...",
      "leaf_ids": ["leaf-a", "leaf-b"]
    }}
  }}
}}
"""
