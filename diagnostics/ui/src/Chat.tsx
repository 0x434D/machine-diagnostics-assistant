/** One question, the steps it took, and the answer.
 *
 * Behaviour is M1's and unchanged. What moved at M6 is where it sits: §15 deferred the
 * frontend design language until the shape of an answer could be seen, that shape is now
 * an artifact, and this view is one of several in an application rather than the whole of
 * it. So it renders a `<section>` and the shell renders the `<main>` around it.
 */
import { useState, type ReactNode } from "react";

import { ask, describeFailure, type Answer } from "./api";
import { ContradictionBanner } from "./answer/ContradictionBanner";
import { FeedbackForm } from "./answer/FeedbackForm";
import { ReasoningTrace } from "./answer/ReasoningTrace";
import { useAuth } from "./AuthContext";
import { CitationChip } from "./CitationChip";
import { ExchangeProvider, type Exchange } from "./citations/exchange";

const EXAMPLE =
  "How many parts were rejected in the last hour, and what were the defects?";

export function Chat() {
  const { token } = useAuth();
  const [question, setQuestion] = useState(EXAMPLE);
  const [progress, setProgress] = useState<string[]>([]);
  const [answer, setAnswer] = useState<Answer | null>(null);
  // Which exchange the answer below belongs to (§7.2's `session` event). §7.4's charts are
  // drawn from the tool calls in this exchange's trace, so a citation cannot be opened
  // without it — and it arrives before the first step runs, which is what lets a trace be
  // reached even for a run that then failed.
  const [exchange, setExchange] = useState<Exchange | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setProgress([]);
    setAnswer(null);
    setExchange(null);
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
        await ask(question, token, {
          progress: (line) => setProgress((s) => [...s, line]),
          exchange: setExchange,
        }),
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

      {answer === null ? null : (
        <AnswerView answer={answer} exchange={exchange} />
      )}
    </section>
  );
}

function AnswerView({
  answer,
  exchange,
}: {
  answer: Answer;
  exchange: Exchange | null;
}) {
  const citations = (answer.findings ?? []).flatMap(
    (finding) => finding.citations ?? [],
  );
  // §6.5's field is optional *and* nullable, and both mean the same thing here: the agent
  // did not disagree. Collapsed once rather than twice at the branch below.
  const contradiction = answer.contradiction ?? null;

  const evidence =
    citations.length === 0 ? null : (
      <p className="citations">
        Evidence:{" "}
        {citations.map((citation, index) => (
          <CitationChip
            // The index, because two citations of one kind are two referents and several
            // kinds carry no id at all — §7.4's charts are keyed by a tool call, and two
            // views of one call are two legitimate chips.
            key={`${citation.kind}:${String(index)}`}
            citation={citation}
          />
        ))}
      </p>
    );

  return (
    <article className="answer">
      {/* Above the prose, because §6.5 says the UI renders it prominently and because the
          answer underneath was written by one of the two sides it reports. A reader who
          meets the disagreement after the conclusion has already taken the conclusion. */}
      {contradiction === null ? null : (
        <ContradictionBanner contradiction={contradiction} />
      )}

      {answer.answer_markdown.split("\n\n").map((paragraph) => (
        <p key={paragraph}>{emphasise(paragraph)}</p>
      ))}

      {/* The exchange the citations below belong to. A `chart` citation names a tool call
          this run made (§7.4) and can only be resolved inside the run that made it. */}
      {exchange === null ? (
        evidence
      ) : (
        <ExchangeProvider exchange={exchange}>{evidence}</ExchangeProvider>
      )}

      <ReasoningTrace answer={answer} exchange={exchange} />

      {exchange === null ? (
        <p className="feedback__unaddressed">
          This answer was not addressed by the stream, so feedback on it has
          nowhere to be recorded.
        </p>
      ) : (
        <FeedbackForm exchange={exchange} />
      )}
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
