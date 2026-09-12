/**
 * Заголовок страницы. Курсивная фраза — подпись референса: серифный курсив
 * внутри обычной строки.
 */
export function PageTitle({ eyebrow, plain, italic }:
  { eyebrow: string; plain: string; italic?: string }) {
  return (
    <header className="mb-8">
      <p className="nums text-caption-1-semibold uppercase tracking-[0.12em] text-text-tertiary">
        {eyebrow}
      </p>
      <h1 className="mt-2 font-[family-name:var(--font-literata)] text-display-recorder text-text-primary sm:text-display-recorder-lg">
        {plain}
        {italic ? <> <em className="italic">{italic}</em></> : null}
      </h1>
    </header>
  );
}
