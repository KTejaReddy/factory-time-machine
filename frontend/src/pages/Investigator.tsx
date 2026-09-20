import { useState } from "react";
import { Badge, Bullets, ErrorBox, Spinner } from "../components/ui";
import { api, type AINarrative } from "../lib/api";
import { useApi } from "../lib/hooks";

const EXAMPLE_QUESTIONS = [
  "Which station is slowing down the factory?",
  "Why did the factory produce fewer parts this time?",
  "What happens if we speed up Cell 4?",
  "Where did the problem start?",
];

export default function Investigator() {
  const status = useApi(() => api.aiStatus(), []);

  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<AINarrative | null>(null);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ask = async (text?: string) => {
    const value = (text ?? question).trim();
    if (!value) return;
    setAsking(true);
    setError(null);
    try {
      const result = await api.aiAsk(value, "overview", null);
      setAnswer(result);
      setQuestion(value);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setAsking(false);
    }
  };

  const renderFinding = (payload: AINarrative) => (
    <div className="glass-panel p-8 z-10">
      <div className="flex items-center justify-between gap-2 mb-5 border-b border-[var(--color-edge)] pb-4">
        <div className="flex items-center gap-2">
          <span className="grid h-7 w-7 place-items-center rounded bg-[var(--color-accent-soft)] text-[var(--color-accent)] text-[14px]">
            ✨
          </span>
          <span className="font-semibold text-[14px] text-[var(--color-ink)]">AI Analysis</span>
        </div>
        <Badge tone={payload.source === "llm" ? "info" : "muted"}>
          {payload.source === "llm" ? "AI Powered" : "Basic Rules Engine"}
        </Badge>
      </div>

      <div className="space-y-6">
        <div>
          <h4 className="text-[11.5px] font-semibold uppercase tracking-wider text-[var(--color-ink-faint)] mb-2">Finding</h4>
          <p className="text-[14px] leading-relaxed text-[var(--color-ink)]">{payload.finding.finding}</p>
        </div>

        {payload.finding.evidence?.length > 0 && (
          <div>
            <h4 className="text-[11.5px] font-bold uppercase tracking-widest text-[var(--color-ink-faint)] mb-3">Evidence</h4>
            <div className="glass-panel p-4">
              <Bullets items={payload.finding.evidence} />
            </div>
          </div>
        )}

        {payload.finding.recommendation && (
          <div className="callout callout-info">
            <h4 className="text-[11.5px] font-semibold uppercase tracking-wider mb-1">Recommendation</h4>
            <p className="text-[13px] leading-relaxed">{payload.finding.recommendation}</p>
          </div>
        )}
      </div>
      
      <details className="mt-4 border-t border-[var(--color-edge)] pt-4 text-[13px] text-[var(--color-ink-dim)] cursor-pointer">
        <summary className="font-semibold text-[var(--color-ink)]">How this works ▾</summary>
        <div className="mt-2 space-y-2">
          <p><strong>System:</strong> The AI model explains and summarizes but does not compute numbers itself.</p>
          <p><strong>Safety:</strong> Every number it receives was produced by the backend's deterministic code.</p>
          {status.data?.llm_enabled === false && (
             <p className="text-[var(--color-bad)]"><strong>Note:</strong> API Key missing, running on basic rules engine.</p>
          )}
        </div>
      </details>
    </div>
  );

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <header className="text-center fade-in pt-4">
        <h1 className="text-[24px] font-bold tracking-widest text-[var(--color-ink)] uppercase">AI INVESTIGATOR</h1>
        <p className="mt-2 text-[14px] text-[var(--color-ink-dim)]">
          Ask about your manufacturing data.
        </p>
      </header>

      <div className="glass-panel p-8 z-10">
        <div className="flex flex-col sm:flex-row gap-3">
          <input
            className="field flex-1 text-[15px] font-medium tracking-wide px-5 py-3 bg-[rgba(255,255,255,0.8)] backdrop-blur-sm border-[rgba(20,180,100,0.3)] shadow-[inset_0_2px_4px_rgba(0,0,0,0.05)] focus:ring-[rgba(34,197,94,0.5)] focus:bg-white transition-all"
            placeholder="Ask your question..."
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') ask();
            }}
          />
          <button className="btn btn-primary px-6 font-semibold" onClick={() => ask()} disabled={asking || !question.trim()}>
            {asking ? "Thinking..." : "Ask AI"}
          </button>
        </div>
        
        {!answer && !asking && (
          <div className="mt-6 border-t border-[var(--color-edge)] pt-6">
            <h3 className="text-[12px] font-semibold text-[var(--color-ink-faint)] uppercase tracking-wider mb-3">Suggested questions</h3>
            <div className="flex flex-wrap gap-2">
              {EXAMPLE_QUESTIONS.map((item) => (
                <button 
                  key={item} 
                  className="chip chip-muted hover:bg-[var(--color-accent-soft)] hover:text-[var(--color-accent)] hover:border-[var(--color-accent-edge)] transition-colors py-1.5 px-3 text-[12px] rounded-full cursor-pointer"
                  onClick={() => ask(item)}
                >
                  {item}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="space-y-4">
        {error && <ErrorBox message={error} />}

        {asking ? (
          <div className="glass-panel p-10 text-center flex flex-col items-center justify-center text-[var(--color-ink-dim)]">
            <Spinner />
            <span className="mt-3 text-[13px]">Analyzing data...</span>
          </div>
        ) : answer ? (
          <div className="fade-in">
            {renderFinding(answer)}
          </div>
        ) : null}
      </div>
    </div>
  );
}
