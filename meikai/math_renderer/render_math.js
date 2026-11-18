/**
 * Meikai Math Renderer Engine
 * Converts mathematical LaTeX expressions and mixed Markdown problem statements
 * into standalone crisp vector SVG and formatted HTML via MathJax-full.
 * Runs as a persistent background daemon communicating over stdin/stdout JSON-RPC.
 */

const readline = require('readline');
const { mathjax } = require('mathjax-full/js/mathjax.js');
const { TeX } = require('mathjax-full/js/input/tex.js');
const { SVG } = require('mathjax-full/js/output/svg.js');
const { liteAdaptor } = require('mathjax-full/js/adaptors/liteAdaptor.js');
const { RegisterHTMLHandler } = require('mathjax-full/js/handlers/html.js');
const { AllPackages } = require('mathjax-full/js/input/tex/AllPackages.js');

const adaptor = liteAdaptor();
RegisterHTMLHandler(adaptor);

// Initialize MathJax TeX input with all standard LaTeX packages
const texInput = new TeX({
    packages: AllPackages,
    inlineMath: [['$', '$'], ['\\(', '\\)']],
    displayMath: [['$$', '$$'], ['\\[', '\\]']],
    processEscapes: true
});

const svgOutput = new SVG({
    fontCache: 'local',
    scale: 1.15
});

const htmlDoc = mathjax.document('', {
    InputJax: texInput,
    OutputJax: svgOutput
});

function texToSvgData(tex, color = '#E2E8F0', display = false, fontSize = 16) {
    if (!tex || !tex.trim()) return null;
    try {
        const node = htmlDoc.convert(tex.trim(), { display: display });
        let svg = adaptor.innerHTML(node);
        if (color) {
            svg = svg.replace('<svg ', `<svg fill="${color}" color="${color}" `);
        }

        // Calculate exact proportional pixel dimensions from MathJax viewBox
        // MathJax uses 1000 units = 1em.
        // Scale proportionally to fontSize so math dynamically fills available space.
        const vbMatch = svg.match(/viewBox="([^"]+)"/);
        const emScale = display ? (fontSize * 1.35) : (fontSize * 1.05);
        let widthPx = display ? Math.round(fontSize * 8) : Math.round(fontSize * 1.5);
        let heightPx = display ? Math.round(fontSize * 2.2) : Math.round(fontSize * 1.1);

        if (vbMatch) {
            const parts = vbMatch[1].trim().split(/\s+/);
            if (parts.length === 4) {
                const vbW = parseFloat(parts[2]);
                const vbH = parseFloat(parts[3]);
                if (vbH > 0 && vbW > 0) {
                    if (display) {
                        widthPx = Math.max(16, Math.round(vbW * emScale / 1000));
                        heightPx = Math.max(20, Math.round(vbH * emScale / 1000));
                    } else {
                        widthPx = Math.max(8, Math.round(vbW * emScale / 1000));
                        heightPx = Math.max(12, Math.round(vbH * emScale / 1000));
                    }
                }
            }
        }

        // Replace MathJax's 'ex' units inside the SVG root element with explicit pixel units
        // Qt's QSvgRenderer does not parse 'ex' units and falls back to raw viewBox coordinates without this!
        svg = svg.replace(/width="[^"]*"/, `width="${widthPx}px"`);
        svg = svg.replace(/height="[^"]*"/, `height="${heightPx}px"`);

        const b64 = Buffer.from(svg).toString('base64');
        return {
            b64: b64,
            width: widthPx,
            height: heightPx
        };
    } catch (err) {
        return null;
    }
}

