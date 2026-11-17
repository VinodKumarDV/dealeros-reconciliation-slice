import { FormEvent, useCallback, useEffect, useState } from "react";

type Exception = {
  id: number;
  record_id: string;
  reason_code: string;
  reason: string;
  location_id: string;
  location_name: string;
  system_a: Record<string, string> | null;
  system_b: Array<Record<string, string>>;
};
type Filters = {
  locations: Array<{ id: string; name: string }>;
  reasons: Array<{ code: string; label: string }>;
};

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(options?.method === "POST" && !url.endsWith("/login") && !url.endsWith("/logout")
        ? { "X-CSRFToken": document.cookie.split("; ").find((cookie) => cookie.startsWith("csrftoken="))?.split("=")[1] ?? "" }
        : {}),
      ...options?.headers,
    },
    ...options,
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error ?? body.answer ?? "Request failed.");
  return body as T;
}

export default function App() {
  const [user, setUser] = useState<{ username: string; organization_id: string } | null>(null);
  const [username, setUsername] = useState("dealer-a");
  const [password, setPassword] = useState("dealeros123");
  const [filters, setFilters] = useState<Filters>({ locations: [], reasons: [] });
  const [exceptions, setExceptions] = useState<Exception[]>([]);
  const [reason, setReason] = useState("");
  const [location, setLocation] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [checkingSession, setCheckingSession] = useState(true);

  useEffect(() => {
    let active = true;
    fetch("/api/me", { credentials: "include" })
      .then(async (response) => {
        if (response.status === 401) return null;
        const body = await response.json();
        if (!response.ok) throw new Error(body.error ?? "Could not restore the session.");
        return body as { username: string; organization_id: string };
      })
      .then((session) => {
        if (active && session) setUser(session);
      })
      .catch((cause: Error) => {
        if (active) setError(cause.message);
      })
      .finally(() => {
        if (active) setCheckingSession(false);
      });
    return () => { active = false; };
  }, []);

  const loadExceptions = useCallback(async () => {
    if (!user) return;
    const params = new URLSearchParams();
    if (reason) params.set("reason", reason);
    if (location) params.set("location", location);
    const suffix = params.size ? `?${params}` : "";
    const result = await request<{ results: Exception[] }>(`/api/exceptions${suffix}`);
    setExceptions(result.results);
  }, [location, reason, user]);

  useEffect(() => {
    if (!user) return;
    setError("");
    Promise.all([
      request<Filters>("/api/filters").then(setFilters),
      loadExceptions(),
    ]).catch((cause: Error) => setError(cause.message));
  }, [user, loadExceptions]);

  async function signIn(event: FormEvent) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      const result = await request<{ username: string; organization_id: string }>("/api/login", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      setUser(result);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function signOut() {
    try {
      await request("/api/logout", { method: "POST" });
      setUser(null);
      setExceptions([]);
      setAnswer("");
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  async function ask(event: FormEvent) {
    event.preventDefault();
    setAnswer("");
    setError("");
    try {
      const result = await request<{ answer: string }>("/api/ask", {
        method: "POST",
        body: JSON.stringify({ question }),
      });
      setAnswer(result.answer);
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  if (checkingSession) {
    return <main><p>Checking session…</p></main>;
  }

  if (!user) {
    return (
      <main className="login">
        <h1>DealerOS exceptions</h1>
        <p>Sign in to review your organization’s reconciled events.</p>
        <form onSubmit={signIn}>
          <label>Username<input value={username} onChange={(event) => setUsername(event.target.value)} /></label>
          <label>Password<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} /></label>
          <button disabled={loading}>{loading ? "Signing in…" : "Sign in"}</button>
        </form>
        <p className="hint">Demo accounts: dealer-a / dealer-b · password: dealeros123</p>
        {error && <p className="error">{error}</p>}
      </main>
    );
  }

  return (
    <main>
      <header>
        <div>
          <h1>Exceptions</h1>
          <p>Signed in as {user.username} · {user.organization_id}</p>
        </div>
        <button className="secondary" onClick={signOut}>Sign out</button>
      </header>

      <section className="filters" aria-label="Exception filters">
        <label>Reason
          <select value={reason} onChange={(event) => setReason(event.target.value)}>
            <option value="">All reasons</option>
            {filters.reasons.map((item) => <option key={item.code} value={item.code}>{item.code}</option>)}
          </select>
        </label>
        <label>Location
          <select value={location} onChange={(event) => setLocation(event.target.value)}>
            <option value="">All locations</option>
            {filters.locations.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </label>
        <span>{exceptions.length} exception row(s)</span>
      </section>

      {error && <p className="error">{error}</p>}
      <div className="table-wrap">
        <table>
          <thead><tr><th>System A record</th><th>Reason</th><th>Location</th><th>System A total</th><th>System B entries</th></tr></thead>
          <tbody>
            {exceptions.map((item) => (
              <tr key={item.id}>
                <td>{item.record_id}</td>
                <td><strong>{item.reason_code}</strong><br />{item.reason}</td>
                <td>{item.location_name}<br /><small>{item.location_id}</small></td>
                <td>{item.system_a?.total_value ?? "—"}</td>
                <td>{item.system_b.length
                  ? item.system_b.map((entry) => `${entry.entry_id}: ${entry.value || "missing amount"}`).join("; ")
                  : "No System B entry"}</td>
              </tr>
            ))}
            {!exceptions.length && <tr><td colSpan={5}>No exceptions match these filters.</td></tr>}
          </tbody>
        </table>
      </div>

      <section className="ask">
        <h2>Ask about your exceptions</h2>
        <form onSubmit={ask}>
          <input
            aria-label="Question"
            placeholder="How many amount mismatches are there?"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
          />
          <button>Ask</button>
        </form>
        <p className="hint">Supported: exception counts, location rankings, mismatch totals, and selected record lookups. Unsupported questions are refused.</p>
        {answer && <p className="answer">{answer}</p>}
      </section>
    </main>
  );
}
