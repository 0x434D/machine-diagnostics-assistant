/** §7.2's two questions — *was this useful?* and *did this match what you actually found?*
 *
 * The second is the operator's own ground truth and the more valuable of the two signals:
 * §9's improvement loop has nothing else that compares an answer against the machine, and
 * §8's evaluation runs on scripted cases rather than on the line.
 *
 * **The two are independently answerable, and answering one must not erase the other.** The
 * endpoint `COALESCE`s per column and answers with everything now stored, so each button
 * sends only the question it belongs to and the panel re-renders from what came back — never
 * from what it sent. That is the difference that matters: a panel rendering its own optimism
 * would show both answers recorded after a request that stored one of them.
 *
 * Nothing here withdraws an answer. §7.2 asks two questions and offers no third state to move
 * back to, and a `null` that cleared a stored answer would make the ordinary case — coming
 * back an hour later to answer the second question — erase the first.
 */
import { useState } from "react";

import { describeFailure, submitFeedback, type Feedback } from "../api";
import { useAuth } from "../AuthContext";
import type { Exchange } from "../citations/exchange";

export function FeedbackForm({ exchange }: { exchange: Exchange }) {
  const { token } = useAuth();
  const [stored, setStored] = useState<Feedback | null>(null);
  const [comment, setComment] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send(answers: Feedback) {
    setError(null);
    setSending(true);
    try {
      setStored(await submitFeedback(exchange, answers, token));
    } catch (reason: unknown) {
      // Shown, not logged. Feedback that silently failed to record is worse than feedback
      // nobody gave: the person who gave it believes the system now knows.
      setError(describeFailure(reason));
    } finally {
      setSending(false);
    }
  }

  return (
    <section className="feedback" aria-label="Feedback on this answer">
      <h3 className="feedback__title">Was this any good?</h3>

      <Question
        legend="Was this useful?"
        recorded={stored?.useful ?? null}
        disabled={sending}
        onAnswer={(value) => {
          void send({ useful: value });
        }}
      />
      <Question
        legend="Did this match what you actually found?"
        hint="Your own ground truth — the more valuable of the two, and the only signal in this system that compares an answer against the machine."
        recorded={stored?.matched_reality ?? null}
        disabled={sending}
        onAnswer={(value) => {
          void send({ matched_reality: value });
        }}
      />

      <div className="feedback__comment">
        <label htmlFor="feedback-comment">Anything else worth recording</label>
        <textarea
          id="feedback-comment"
          rows={2}
          value={comment}
          onChange={(event) => {
            setComment(event.target.value);
          }}
        />
        <button
          type="button"
          disabled={sending || comment.trim() === ""}
          onClick={() => {
            void send({ comment });
          }}
        >
          Record the comment
        </button>
        {(stored?.comment ?? null) === null ? null : (
          <p className="feedback__recorded">
            Recorded comment: {stored?.comment}
          </p>
        )}
      </div>

      {error === null ? null : (
        <p role="alert" className="error">
          Feedback was not recorded: {error}
        </p>
      )}
    </section>
  );
}

/** One question: two buttons, and the answer the server says it now holds.
 *
 * `null` is *not answered*, which is a different claim from *no* — the column is nullable for
 * exactly that reason — so the panel has three states to render and not two.
 */
function Question({
  legend,
  hint = null,
  recorded,
  disabled,
  onAnswer,
}: {
  legend: string;
  hint?: string | null;
  recorded: boolean | null;
  disabled: boolean;
  onAnswer: (value: boolean) => void;
}) {
  return (
    <fieldset className="feedback__question">
      <legend>{legend}</legend>
      {hint === null ? null : <p className="feedback__hint">{hint}</p>}
      <div className="feedback__answers">
        {[true, false].map((value) => (
          <button
            key={String(value)}
            type="button"
            // The pressed state is what a screen reader reads back, and the written line
            // below is what a greyscale print shows: the colour of the chosen button is
            // never the only channel (ISA-101).
            aria-pressed={recorded === value}
            data-chosen={String(recorded === value)}
            disabled={disabled}
            onClick={() => {
              onAnswer(value);
            }}
          >
            {value ? "Yes" : "No"}
          </button>
        ))}
      </div>
      <p className="feedback__recorded">
        {recorded === null
          ? "Not answered. Answering the other question leaves this one as it is."
          : `Recorded: ${recorded ? "yes" : "no"}. It can be changed, but not withdrawn.`}
      </p>
    </fieldset>
  );
}
