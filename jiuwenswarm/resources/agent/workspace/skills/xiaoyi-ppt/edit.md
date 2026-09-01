# Editing Existing Presentations

## Template-Based Workflow

When using an existing presentation as a template:

1. **Copy and analyze**:
```bash
   cp /path/to/user-provided.pptx /tmp/template.pptx
   python {baseDir}/scripts/thumbnail.py /tmp/template.pptx /tmp/thumbnails
   python -m markitdown /tmp/template.pptx > /tmp/template.md
```
   - Review `/tmp/thumbnails.jpg` to understand layouts and visual structure (requires multimodal capability). If the model lacks vision capability, use a layout description script or image-understanding skill instead.
   - Review `/tmp/template.md` to see placeholder text and slide structure.

2. **Plan slide mapping**: For each content section, choose a template slide.

   **USE VARIED LAYOUTS** — monotonous presentations are a common failure mode. Don't default to basic title + bullet slides. Actively seek out:
   - Multi-column layouts (2-column, 3-column)
   - Image + text combinations
   - Full-bleed images with text overlay
   - Quote or callout slides
   - Section dividers
   - Stat/number callouts
   - Icon grids or icon + text rows

   **Avoid:** Repeating the same text-heavy layout for every slide.

   Match content type to layout style (e.g., key points -> bullet slide, team info -> multi-column, testimonials -> quote slide).

3. **Unpack**: Extract the PPTX into an editable XML tree using Python's `zipfile` module. Pretty-print the XML for readability.

4. **Build presentation** (do this yourself, not with subagents):
   - Delete unwanted slides (remove from `<p:sldIdLst>`)
   - Duplicate slides you want to reuse (copy slide XML, relationships, and update `Content_Types.xml` and `presentation.xml`)
   - Reorder slides in `<p:sldIdLst>`
   - **Complete all structural changes before step 5**

5. **Edit content**: Update text in each `slide{N}.xml`.
   **Use subagents here if available** — slides are separate XML files, so subagents can edit in parallel.

6. **Clean**: Remove orphaned files — slides not in `<p:sldIdLst>`, unreferenced media, orphaned rels.

7. **Pack**: Repack the XML tree into a PPTX file. Validate, repair, condense XML, re-encode smart quotes.

   Always write to `/tmp/` first, then copy to the final path. Python's `zipfile` module uses `seek` internally, which fails on some volume mounts (e.g. Docker bind mounts). Writing to a local temp path avoids this.

## Output Structure

Copy the user-provided file to `template.pptx` in cwd. This preserves the original and gives a predictable name for all downstream operations.

```bash
cp /path/to/user-provided.pptx template.pptx
```

```text
./
├── template.pptx               # Copy of user-provided file (never modified)
├── template.md                 # markitdown extraction
├── unpacked/                   # Editable XML tree
└── edited.pptx                 # Final repacked deck
```

Minimum expected deliverable: `edited.pptx`.

## Slide Operations

Slide order is in `ppt/presentation.xml` -> `<p:sldIdLst>`.

**Reorder**: Rearrange `<p:sldId>` elements.

**Delete** — all 4 steps, atomically:

```
1. Remove <p:sldId> from presentation.xml → <p:sldIdLst>
2. Remove <Relationship> from ppt/_rels/presentation.xml.rels
3. ⚠️ Delete physical files: ppt/slides/slideN.xml AND ppt/slides/_rels/slideN.xml.rels
4. Remove <Override> from [Content_Types].xml
```

> Step 3 is the most commonly missed. Leaving the file on disk creates a **zombie** that gets repacked and corrupts the output.

**Add** — all 5 steps, atomically:

```
1. Write ppt/slides/slideN.xml
2. Write ppt/slides/_rels/slideN.xml.rels
3. Add <Relationship> to ppt/_rels/presentation.xml.rels (new rId)
4. ⚠️ Add <p:sldId id="..." r:id="..."/> to presentation.xml <p:sldIdLst>
5. Add <Override> to [Content_Types].xml
```

> Missing step 3 = broken reference. Missing step 4 = slide on disk but invisible. Both are required.

**ID 计算规则**：`<p:sldId>` 有两个必填属性，都要正确计算：
- `r:id`：在 `ppt/_rels/presentation.xml.rels` 中找最大 rId 数字 +1（如现有 rId14 → 新建 rId15）
- `id`：在 `presentation.xml` 的 `<p:sldId>` 中找最大 id 数字 +1（通常从 256 起步）。**不要省略此属性**，也不要设为空字符串。

Never manually copy slide files without updating all references — this causes broken notes references and missing relationship IDs.

---

## Cross-Presentation Slide Import

Copying a slide from PPT-A into PPT-B is error-prone because slides reference layouts, notes, and media that differ between files.

**Three things that break:**

| Problem | Cause | Fix |
|---------|-------|-----|
| Corrupted rendering | Slide rels points to source's `slideLayout1.xml` (different master/theme) | **Remap** to target PPT's layout |
| Broken reference | Slide rels points to `notesSlideN.xml` that doesn't exist in target | **Strip** all notesSlide relationships |
| Missing images | Slide references `../media/imageN.png` not in target | **Copy** media files from source |