function renderContent(input, color = '#E2E8F0', fontSize = 16, paddingTop = 16) {
    if (!input || !input.trim()) {
        return { html: '<p style="color:#8B949E; text-align:center; padding-top:40px;">No problem text to render</p>', error: null };
    }

    let text = input.trim();
    // Strip markdown code fences if present
    if (text.startsWith('```latex') || text.startsWith('```tex') || text.startsWith('```markdown')) {
        text = text.replace(/^```[a-z]*\s*/, '').replace(/```\s*$/, '').trim();
    } else if (text.startsWith('```')) {
        text = text.replace(/^```\s*/, '').replace(/```\s*$/, '').trim();
    }

    // Auto-normalize LaTeX if written in raw math mode with \text{...} or \\ without $
    if ((text.includes('\\text{') || text.includes('\\\\')) && (text.match(/\$/g) || []).length < 2) {
        const parts = text.split(/\s*\\\\(?:\s*\[[^\]]*\])?\s*/);
        const normLines = parts.map(line => {
            let l = line.trim();
            if (!l) return '';
            // Handle options row (A) ... (B) ...
            if (/\(A\)/i.test(l) && /\(B\)/i.test(l)) {
                l = l.replace(/\\(?:hspace\{[^}]*\}|quad|qquad)/g, ' ');
                l = l.replace(/(?:\\text\{\s*)?(\([A-D]\))(?:\s*\})?\s*([^(]+?)(?=(?:\\text\{\s*)?\([A-D]\)|$)/gi, (m, letter, val) => {
                    let v = val.replace(/\\text\{([^}]+)\}/g, '$1').trim();
                    if (v && !v.startsWith('$')) v = `$${v}$`;
                    return `${letter} ${v} \\quad\\quad `;
                });
                return l.trim();
            }
            if (!l.includes('\\text{')) {
                return (l.startsWith('$$') || l.startsWith('\\[')) ? l : `$$${l}$$`;
            }
            let pieces = [];
            let last = 0;
            const re = /\\text\{([^}]*)\}/g;
            let m;
            while ((m = re.exec(l)) !== null) {
                let mathBefore = l.slice(last, m.index).trim();
                if (mathBefore) {
                    const qm = mathBefore.match(/^(Q\.?\s*\d+[:.]?)\s*(.*)/);
                    if (qm) {
                        pieces.push(qm[1] + (qm[2] ? ` $${qm[2].trim()}$` : ''));
                    } else {
                        pieces.push(`$${mathBefore}$`);
                    }
                }
                pieces.push(m[1]);
                last = re.lastIndex;
            }
            let mathAfter = l.slice(last).trim();
            if (mathAfter) {
                pieces.push(`$${mathAfter}$`);
            }
            return pieces.join(' ').replace(/\s+/g, ' ').trim();
        });
        text = normLines.filter(Boolean).join('\n\n');
    }

    // Auto-delimit and promote standalone formulas to display math
    const rawLines = text.split('\n');
    const processedLines = rawLines.map(line => {
        const trimmed = line.trim();
        if (!trimmed) return line;
        if (trimmed.startsWith('$$') || trimmed.startsWith('\\[') || trimmed.startsWith('\\begin{')) return line;
        // Standalone inline math line: $...$ -> $$...$$ for display scaling
        if (/^\$[^$\n]+\$$/.test(trimmed)) {
            return '$$' + trimmed.slice(1, -1) + '$$';
        }
        // Bare math formula line without delimiters
        if (!trimmed.includes('$') && !trimmed.includes('\\(') && !trimmed.includes('\\[')) {
            const isTextPrompt = /^(Q\.|Question|Problem|Example|Show that|Find|Prove|Where|Let)\b/i.test(trimmed);
            const hasMath = /[\\=_^]/.test(trimmed) && (trimmed.includes('\\') || trimmed.includes('='));
            if (hasMath && !isTextPrompt) {
                return '$$' + trimmed + '$$';
            }
        }
        return line;
    });
    text = processedLines.join('\n');

    try {
        const blockMargin = Math.max(10, Math.round(fontSize * 0.7));

        // 1. Process block equations $$...$$ or \[...\]
        let processed = text.replace(/\$\$([\s\S]*?)\$\$/g, (m, tex) => {
            const res = texToSvgData(tex, color, true, fontSize);
            if (!res) return `<div align="center" style="color:#F85149;">[Invalid Formula]</div>`;
            return `<div align="center" style="margin: ${blockMargin}px 0;"><img width="${res.width}" height="${res.height}" style="max-width: 100%; height: auto;" src="data:image/svg+xml;base64,${res.b64}"/></div>`;
        });
        processed = processed.replace(/\\\[([\s\S]*?)\\\]/g, (m, tex) => {
            const res = texToSvgData(tex, color, true, fontSize);
            if (!res) return `<div align="center" style="color:#F85149;">[Invalid Formula]</div>`;
            return `<div align="center" style="margin: ${blockMargin}px 0;"><img width="${res.width}" height="${res.height}" style="max-width: 100%; height: auto;" src="data:image/svg+xml;base64,${res.b64}"/></div>`;
        });

        // 2. Process inline equations $...$ or \(...\)
        processed = processed.replace(/\$([^$\n]+?)\$/g, (m, tex) => {
            const res = texToSvgData(tex, color, false, fontSize);
            if (!res) return `<span style="color:#F85149;">[Formula]</span>`;
            return `<img width="${res.width}" height="${res.height}" style="vertical-align: middle; margin: 0 3px;" src="data:image/svg+xml;base64,${res.b64}"/>`;
        });
        processed = processed.replace(/\\\(([\s\S]*?)\\\)/g, (m, tex) => {
            const res = texToSvgData(tex, color, false, fontSize);
            if (!res) return `<span style="color:#F85149;">[Formula]</span>`;
            return `<img width="${res.width}" height="${res.height}" style="vertical-align: middle; margin: 0 3px;" src="data:image/svg+xml;base64,${res.b64}"/>`;
        });

        // 3. Format lines, MCQ option grids, and LaTeX text macros
        function formatTextLine(line) {
            let s = line.trim();
            if (!s) return '';

            // Clean LaTeX spacing commands: \quad, \qquad, \hspace{...}
            const cleanSpacers = (str) => str.replace(/\\(?:quad|qquad|hspace\{[^}]*\}|\s+)+/g, ' ').trim();

            // Check for 4-option row: (A) ... (B) ... (C) ... (D) ...
            const opt4 = s.match(/^\s*\(A\)\s*([\s\S]+?)\s*\(B\)\s*([\s\S]+?)\s*\(C\)\s*([\s\S]+?)\s*\(D\)\s*([\s\S]+?)$/i);
            if (opt4) {
                return `<table width="100%" style="margin: 12px 0; border: none; border-collapse: collapse;"><tr>` +
                    `<td width="25%" align="left" style="padding: 4px 6px; vertical-align: middle;"><b>(A)</b> ${cleanSpacers(opt4[1])}</td>` +
                    `<td width="25%" align="left" style="padding: 4px 6px; vertical-align: middle;"><b>(B)</b> ${cleanSpacers(opt4[2])}</td>` +
                    `<td width="25%" align="left" style="padding: 4px 6px; vertical-align: middle;"><b>(C)</b> ${cleanSpacers(opt4[3])}</td>` +
                    `<td width="25%" align="left" style="padding: 4px 6px; vertical-align: middle;"><b>(D)</b> ${cleanSpacers(opt4[4])}</td>` +
                    `</tr></table>`;
            }

            // Check for 2-option rows: (A)...(B)... or (C)...(D)...
            const optAB = s.match(/^\s*\(A\)\s*([\s\S]+?)\s*\(B\)\s*([\s\S]+?)$/i);
            if (optAB) {
                return `<table width="100%" style="margin: 8px 0; border: none; border-collapse: collapse;"><tr>` +
                    `<td width="50%" align="left" style="padding: 4px 6px; vertical-align: middle;"><b>(A)</b> ${cleanSpacers(optAB[1])}</td>` +
                    `<td width="50%" align="left" style="padding: 4px 6px; vertical-align: middle;"><b>(B)</b> ${cleanSpacers(optAB[2])}</td>` +
                    `</tr></table>`;
            }
            const optCD = s.match(/^\s*\(C\)\s*([\s\S]+?)\s*\(D\)\s*([\s\S]+?)$/i);
            if (optCD) {
                return `<table width="100%" style="margin: 8px 0; border: none; border-collapse: collapse;"><tr>` +
                    `<td width="50%" align="left" style="padding: 4px 6px; vertical-align: middle;"><b>(C)</b> ${cleanSpacers(optCD[1])}</td>` +
                    `<td width="50%" align="left" style="padding: 4px 6px; vertical-align: middle;"><b>(D)</b> ${cleanSpacers(optCD[2])}</td>` +
                    `</tr></table>`;
            }

            // General LaTeX markup outside math mode
            s = s.replace(/\\textbf\{([^}]+)\}/g, '<b>$1</b>');
            s = s.replace(/\\textit\{([^}]+)\}/g, '<i>$1</i>');
            s = s.replace(/\\emph\{([^}]+)\}/g, '<i>$1</i>');
            s = s.replace(/\\underline\{([^}]+)\}/g, '<u>$1</u>');
            s = s.replace(/\\text\{([^}]+)\}/g, '$1');
            s = s.replace(/\\(?:quad|qquad|hspace\{[^}]*\})+/g, '&emsp;&emsp;');
            s = s.replace(/\\(?:vspace\{[^}]*\}|newline|\\)/g, '<br/>');
            return s;
        }

        const pMargin = Math.max(6, Math.round(fontSize * 0.4));
        const paragraphs = processed.split(/\n\s*\n/).map(p => {
            const lines = p.split('\n').map(l => formatTextLine(l));
            let joined = '';
            for (let i = 0; i < lines.length; i++) {
                const cur = lines[i];
                if (!cur) continue;
                if (joined.length > 0 && !joined.endsWith('</div>') && !joined.endsWith('</table>') && !cur.startsWith('<table') && !cur.startsWith('<div')) {
                    joined += '<br/>';
                }
                joined += cur;
            }
            return `<div style="margin: ${pMargin}px 0; line-height: 1.7; font-size: ${fontSize}px;">${joined}</div>`;
        }).join('');

        const finalHtml = `
            <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans', Ubuntu, sans-serif; color: ${color}; padding: ${paddingTop}px 18px 18px 18px;">
                ${paragraphs}
            </div>
        `;

        return { html: finalHtml, error: null };
    } catch (err) {
        return { html: null, error: err.message };
    }
}

// Persistent JSON-RPC loop over stdin/stdout
const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
    terminal: false
});

rl.on('line', (line) => {
    if (!line.trim()) return;
    try {
        const req = JSON.parse(line);
        const fontSize = req.fontSize || 16;
        const paddingTop = req.paddingTop || 16;
        const result = renderContent(req.latex, req.color || '#E2E8F0', fontSize, paddingTop);
        const resp = {
            id: req.id,
            html: result.html,
            error: result.error
        };
        process.stdout.write(JSON.stringify(resp) + '\n');
    } catch (parseErr) {
        process.stdout.write(JSON.stringify({ error: parseErr.message }) + '\n');
    }
});
