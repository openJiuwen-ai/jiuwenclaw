export interface LatexPreviewOptions {
  resolveImage?: (path: string) => string;
}

function stripComments(latex: string): string {
  return latex.replace(/(?<!\\)%[^\n\r]*/g, '');
}

function readCommand(source: string, command: string): string {
  const marker = `\\${command}{`;
  const start = source.indexOf(marker);
  if (start < 0) return '';

  let index = start + marker.length;
  let depth = 1;
  let value = '';
  while (index < source.length && depth > 0) {
    const char = source[index];
    if (char === '{') depth += 1;
    else if (char === '}') depth -= 1;
    if (depth > 0) value += char;
    index += 1;
  }
  return value;
}

function lastCommandValue(source: string, command: string): string {
  const matches = source.match(new RegExp(`\\\\${command}(?:\\[[^\\]]*\\])?\\{([^{}]*)\\}`, 'g')) ?? [];
  if (matches.length === 0) return '';
  const match = matches[matches.length - 1].match(/\{([^{}]*)\}$/);
  return match?.[1] ?? '';
}

function convertInline(value: string): string {
  const mathSegments: string[] = [];
  const protectedValue = value.replace(/\$\$[\s\S]*?\$\$|\$[^$]*\$/g, (match) => {
    mathSegments.push(match);
    return `%%%MATH${mathSegments.length - 1}%%%`;
  });

  const converted = protectedValue
    .replace(/\\textbf\{([^{}]*)\}/g, '**$1**')
    .replace(/\\textit\{([^{}]*)\}/g, '*$1*')
    .replace(/\\emph\{([^{}]*)\}/g, '*$1*')
    .replace(/\\texttt\{([^{}]*)\}/g, '`$1`')
    .replace(/\\underline\{([^{}]*)\}/g, '<u>$1</u>')
    .replace(/\\href\{([^{}]*)\}\{([^{}]*)\}/g, '[$2]($1)')
    .replace(/\\url\{([^{}]*)\}/g, '[$1]($1)')
    .replace(/\\footnote\{([^{}]*)\}/g, ' ($1)')
    .replace(/\\cite[^\s{]*(?:\[[^\]]*\])?\{([^{}]*)\}/g, '[$1]')
    .replace(/\\(?:ref|eqref|autoref|pageref)\{([^{}]*)\}/g, '[$1]')
    .replace(/\\label\{([^{}]*)\}/g, '')
    .replace(/\\cmark/g, '✓')
    .replace(/\\xmark/g, '✗')
    .replace(/\\%/g, '%')
    .replace(/\\&/g, '&')
    .replace(/\\_/g, '_')
    .replace(/\\#/g, '#')
    .replace(/\\\$/g, '$')
    .replace(/\\\\/g, '<br>')
    .replace(/~/g, ' ')
    .replace(/\\(?:centering|raggedright|raggedleft|small|footnotesize|scriptsize|large|Large|LARGE|huge|Huge|normalfont|bfseries|mdseries|itshape|upshape|rmfamily|sffamily|ttfamily)/g, '')
    .replace(/\\(?:[A-Za-z]+)\*?(?:\[[^\]]*\])?(?:\{[^{}]*\})?/g, ' ')
    .replace(/[ \t]+/g, ' ')
    .trim();

  return converted.replace(/%%%MATH(\d+)%%%/g, (_match, index: string) => mathSegments[Number(index)] ?? '');
}

function convertTabular(content: string): string {
  const body = content
    .replace(/\\(?:toprule|midrule|bottomrule|hline)/g, '')
    .replace(/\\multicolumn\{[^{}]*\}\{[^{}]*\}\{([^{}]*)\}/g, '$1')
    .replace(/\\multirow(?:\[[^\]]*\])?\{[^{}]*\}\{[^{}]*\}\{([^{}]*)\}/g, '$1')
    .replace(/\\begin\{tabular\}(?:\[[^\]]*\])?\{[^{}]*\}/g, '')
    .replace(/\\end\{tabular\}/g, '')
    .trim();

  const rows = body
    .split(/\\\\(?:\s*\[[^\]]*\])?/)
    .map((row) => row.trim())
    .filter(Boolean);
  if (rows.length === 0) return '';

  const tableRows = rows.map((row) => {
    const cells = row
      .split(/&(?!amp;)/)
      .map((cell) => convertInline(cell))
      .map((cell) => cell.replace(/\|/g, '\\|'));
    return `| ${cells.join(' | ')} |`;
  });

  const columnCount = Math.max(...tableRows.map((row) => row.split('|').length - 2));
  const separator = `| ${Array.from({ length: columnCount }, () => '---').join(' | ')} |`;
  return [tableRows[0], separator, ...tableRows.slice(1)].join('\n');
}

