/** One question, the steps it took, and the answer.
 *
 * Behaviour is M1's and unchanged. What moved at M6 is where it sits: §15 deferred the
 * frontend design language until the shape of an answer could be seen, that shape is now
 * an artifact, and this view is one of several in an application rather than the whole of
 * it. So it renders a `<section>` and the shell renders the `<main>` around it.
 */
import { useState, type ReactNode } from "react";

import { ask, describeFailure, type Answer } from "./api";
import { useAuth } from "./AuthContext";
import { CitationChip } from "./CitationChip";

const EXAMPLE =
  "How many parts were rejected in the last hour, and what were the defects?";

export function Chat() {
  const { token } = useAuth();
  const [question, setQuestion] = useState(EXAMPLE);
  const [progress, setProgress] = useState<string[]>([]);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setProgress([]);
    setAnswer(null);
    setError(null);

    if (token === null) {
      // §10.5: every diagnostics endpoint refuses an unauthenticated request, so sending
      // one here is a round trip whose 401 is already known. Saying so up front is also
      // what keeps a missing token from reading like the 401 case below -- both are "not
      // signed in", but only one of them is worth a network call.
      setError("Not signed in. Paste a token above before asking.");
      return;
    }

    setAsking(true);
    try {
      setAnswer(
        await ask(question, token, (line) => setProgress((s) => [...s, line])),
      );
    } catch (reason: unknown) {
      // Shown, not logged. An answer box that silently stays empty is the quiet wrong
      // answer this system exists not to give.
      setError(describeFailure(reason));
    } finally {
      setAsking(false);
    }
  }

  return (
    <section className="chat">
      <form onSubmit={submit}>
        <label htmlFor="question">Ask the line</label>
        <textarea
          id="question"
          value={question}
          rows={2}
          onChange={(event) => setQuestion(event.target.value)}
        />
        <button type="submit" disabled={asking || question.trim() === ""}>
          {asking ? "Asking…" : "Ask"}
        </button>
      </form>

      {progress.length === 0 ? null : (
        <ol className="progress" aria-label="progress">
          {progress.map((line, index) => (
            <li key={`${String(index)}-${line}`}>{line}</li>
          ))}
        </ol>
      )}

      {error === null ? null : (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      {answer === null ? null : <AnswerView answer={answer} />}
    </section>
  );
}

function AnswerView({ answer }: { answer: Answer }) {
  const citations = (answer.findings ?? []).flatMap(
    (finding) => finding.citations ?? [],
  );

  return (
    <article className="answer">
      {answer.answer_markdown.split("\n\n").map((paragraph) => (
        <p key={paragraph}>{emphasise(paragraph)}</p>
      ))}

      {citations.length === 0 ? null : (
        <p className="citations">
          Evidence:{" "}
          {citations.map((citation) => (
            <CitationChip
              key={`${citation.kind}:${citation.id}`}
              citation={citation}
            />
          ))}
        </p>
      )}

      <Trace answer={answer} />
    </article>
  );
}

/**
 * `_like this_` becomes emphasis.
 *
 * Not a markdown renderer, and deliberately not a markdown dependency either: the composer
 * is ours and §6.5 has it arrange findings rather than author prose, so the only construct
 * it emits is this one, around caveats. Rendering the paragraph as plain text put literal
 * underscores in front of the reader on every answer that carried a caveat -- which is every
 * answer the scripted provider produces. If the composer ever emits more, this becomes a
 * real renderer; until then a real renderer would be 40 kB to serve one italic.
 */
function emphasise(paragraph: string): ReactNode[] {
  return paragraph
    .split(/_([^_]+)_/)
    .map((part, index) =>
      index % 2 === 1 ? <em key={`${String(index)}-${part}`}>{part}</em> : part,
    );
}

function Trace({ answer }: { answer: Answer }) {
  const { method } = answer;
  const tools = method.tools_called ?? [];
  const sops = method.sops_used ?? [];

  return (
    <details className="trace">
      {/* §7.2's reasoning trace. It is stored for the audit trail regardless, so showing
          it costs nothing — and an answer whose workings are hidden is asking to be
          trusted rather than checked. */}
      <summary>How this was answered</summary>
      <dl>
        <dt>Provider</dt>
        <dd>{method.provider ?? "unknown"}</dd>
        <dt>Tools called</dt>
        <dd>{tools.length === 0 ? "none" : tools.join(", ")}</dd>
        <dt>Budget used</dt>
        <dd>{method.budget_used ?? 0}</dd>
        <dt>SOPs used</dt>
        <dd>
          {sops.length === 0
            ? "none — knowledge routing arrives at M4"
            : sops.join(", ")}
        </dd>
      </dl>
    </details>
  );
}
