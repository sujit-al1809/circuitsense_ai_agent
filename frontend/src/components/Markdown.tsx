import type { ReactNode } from "react";

// Minimal, safe Markdown subset for agent answers: paragraphs, "-"/"1." lists,
// **bold**, _italic_ / *italic* and `code`. Produces React elements only.

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|_[^_\s][^_]*_|\*[^*\s][^*]*\*)/g;

function inline(text: string, keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let i = 0;
  for (const m of text.matchAll(INLINE)) {
    const idx = m.index ?? 0;
    if (idx > last) out.push(text.slice(last, idx));
    const tok = m[0];
    const key = `${keyPrefix}-${i++}`;
    if (tok.startsWith("**")) out.push(<strong key={key} className="font-semibold text-ink-1">{tok.slice(2, -2)}</strong>);
    else if (tok.startsWith("`")) out.push(<code key={key} className="rounded bg-surface-3 px-1 py-0.5 font-mono text-[0.85em] text-accent">{tok.slice(1, -1)}</code>);
    else out.push(<em key={key} className="text-ink-2">{tok.slice(1, -1)}</em>);
    last = idx + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown({ text }: { text: string }) {
  const blocks = text.replace(/\r\n/g, "\n").split(/\n{2,}/);
  return (
    <div className="space-y-2.5 text-sm leading-relaxed text-ink-1/95">
      {blocks.map((block, bi) => {
        const lines = block.split("\n").filter((l) => l.trim() !== "");
        if (lines.length === 0) return null;
        if (lines.length === 1 && /^#{1,6}\s+/.test(lines[0])) {
          return (
            <p key={bi} className="pt-1 font-semibold text-ink-1">
              {inline(lines[0].replace(/^#{1,6}\s+/, ""), `${bi}-h`)}
            </p>
          );
        }
        const bullet = lines.every((l) => /^\s*[-•]\s+/.test(l));
        const ordered = lines.every((l) => /^\s*\d+\.\s+/.test(l));
        if (bullet || ordered) {
          const items = lines.map((l, li) => (
            <li key={li}>{inline(l.replace(/^\s*([-•]|\d+\.)\s+/, ""), `${bi}-${li}`)}</li>
          ));
          return ordered ? (
            <ol key={bi} className="list-decimal space-y-1 pl-5 marker:text-ink-3">{items}</ol>
          ) : (
            <ul key={bi} className="list-disc space-y-1 pl-5 marker:text-ink-3">{items}</ul>
          );
        }
        // A heading-like line followed by list items.
        const firstList = lines.findIndex((l) => /^\s*[-•]\s+/.test(l) || /^\s*\d+\.\s+/.test(l));
        if (firstList > 0 && lines.slice(firstList).every((l) => /^\s*([-•]|\d+\.)\s+/.test(l))) {
          const isOrdered = /^\s*\d+\./.test(lines[firstList]);
          const items = lines.slice(firstList).map((l, li) => (
            <li key={li}>{inline(l.replace(/^\s*([-•]|\d+\.)\s+/, ""), `${bi}-l${li}`)}</li>
          ));
          return (
            <div key={bi} className="space-y-1">
              <p>{inline(lines.slice(0, firstList).join(" "), `${bi}-h`)}</p>
              {isOrdered ? (
                <ol className="list-decimal space-y-1 pl-5 marker:text-ink-3">{items}</ol>
              ) : (
                <ul className="list-disc space-y-1 pl-5 marker:text-ink-3">{items}</ul>
              )}
            </div>
          );
        }
        return <p key={bi}>{inline(lines.join(" "), `${bi}`)}</p>;
      })}
    </div>
  );
}