**Procedure** (after unpacking target PPT):

1. Read source slide XML + rels from source PPTX via `zipfile`
2. **Remap** the `slideLayout` target in rels to a target PPT layout：读取目标 PPT 中任意一张内容页（非封面/章节页）的 `slideN.xml.rels`，取其 `slideLayout` 引用路径，用该路径替换源 slide rels 中的 layout 引用。若无法判断，用 `../slideLayouts/slideLayout1.xml` 作为兜底。
3. **Strip** any `<Relationship>` with Type containing `notesSlide`
4. **Copy** any referenced `../media/*` files from source into target's `ppt/media/`。若目标已有同名文件且内容不同，重命名为 `imageN_imported.png` 并同步更新 rels 中的 Target。
5. Write slide XML + modified rels into target's `ppt/slides/`
6. Complete the standard **Add** checklist above (steps 3–5: presentation.xml.rels 加 Relationship、presentation.xml 加 sldId、Content_Types 加 Override)

> **When to skip cross-PPT import**: If the source slide uses charts, SmartArt, or custom themes, prefer extracting text with `markitdown` and recreating the slide natively. Cross-PPT import works reliably only for basic shapes/text/solid fills.

---

## Editing Content

**Subagents:** If available, use them here (after completing step 4). Each slide is a separate XML file, so subagents can edit in parallel. In your prompt to subagents, include:
- The slide file path(s) to edit
- **"Use the Edit tool for all changes"**
- The formatting rules and common pitfalls below

For each slide:
1. Read the slide's XML
2. Identify ALL placeholder content — text, images, charts, icons, captions
3. Replace each placeholder with final content

**Use the Edit tool, not sed or Python scripts.** The Edit tool forces specificity about what to replace and where, yielding better reliability.

## Formatting Rules

- **Bold all headers, subheadings, and inline labels**: Use `b="1"` on `<a:rPr>`. This includes:
  - Slide titles
  - Section headers within a slide
  - Inline labels like (e.g.: "Status:", "Description:") at the start of a line
- **Never use unicode bullets**: Use proper list formatting with `<a:buChar>` or `<a:buAutoNum>`
- **Bullet consistency**: Let bullets inherit from the layout. Only specify `<a:buChar>` or `<a:buNone>`.

## Common Pitfalls — Template Editing

### Template Adaptation

When source content has fewer items than the template:
- **Remove excess elements entirely** (images, shapes, text boxes), don't just clear text
- Check for orphaned visuals after clearing text content
- Run content QA with `markitdown` to catch mismatched counts

When replacing text with different length content:
- **Shorter replacements**: Usually safe
- **Longer replacements**: May overflow or wrap unexpectedly
- Verify with `markitdown` after text changes
- Consider truncating or splitting content to fit the template's design constraints

**Template slots != Source items**: If template has 4 team members but source has 3 users, delete the 4th member's entire group (image + text boxes), not just the text.

### Multi-Item Content

If source has multiple items (numbered lists, multiple sections), create separate `<a:p>` elements for each — **never concatenate into one string**.

**WRONG** — all items in one paragraph:
```xml
<a:p>
  <a:r><a:rPr .../><a:t>Step 1: Do the first thing. Step 2: Do the second thing.</a:t></a:r>
</a:p>
```

**CORRECT** — separate paragraphs with bold headers:
```xml
<a:p>
  <a:pPr algn="l"><a:lnSpc><a:spcPts val="3919"/></a:lnSpc></a:pPr>
  <a:r><a:rPr lang="en-US" sz="2799" b="1" .../><a:t>Step 1</a:t></a:r>
</a:p>
<a:p>
  <a:pPr algn="l"><a:lnSpc><a:spcPts val="3919"/></a:lnSpc></a:pPr>
  <a:r><a:rPr lang="en-US" sz="2799" .../><a:t>Do the first thing.</a:t></a:r>
</a:p>
<a:p>
  <a:pPr algn="l"><a:lnSpc><a:spcPts val="3919"/></a:lnSpc></a:pPr>
  <a:r><a:rPr lang="en-US" sz="2799" b="1" .../><a:t>Step 2</a:t></a:r>
</a:p>
<!-- continue pattern -->
```

Copy `<a:pPr>` from the original paragraph to preserve line spacing. Use `b="1"` on headers.

### Smart Quotes

The Edit tool converts smart quotes to ASCII. **When adding new text with quotes, use XML entities:**

```xml
<a:t>the &#x201C;Agreement&#x201D;</a:t>
```

| Character | Name | Unicode | XML Entity |
|-----------|------|---------|------------|
| \u201c | Left double quote | U+201C | `&#x201C;` |
| \u201d | Right double quote | U+201D | `&#x201D;` |
| \u2018 | Left single quote | U+2018 | `&#x2018;` |
| \u2019 | Right single quote | U+2019 | `&#x2019;` |

### Other

- **Whitespace**: Use `xml:space="preserve"` on `<a:t>` with leading/trailing spaces
- **XML parsing**: Use `defusedxml.minidom`, not `xml.etree.ElementTree` (corrupts namespaces)