// Syntax-highlighted JSON rendered as React elements (no innerHTML, so vendor payloads can't inject markup).
const TOKEN = /("(?:\\.|[^"\\])*")(\s*:)?|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|\b(true|false|null)\b/g;

export function JsonView({ value, maxHeight }: { value: unknown; maxHeight?: number }) {
  const text = JSON.stringify(value, null, 2) ?? "undefined";
  const out: React.ReactNode[] = [];
  let last = 0, i = 0;
  for (const m of text.matchAll(TOKEN)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const [, str, colon, num, lit] = m;
    if (str) out.push(<span key={i++} className={colon ? "j-key" : "j-str"}>{str}</span>, colon ?? "");
    else if (num) out.push(<span key={i++} className="j-num">{num}</span>);
    else out.push(<span key={i++} className="j-lit">{lit}</span>);
    last = m.index + m[0].length;
  }
  out.push(text.slice(last));
  return <pre className="json" style={maxHeight ? { maxHeight } : undefined}>{out}</pre>;
}
