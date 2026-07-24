// Constitution rule 7 / AI-10: this banner must stay persistent and
// unmissable on every review screen -- never removed, hidden, or made
// dismissible, regardless of visual redesign.
export function Disclaimer() {
  return (
    <div className="border-b bg-foreground px-4 py-2 text-center text-sm font-medium text-background">
      AI-generated draft. Not legal advice. Requires attorney review.
    </div>
  );
}
