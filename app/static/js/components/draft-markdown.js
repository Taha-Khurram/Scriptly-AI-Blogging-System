/**
 * Draft markdown — the preview renderer, shared by Create and History.
 *
 * A deliberately small subset: headings, both kinds of list, bold, italic,
 * inline code. Not a markdown library, and not trying to be. Both callers are
 * previewing text the server renders properly elsewhere, and the point is only
 * to make the *shape* of a piece legible — where the sections are, where the
 * lists are — at a glance.
 *
 * It lives here because two screens render the same draft text:
 *
 *   - Create paints it a chunk at a time as the model writes, where a
 *     half-finished `**bo` showing its asterisks for one frame is the correct
 *     trade for seeing the piece appear.
 *   - History paints the opening of a draft that finished days ago.
 *
 * Everything it touches is model output, so the source is escaped *first* and
 * the markup is added afterwards, to text that can no longer contain any. That
 * ordering is the whole safety argument for handing the result to innerHTML:
 * get it backwards and a post that happens to contain a script tag runs.
 */

(function draftMarkdown() {
    'use strict';

    const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;' };

    // Everything below renders model output, so it is escaped before a single
    // formatting rule touches it. The markdown-ish tags are added afterwards,
    // to text that can no longer contain markup.
    function escapeHtml(text) {
        return String(text).replace(/[&<>]/g, (c) => ESC[c]);
    }

    function inlineMd(text) {
        return text
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
            .replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    }

    function renderMd(src) {
        const out = [];
        let para = [];
        let list = null;

        const flushPara = () => {
            if (!para.length) return;
            out.push('<p>' + inlineMd(para.join(' ')) + '</p>');
            para = [];
        };
        const flushList = () => {
            if (!list) return;
            out.push('<' + list.tag + '>' + list.items.join('') + '</' + list.tag + '>');
            list = null;
        };
        const pushItem = (tag, html) => {
            if (!list || list.tag !== tag) {
                flushList();
                list = { tag: tag, items: [] };
            }
            list.items.push('<li>' + inlineMd(html) + '</li>');
        };

        escapeHtml(src).split('\n').forEach((raw) => {
            const line = raw.trim();
            if (!line) {
                flushPara();
                flushList();
                return;
            }

            const heading = /^(#{1,6})\s+(.*)$/.exec(line);
            if (heading) {
                flushPara();
                flushList();
                const tag = heading[1].length <= 2 ? 'h3' : 'h4';
                out.push('<' + tag + '>' + inlineMd(heading[2]) + '</' + tag + '>');
                return;
            }

            const bullet = /^[-*]\s+(.*)$/.exec(line);
            if (bullet) {
                flushPara();
                pushItem('ul', bullet[1]);
                return;
            }

            const numbered = /^\d+[.)]\s+(.*)$/.exec(line);
            if (numbered) {
                flushPara();
                pushItem('ol', numbered[1]);
                return;
            }

            flushList();
            para.push(line);
        });

        flushPara();
        flushList();
        return out.join('');
    }

    window.DraftMarkdown = {
        escape: escapeHtml,
        inline: inlineMd,
        render: renderMd
    };
})();
