/* markdown.js — lightweight markdown renderer (no CDN dependency) */

function _escapeHtmlForCode(str) {
    return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function highlightCode(code, lang) {
    // Minimal syntax highlighting - keywords, strings, comments, numbers
    if (!lang) {
        // Auto-detect: look for common patterns
        if (code.includes('def ') || code.includes('import ') || /^\s*class\s/.test(code)) lang = 'python';
        else if (code.includes('function ') || code.includes('const ') || code.includes('=>')) lang = 'javascript';
        else if (code.includes('func ') || code.includes('package ')) lang = 'go';
        else if (code.includes('fn ') || code.includes('let mut')) lang = 'rust';
        else if (code.includes('#include') || code.includes('int main')) lang = 'cpp';
    }

    // Apply highlighting via regex replacements
    // Order matters: strings first, then comments, then keywords, then numbers
    let html = _escapeHtmlForCode(code);

    // Strings (double and single quoted) — but avoid breaking HTML entities
    html = html.replace(/(["'])(?:(?!\1|\\).|\\.)*?\1/g, '<span class="hl-str">$&</span>');

    // Single-line comments
    html = html.replace(/(\/\/.*$|#(?!include).*$)/gm, '<span class="hl-cmt">$&</span>');

    // Numbers (not inside already-highlighted spans)
    html = html.replace(/\b(\d+\.?\d*)\b/g, '<span class="hl-num">$&</span>');

    // Keywords (language-specific)
    const kwMap = {
        python: /\b(def|class|import|from|return|if|elif|else|for|while|try|except|finally|with|as|in|not|and|or|True|False|None|self|async|await|yield|raise|pass|break|continue|lambda|global|nonlocal)\b/g,
        javascript: /\b(function|const|let|var|return|if|else|for|while|do|class|new|this|async|await|import|export|from|default|true|false|null|undefined|try|catch|throw|finally|typeof|instanceof|switch|case|break|continue|of|in)\b/g,
        typescript: /\b(function|const|let|var|return|if|else|for|while|do|class|new|this|async|await|import|export|from|default|true|false|null|undefined|try|catch|throw|finally|typeof|instanceof|switch|case|break|continue|interface|type|enum|implements|extends|public|private|protected|readonly|of|in)\b/g,
        go: /\b(func|package|import|return|if|else|for|range|struct|interface|type|var|const|map|chan|go|defer|select|case|switch|default|nil|true|false|err|make|append|len|cap)\b/g,
        rust: /\b(fn|let|mut|pub|struct|enum|impl|trait|use|mod|return|if|else|for|while|loop|match|self|Self|true|false|None|Some|Ok|Err|async|await|move|unsafe|where|crate|super|dyn|ref|as|in|type)\b/g,
        cpp: /\b(int|char|float|double|void|bool|long|short|unsigned|signed|const|static|struct|class|template|typename|namespace|using|return|if|else|for|while|do|switch|case|break|continue|new|delete|true|false|nullptr|include|define|auto|virtual|override|public|private|protected)\b/g,
    };
    // TypeScript uses same as javascript if not explicitly ts
    if (lang === 'ts') lang = 'typescript';
    if (lang === 'js') lang = 'javascript';
    if (lang === 'py') lang = 'python';
    if (lang === 'rs') lang = 'rust';
    if (lang === 'c' || lang === 'cc' || lang === 'h') lang = 'cpp';
    const kw = kwMap[lang];
    if (kw) html = html.replace(kw, '<span class="hl-kw">$&</span>');

    return html;
}

function mdParse(md) {
    if (!md) return '';
    let html = md;
    // Escape HTML in non-code regions (we'll handle code blocks first)
    const codeBlocks = [];
    // Fenced code blocks — with syntax highlighting
    html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
      const idx = codeBlocks.length;
      const langClass = lang ? ' class="lang-' + _escapeHtmlForCode(lang) + '"' : '';
      codeBlocks.push('<pre><code' + langClass + '>' + highlightCode(code, lang || '') + '</code></pre>');
      return '\x00CODE' + idx + '\x00';
    });
    // Inline code
    html = html.replace(/`([^`]+)`/g, (_, code) => {
      const idx = codeBlocks.length;
      codeBlocks.push('<code>' + code.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</code>');
      return '\x00CODE' + idx + '\x00';
    });
    // Escape remaining HTML
    html = html.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    // Headers
    html = html.replace(/^######\s+(.+)$/gm, '<h6>$1</h6>');
    html = html.replace(/^#####\s+(.+)$/gm, '<h5>$1</h5>');
    html = html.replace(/^####\s+(.+)$/gm, '<h4>$1</h4>');
    html = html.replace(/^###\s+(.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^##\s+(.+)$/gm, '<h2>$1</h2>');
    html = html.replace(/^#\s+(.+)$/gm, '<h1>$1</h1>');
    // Bold / italic
    html = html.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
    html = html.replace(/___(.+?)___/g, '<strong><em>$1</em></strong>');
    html = html.replace(/__(.+?)__/g, '<strong>$1</strong>');
    html = html.replace(/_(.+?)_/g, '<em>$1</em>');
    // Blockquotes
    html = html.replace(/^&gt;\s?(.+)$/gm, '<blockquote>$1</blockquote>');
    // Horizontal rule
    html = html.replace(/^[-*_]{3,}$/gm, '<hr>');
    // Lists — collect consecutive lines
    html = html.replace(/((?:^[-*+]\s+.+\n?)+)/gm, (block) => {
      const items = block.trim().split('\n').map(l => '<li>' + l.replace(/^[-*+]\s+/, '') + '</li>').join('');
      return '<ul>' + items + '</ul>\n';
    });
    html = html.replace(/((?:^\d+\.\s+.+\n?)+)/gm, (block) => {
      const items = block.trim().split('\n').map(l => '<li>' + l.replace(/^\d+\.\s+/, '') + '</li>').join('');
      return '<ol>' + items + '</ol>\n';
    });
    // Tables — | col | col | rows with a separator row of |---|---|
    html = html.replace(/((?:^\|.+\|\n?)+)/gm, (block) => {
      const rows = block.trim().split('\n').filter(r => r.trim());
      if (rows.length < 2) return block;
      const sepIdx = rows.findIndex(r => /^\|[\s\-|:]+\|$/.test(r.trim()));
      if (sepIdx < 0) return block;
      const headerRows = rows.slice(0, sepIdx);
      const bodyRows = rows.slice(sepIdx + 1);
      const parseRow = (r, tag) => '<tr>' + r.replace(/^\||\|$/g,'').split('|').map(c => `<${tag}>${c.trim()}</${tag}>`).join('') + '</tr>';
      const thead = '<thead>' + headerRows.map(r => parseRow(r,'th')).join('') + '</thead>';
      const tbody = bodyRows.length ? '<tbody>' + bodyRows.map(r => parseRow(r,'td')).join('') + '</tbody>' : '';
      return '<table>' + thead + tbody + '</table>\n';
    });
    // Links
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');
    // Paragraphs — wrap double-newline separated blocks
    html = html.split(/\n{2,}/).map(para => {
      para = para.trim();
      if (!para) return '';
      if (/^<(h[1-6]|ul|ol|li|blockquote|hr|pre|table)/.test(para)) return para;
      if (para.includes('\x00CODE')) return para;
      return '<p>' + para.replace(/\n/g, '<br>') + '</p>';
    }).join('\n');
    // Restore code blocks
    codeBlocks.forEach((block, idx) => {
      html = html.replace('\x00CODE' + idx + '\x00', block);
    });
    return html;
}
