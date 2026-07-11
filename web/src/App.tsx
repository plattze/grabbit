import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  ApiError,
  getToken,
  Job,
  JobState,
  openEventSocket,
  setToken,
  Stats,
  SubmitResult,
} from "./api";
import { KeysPanel } from "./KeysPanel";

const FILTERS: (JobState | "all")[] = ["all", "active", "queued", "paused", "error", "done"];

function fmtBytes(n: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i++;
  }
  return `${n.toFixed(n >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

function Login({ onDone }: { onDone: () => void }) {
  const [value, setValue] = useState("");
  return (
    <div className="login">
      <h1>Grabbit</h1>
      <div className="panel">
        <p className="hint">
          Paste an API key. The admin key is printed to the container log on first run.
        </p>
        <input
          type="text"
          placeholder="API key"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && value.trim()) {
              setToken(value.trim());
              onDone();
            }
          }}
        />
        <div className="submit-row">
          <button
            className="primary"
            disabled={!value.trim()}
            onClick={() => {
              setToken(value.trim());
              onDone();
            }}
          >
            Save key
          </button>
        </div>
      </div>
    </div>
  );
}

function SubmitBox({ onSubmitted }: { onSubmitted: () => void }) {
  const [text, setText] = useState("");
  const [dest, setDest] = useState("");
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<SubmitResult[]>([]);

  const submit = async () => {
    const urls = text
      .split(/\s+/)
      .map((u) => u.trim())
      .filter(Boolean);
    if (!urls.length) return;
    setBusy(true);
    try {
      const res = await api.submit(urls, dest.trim() || undefined);
      setResults(res);
      if (res.every((r) => r.accepted)) setText("");
      onSubmitted();
    } catch (e) {
      setResults([
        { url: "", accepted: false, job_id: null, reason: e instanceof Error ? e.message : "failed" },
      ]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel">
      <textarea
        placeholder="Paste URLs — one per line"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="submit-row">
        <input
          type="text"
          placeholder="Sub-folder (optional)"
          value={dest}
          onChange={(e) => setDest(e.target.value)}
        />
        <button className="primary" onClick={submit} disabled={busy || !text.trim()}>
          {busy ? "Queuing…" : "Queue"}
        </button>
      </div>
      {results.length > 0 && (
        <ul className="results">
          {results.map((r, i) => (
            <li key={i} className={r.accepted ? "ok" : "rejected"}>
              {r.accepted ? "✓ queued" : `✗ ${r.reason}`}
              {r.url ? ` — ${r.url}` : ""}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function JobRow({ job, onChanged }: { job: Job; onChanged: () => void }) {
  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
    } catch {
      /* surfaced by refresh */
    }
    onChanged();
  };
  const pct =
    job.files_total > 0 ? Math.min(100, (job.files_done / job.files_total) * 100) : null;
  return (
    <div className="job">
      <div className="url" title={job.url}>
        {job.url}
      </div>
      <div className="actions">
        {(job.state === "queued" || job.state === "active") && (
          <button onClick={() => act(() => api.pause(job.id))}>Pause</button>
        )}
        {job.state === "paused" && (
          <button onClick={() => act(() => api.resume(job.id))}>Resume</button>
        )}
        {(job.state === "error" || job.state === "cancelled") && (
          <button onClick={() => act(() => api.retry(job.id))}>Retry</button>
        )}
        <button onClick={() => act(() => api.remove(job.id))}>
          {["queued", "active", "paused"].includes(job.state) ? "Cancel" : "Remove"}
        </button>
      </div>
      <div className="meta">
        <span className={`badge ${job.state}`}>{job.state}</span>
        <span>{job.host}</span>
        {job.files_done > 0 && (
          <span>
            {job.files_done}
            {job.files_total > 0 ? ` / ${job.files_total}` : ""} files
          </span>
        )}
      </div>
      {job.state === "active" && (
        <div className="progress">
          <div style={{ width: pct !== null ? `${pct}%` : "100%" }} />
        </div>
      )}
      {job.error && <div className="error">{job.error}</div>}
    </div>
  );
}

export default function App() {
  const [authed, setAuthed] = useState(() => Boolean(getToken()));
  const [authFailed, setAuthFailed] = useState(false);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("all");
  const [view, setView] = useState<"queue" | "keys">("queue");
  const [wsDown, setWsDown] = useState(false);
  const refreshTimer = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [j, s] = await Promise.all([api.listJobs(), api.stats()]);
      setJobs(j);
      setStats(s);
      setAuthFailed(false);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setAuthFailed(true);
    }
  }, []);

  // Debounced refresh so a burst of WS events causes one reload.
  const scheduleRefresh = useCallback(() => {
    if (refreshTimer.current !== null) return;
    refreshTimer.current = window.setTimeout(() => {
      refreshTimer.current = null;
      void refresh();
    }, 250);
  }, [refresh]);

  useEffect(() => {
    if (!authed) return;
    void refresh();
    const close = openEventSocket(
      (ev) => {
        setWsDown(false);
        if (ev.type === "progress") {
          setJobs((prev) =>
            prev.map((j) =>
              j.id === ev.job_id ? { ...j, files_done: ev.files_done } : j,
            ),
          );
        } else {
          scheduleRefresh();
        }
      },
      () => setWsDown(true),
    );
    // Fallback poll: keeps the view converging even if WS is blocked.
    const poll = window.setInterval(() => void refresh(), 15000);
    return () => {
      close();
      window.clearInterval(poll);
    };
  }, [authed, refresh, scheduleRefresh]);

  const visible = useMemo(
    () => (filter === "all" ? jobs : jobs.filter((j) => j.state === filter)),
    [jobs, filter],
  );

  if (!authed || authFailed) {
    return (
      <Login
        onDone={() => {
          setAuthed(true);
          setAuthFailed(false);
          void refresh();
        }}
      />
    );
  }

  return (
    <div className="app">
      <header className="top">
        <h1>Grabbit</h1>
        {stats && <span className="version">v{stats.version}</span>}
        <nav>
          <button
            className={`linkish ${view === "queue" ? "active" : ""}`}
            onClick={() => setView("queue")}
          >
            Queue
          </button>
          <button
            className={`linkish ${view === "keys" ? "active" : ""}`}
            onClick={() => setView("keys")}
          >
            API keys
          </button>
        </nav>
      </header>

      {wsDown && <div className="offline">Live updates disconnected — retrying…</div>}

      {view === "keys" ? (
        <KeysPanel />
      ) : (
        <>
          <SubmitBox onSubmitted={scheduleRefresh} />

          {stats && (
            <div className="panel statbar">
              <span>
                Active <b>{stats.active}</b>
              </span>
              <span>
                Queued <b>{stats.queued}</b>
              </span>
              <span>
                Disk free <b>{fmtBytes(stats.disk_free_bytes)}</b>
              </span>
            </div>
          )}

          <div style={{ display: "flex", gap: "0.25rem", marginBottom: "0.75rem" }}>
            {FILTERS.map((f) => (
              <button
                key={f}
                className={`linkish ${filter === f ? "active" : ""}`}
                onClick={() => setFilter(f)}
              >
                {f}
              </button>
            ))}
          </div>

          <div className="joblist">
            {visible.length === 0 && <div className="empty">No downloads</div>}
            {visible.map((job) => (
              <JobRow key={job.id} job={job} onChanged={scheduleRefresh} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
