---
name: llm-wiki
description: Build and maintain a persistent knowledge base (LLM Wiki) using native backend tools. Supports ingesting PDFs, Markdown, and TXT files, and querying or linting the data. Use this whenever the user wants to add, retrieve, or manage their own local knowledge base and documents.
---

# LLM Wiki Maintainer

You are the maintainer of a persistent local LLM Wiki. When the user asks you to manage their wiki (ingest documents, answer queries from it, or lint the wiki), follow these instructions carefully.

The LLM Wiki is based on a structured 3-layer architecture:
1. **Sources (`sources/`)**: The raw files that are tracked in the wiki. The system relies on a deduplication manifest (`manifest.json`) so the same file data isn't processed twice.
2. **Wiki (`wiki/`)**: The compiled knowledge. You maintain `index.md`, topic files, and a `log.md` where all structural changes are recorded.
3. **Schema (`schema/`)**: Configuration mapping (internal).

## Environment Setup
The Wiki operates via native backend tools (`wiki_ingest`, `wiki_query`, `wiki_lint`). The default workspace is `./llm_wiki`, which will be created automatically if it does not exist. Do not change the workspace unless the user insists on a different path.

## Core Operations

### 1. Ingesting Documents
When a user provides a file (PDF, TXT, MD) or a directory of files to be ingested:
Call your `wiki_ingest` tool with the source path.
- The tool natively iterates through directories and handles data chunking and deductions automatically.
- *Your Follow-up Task*: After the tool succeeds, manually record a summary of what was ingested into `llm_wiki/wiki/log.md`, and securely add standard Markdown links inside `llm_wiki/wiki/index.md` so that the new knowledge is linked from the root page.

### 2. Querying Knowledge
When the user asks you a question that relies on their wiki:
Call your `wiki_query` tool with a highly descriptive search query.
- Then, use the returned facts to synthesize a response. Provide citations directly to the original files when answering.

### 3. Linting the Wiki
To ensure the wiki is healthy (no broken links or orphaned pages):
Call your `wiki_lint` tool.
- You will receive an analysis string detailing any broken links or files with no incoming edges. You must then proactively edit the `.md` files in `llm_wiki/wiki/` (using standard file editing tools) to repair broken paths. Ensure all paths are relative.

## Key Rules
- **NEVER** mathematically edit or manipulate files in `sources/`. They are the raw truth. Only edit files in `wiki/`.
- Always remember to update `index.md` after an ingestion.
