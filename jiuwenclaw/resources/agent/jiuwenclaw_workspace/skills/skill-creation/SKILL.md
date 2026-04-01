---
name: skill-creation
description: Guide to skill creation. Use this skill when the user requests to make, create, or write a new skill.
---

# Skill Creation

This document explains how to design and build effective skills for agents.

## What is a Skill

Skills are a manual of information that gives an agent focused capabilities in a specific domain. It includes instructions and reusable resources.

## Skill Design

### Keep Everything Concise

The context window is a limited space. Write only what an agent truly needs.

### Structure

Every skill directory contains:

```
skill-name/
├── SKILL.md (required)
├── scripts/ (optional, for executable code, e.g. Python, Bash, etc.)
├── references/ (optional, documentation or schemas loaded only when needed to keep SKILL.md short)
└── assets/ (optional)
```

Do NOT create unrelated documentation on the creation of this skill. **DO NOT** create any README, changelog, installation guide, creation summary etc. files. The skill-name directory should contain only execution-relevant content.

#### Skill.md

##### Frontmatter (YAML)

* Must include `name` and `description`.
* The description controls when the skill is triggered. Clearly state its purpose and include trigger words.

##### Body (Markdown)

* Practical instructions for using the skill and its resources
* Loaded only after the skill is triggered

#### Optional Resource Types

* scripts/ - Executable code (e.g. Python, Bash, etc.)
* references/ - Documentation or schemas loaded only when needed to keep SKILL.md short.
* assets/ - Other files to use as is (e.g. templates, icons, fonts, etc.)

### Progressive Disclosure

Skills minimize context usage through three layers of progressive disclosure:

1. **Metadata** - `name` and `description` of the skill is always in context (~100 words)
2. **SKILL.md** - loaded only when triggered
3. **Other referenced documents** - accessed only when required.

Keep SKILL.md focused and short, move detailed explanations or lengthy codes/examples into other files.
Always reference other files inside SKILL.md so the agent knows where they are.

## Skill Output Format

Create the skill folder **inside the workspace directly**, instead of the skill folder. JiuwenClaw needs to import the skill before it can be used, just creating the skill inside the folder won't work! In addition, provide the **file-path** to the newly created skill so the user can easily import the skill.
