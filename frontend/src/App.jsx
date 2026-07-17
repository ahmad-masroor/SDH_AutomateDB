import { useState } from "react";

const API_URL = "http://localhost:8000/api/ask";

function StatusBadge({ status }) {
  const labels = {
    success: "Success",
    needs_clarification: "Needs clarification",
    blocked: "Blocked",
    failed: "Failed",
  };
  return <span className={`badge badge-${status}`}>{labels[status] || status}</span>;
}

function ResultsTable({ rows }) {
  if (!rows || rows.length === 0) {
    return <p className="muted">No rows returned.</p>;
  }
  const columns = Object.keys(rows[0]);
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col}>{col}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {columns.map((col) => (
                <td key={col}>{String(row[col])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function App() {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!question.trim() || loading) return;

    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const response = await fetch(API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => null);
        throw new Error(detail?.detail || `Request failed (${response.status})`);
      }
      const data = await response.json();
      setResult(data);
    } catch (err) {
      setError(err.message || "Something went wrong reaching the API.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="page">
      <header>
        <h1>AutomateDB</h1>
        <p className="subtitle">Natural language questions, answered with real SQL (Initial Phase).</p>
      </header>

      <form onSubmit={handleSubmit} className="ask-form">
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="e.g. Which department has the most employees?"
          disabled={loading}
        />
        <button type="submit" disabled={loading || !question.trim()}>
          {loading ? "Thinking..." : "Ask"}
        </button>
      </form>

      {error && (
        <div className="panel panel-error">
          <strong>Request failed:</strong> {error}
        </div>
      )}

      {result && (
        <div className="results">
          <div className="results-header">
            <StatusBadge status={result.status} />
            {result.attempts && (
              <span className="muted">
                {result.attempts} attempt{result.attempts > 1 ? "s" : ""}
              </span>
            )}
          </div>

          {result.sql && (
            <div className="panel">
              <div className="panel-label">SQL</div>
              <pre className="sql-block">{result.sql}</pre>
            </div>
          )}

          {result.message && (
            <div className="panel">
              <div className="panel-label">Message</div>
              <p>{result.message}</p>
            </div>
          )}

          {result.rows && (
            <div className="panel">
              <div className="panel-label">Results ({result.rows.length} row{result.rows.length !== 1 ? "s" : ""})</div>
              <ResultsTable rows={result.rows} />
            </div>
          )}

          {result.tables_used && (
            <div className="panel panel-muted">
              <div className="panel-label">Tables used</div>
              <p className="muted">{result.tables_used.join(", ")}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
