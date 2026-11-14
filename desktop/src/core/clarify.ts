const norm = (s: string) => s.replace(/\s+/g, " ").trim().toLocaleLowerCase("tr");

/** Clarification questions that do not repeat the clarification text of an ambiguity entry. */
export function uniqueQuestions(ambiguities: { clarification: string }[], questions: string[]): string[] {
  const shown = new Set(ambiguities.map((a) => norm(a.clarification)));
  const seen = new Set<string>();
  return questions.filter((q) => {
    const k = norm(q);
    if (shown.has(k) || seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}
