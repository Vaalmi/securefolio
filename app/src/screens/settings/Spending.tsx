import { useState } from "react";
import type { ReactElement } from "react";

import { fmtMoment, modelName } from "../../format";
import { updateSettings, getSpending } from "../../api";
import type { CloudCall } from "../../api";
import { useAction } from "../../useAction";
import { useLoad } from "../../useLoad";

const PURPOSES: Record<string, string> = {
  answer: "An answer",
  rewrite: "Turning a follow-up into a search",
  summary: "Summarizing a chat",
  memory: "Noting what to remember",
  card: "Writing a card",
  key_test: "Testing your key",
  page_read: "Reading a page",
  eval_judge: "Judging an evaluation",
};

function usd(amount: number): string {
  return `$${amount.toFixed(amount > 0 && amount < 0.01 ? 4 : 2)}`;
}

function CallRow({ call }: Readonly<{ call: CloudCall }>): ReactElement {
  const refused = call.outcome === "refused";
  return (
    <tr>
      <td className="small">{fmtMoment(call.at)}</td>
      <td>
        {PURPOSES[call.purpose] ?? call.purpose}
        {refused && <span className="small muted"> · refused, not sent</span>}
      </td>
      <td className="small">{call.provider_name}</td>
      <td className="small">{call.model_name || modelName(call.model)}</td>
      <td className="num">
        {(call.input_tokens + call.output_tokens).toLocaleString("en-US")}
      </td>
      <td className="num">{call.document_ids.length}</td>
      <td className="num">{usd(call.cost_usd)}</td>
    </tr>
  );
}

export function Spending(): ReactElement {
  const spending = useLoad(() => getSpending(), "spending");
  const [draft, setDraft] = useState<string>();
  const cap = draft ?? spending.data?.cap_usd.toFixed(2) ?? "";
  const amount = Number(cap.replace(/[$,]/g, ""));
  const valid = Number.isFinite(amount) && amount > 0;
  const save = useAction(
    () => updateSettings({ spend_cap_usd: amount }),
    spending.reload,
  );
  const spent = spending.data?.spent_usd ?? 0;
  const share = valid ? Math.min(100, Math.round((spent / amount) * 100)) : 0;
  return (
    <>
      <div className="card">
        <h2 style={{ marginBottom: 8 }}>Monthly spend cap</h2>
        <div className="hstack" style={{ gap: 16, flexWrap: "wrap" }}>
          <div className="field" style={{ width: 160 }}>
            <label htmlFor="cap">Cap in US dollars</label>
            <input
              id="cap"
              className="input mono"
              inputMode="decimal"
              value={cap}
              aria-describedby="cap-help cap-err"
              aria-invalid={!valid}
              onChange={(event) => {
                setDraft(event.target.value);
              }}
              onBlur={() => {
                if (valid) void save.run();
              }}
            />
            <span
              id="cap-err"
              className="small"
              role="status"
              style={{ color: "var(--ink-2)" }}
            >
              {valid
                ? save.error
                : "Enter an amount in US dollars, such as 25.00."}
            </span>
          </div>
          <div className="stack" style={{ gap: 6, flex: 1, minWidth: 220 }}>
            <span className="small">
              This month: <strong className="mono">{usd(spent)}</strong> of{" "}
              <span className="mono">{valid ? usd(amount) : "…"}</span>
            </span>
            <div
              className="progress"
              role="progressbar"
              aria-label="Spend this month"
              aria-valuenow={share}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <span style={{ width: `${String(share)}%` }} />
            </div>
          </div>
        </div>
        <p id="cap-help" className="help" style={{ marginTop: 8 }}>
          The cap covers every provider together: Anthropic, OpenAI and
          OpenRouter. At the cap, background cloud work such as organizing
          pauses. Answers you ask for still run, and a Deep run over $0.50
          always asks first.
        </p>
      </div>
      <div className="card">
        <div className="hstack" style={{ marginBottom: 8 }}>
          <h2 style={{ flex: 1 }}>Cloud activity</h2>
          <span className="small muted">Every call that left this Mac</span>
        </div>
        {spending.error && <p role="alert">{spending.error}</p>}
        <div className="table-scroll">
          <table className="table">
            <caption className="sr-only">Cloud calls</caption>
            <thead>
              <tr>
                <th scope="col">When</th>
                <th scope="col">What</th>
                <th scope="col">Provider</th>
                <th scope="col">Model</th>
                <th scope="col" className="num">
                  Tokens
                </th>
                <th scope="col" className="num">
                  Documents
                </th>
                <th scope="col" className="num">
                  Cost
                </th>
              </tr>
            </thead>
            <tbody>
              {(spending.data?.log ?? []).map((call) => (
                <CallRow key={call.id} call={call} />
              ))}
            </tbody>
          </table>
        </div>
        <p className="small muted" style={{ marginTop: 8 }}>
          Private documents never appear here: their answers run on this Mac.
          Prices are dated {spending.data?.prices_as_of}.
        </p>
      </div>
    </>
  );
}