function convertFigure(content: string, options: LatexPreviewOptions): string {
  const caption = convertInline(readCommand(content, 'caption'));
  const imagePath = lastCommandValue(content, 'includegraphics');
  const resolvedImage = imagePath ? options.resolveImage?.(imagePath) : undefined;
  const isImage = /\.(?:png|jpe?g|gif|webp|svg)$/i.test(imagePath);
  if (resolvedImage && isImage) {
    return `![${caption}](${resolvedImage})`;
  }
  return caption ? `**Figure: ${caption}**` : '**Figure**';
}

export function latexToMarkdown(latex: string, options: LatexPreviewOptions = {}): string {
  const withoutComments = stripComments(latex);
  const documentMatch = withoutComments.match(/\\begin\{document\}([\s\S]*?)\\end\{document\}/i);
  const bodySource = documentMatch?.[1] ?? withoutComments;
  const title = readCommand(withoutComments, 'title');
  const author = readCommand(withoutComments, 'author');

  let body = bodySource
    .replace(/\\maketitle|\\newpage|\\clearpage|\\bibliography\{[^{}]*\}|\\printbibliography|\\balance/g, '')
    .replace(/\\begin\{abstract\}([\s\S]*?)\\end\{abstract\}/gi, (_match, abstract: string) => `**Abstract**\n\n${abstract.trim()}`)
    .replace(/\\section\*?\{([^{}]*)\}/g, (_match, title: string) => `## ${convertInline(title)}`)
    .replace(/\\subsection\*?\{([^{}]*)\}/g, (_match, title: string) => `### ${convertInline(title)}`)
    .replace(/\\subsubsection\*?\{([^{}]*)\}/g, (_match, title: string) => `#### ${convertInline(title)}`)
    .replace(/\\begin\{(?:figure|figure\*)\}([\s\S]*?)\\end\{(?:figure|figure\*)\}/g, (_match, figure: string) => convertFigure(figure, options))
    .replace(/\\begin\{(?:table|table\*)\}([\s\S]*?)\\end\{(?:table|table\*)\}/g, (_match, table: string) => {
      const caption = convertInline(readCommand(table, 'caption'));
      const tabular = convertTabular(table);
      return [tabular, caption ? `*${caption}*` : ''].filter(Boolean).join('\n\n');
    })
    .replace(/\\begin\{(?:equation|align|aligned|gather|multline|eqnarray)\*?\}([\s\S]*?)\\end\{(?:equation|align|aligned|gather|multline|eqnarray)\*?\}/g, (_match, equation: string) => `$$${equation.trim()}$$`)
    .replace(/\\\[([\s\S]*?)\\\]/g, (_match, equation: string) => `$$${equation.trim()}$$`)
    .replace(/\\\(([\s\S]*?)\\\)/g, (_match, equation: string) => `$${equation.trim()}$`)
    .replace(/\\begin\{(?:itemize|enumerate|description)\*?\}([\s\S]*?)\\end\{(?:itemize|enumerate|description)\*?\}/g, (_match, list: string) => {
      return list
        .split(/\\item(?![A-Za-z])/)
        .map((item) => item.trim())
        .filter(Boolean)
        .map((item) => {
          const description = item.match(/^\[([^\]]*)\]\s*/);
          const content = description ? item.slice(description[0].length) : item;
          return `- ${description ? `**${convertInline(description[1])}** ` : ''}${convertInline(content)}`;
        })
        .join('\n');
    });

  const header = [
    title ? `# ${convertInline(title)}` : '',
    author ? convertInline(author) : '',
  ].filter(Boolean).join('\n\n');

  const paragraphs = body
    .split(/\n{2,}/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean)
    .map((paragraph) => convertInline(paragraph))
    .filter(Boolean);

  return [header, ...paragraphs].filter(Boolean).join('\n\n');
}
