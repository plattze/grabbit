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
import { SettingsPanel } from "./SettingsPanel";

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

function InstallExtension() {
  const [state, setState] = useState<"idle" | "busy" | "done" | "denied" | "error">("idle");

  const download = async () => {
    setState("busy");
    try {
      await api.downloadExtension();
      setState("done");
    } catch (e) {
      setState(e instanceof ApiError && e.status === 403 ? "denied" : "error");
    }
  };

  return (
    <div className="panel install-ext">
      <span>
        <b>Chrome extension</b> — right-click any page or link → “Send to Grabbit”.
      </span>
      <button className="primary" onClick={download} disabled={state === "busy"}>
        {state === "busy" ? "Preparing…" : "Install Chrome plugin"}
      </button>
      {state === "done" && (
        <p className="hint">
          Downloaded, preconfigured with this server and a fresh API key. Unzip it, open{" "}
          <code>chrome://extensions</code>, enable Developer mode, and <b>Load unpacked</b> —
          done, no further setup.
        </p>
      )}
      {state === "denied" && (
        <p className="hint">Needs an admin key (the download mints a submit key for the extension).</p>
      )}
      {state === "error" && <p className="hint">Download failed — check the server logs.</p>}
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

function MergeBar({
  jobs,
  selected,
  onDone,
  onClear,
}: {
  jobs: Job[];
  selected: number[];
  onDone: () => void;
  onClear: () => void;
}) {
  const [asking, setAsking] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const first = jobs.find((j) => j.id === selected[0]);

  const start = () => {
    setName(first?.dir_name ?? "");
    setError(null);
    setAsking(true);
  };
  const doMerge = async () => {
    try {
      await api.merge(selected, name.trim());
      setAsking(false);
      onClear();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "merge failed");
    }
    onDone();
  };

  return (
    <div className="panel statbar">
      <span>
        <b>{selected.length}</b> selected
      </span>
      {!asking && (
        <>
          <button className="primary" onClick={start}>
            Merge into one folder
          </button>
          <button onClick={onClear}>Clear</button>
        </>
      )}
      {asking && (
        <>
          <input
            type="text"
            placeholder="Merged folder name"
            value={name}
            autoFocus
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && name.trim()) void doMerge();
              if (e.key === "Escape") setAsking(false);
            }}
          />
          <button className="primary" disabled={!name.trim()} onClick={doMerge}>
            Merge
          </button>
          <button onClick={() => setAsking(false)}>Cancel</button>
        </>
      )}
      {error && <span className="error">{error}</span>}
    </div>
  );
}

function JobRow({
  job,
  onChanged,
  selected,
  onSelect,
}: {
  job: Job;
  onChanged: () => void;
  selected?: boolean;
  onSelect?: (checked: boolean) => void;
}) {
  const [renaming, setRenaming] = useState(false);
  const [newName, setNewName] = useState("");
  const [renameError, setRenameError] = useState<string | null>(null);
  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
    } catch {
      /* surfaced by refresh */
    }
    onChanged();
  };
  const canRename = job.state !== "cancelled";
  const startRename = () => {
    setNewName(job.rename_to ?? job.dir_name);
    setRenameError(null);
    setRenaming(true);
  };
  const doRename = async () => {
    try {
      await api.rename(job.id, newName.trim());
      setRenaming(false);
    } catch (e) {
      setRenameError(e instanceof ApiError ? e.message : "rename failed");
    }
    onChanged();
  };
  const pct =
    job.files_total > 0 ? Math.min(100, (job.files_done / job.files_total) * 100) : null;
  return (
    <div className="job">
      <div className="url" title={job.url}>
        {onSelect && (
          <input
            type="checkbox"
            checked={selected ?? false}
            onChange={(e) => onSelect(e.target.checked)}
            title="Select for merge"
          />
        )}{" "}
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
        {canRename && !renaming && <button onClick={startRename}>Rename</button>}
        <button onClick={() => act(() => api.remove(job.id))}>
          {["queued", "active", "paused"].includes(job.state) ? "Cancel" : "Remove"}
        </button>
      </div>
      {renaming && (
        <div className="submit-row">
          <input
            type="text"
            placeholder="Directory name"
            value={newName}
            autoFocus
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && newName.trim()) void doRename();
              if (e.key === "Escape") setRenaming(false);
            }}
          />
          <button className="primary" disabled={!newName.trim()} onClick={doRename}>
            Rename
          </button>
          <button onClick={() => setRenaming(false)}>Cancel</button>
        </div>
      )}
      {renameError && <div className="error">{renameError}</div>}
      <div className="meta">
        <span className={`badge ${job.state}`}>{job.state}</span>
        <span>{job.host}</span>
        {job.dir_name && <span title="Output directory">📁 {job.dir_name}</span>}
        {job.rename_to && <span>→ {job.rename_to} (on completion)</span>}
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
  const [view, setView] = useState<"queue" | "keys" | "settings">("queue");
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
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
          <button
            className={`linkish ${view === "settings" ? "active" : ""}`}
            onClick={() => setView("settings")}
          >
            Settings
          </button>
        </nav>
      </header>

      {wsDown && <div className="offline">Live updates disconnected — retrying…</div>}

      {view === "settings" ? (
        <SettingsPanel />
      ) : view === "keys" ? (
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

          {selectedIds.length >= 2 && (
            <MergeBar
              jobs={jobs}
              selected={selectedIds}
              onDone={scheduleRefresh}
              onClear={() => setSelectedIds([])}
            />
          )}

          <div className="joblist">
            {visible.length === 0 && <div className="empty">No downloads</div>}
            {visible.map((job) => (
              <JobRow
                key={job.id}
                job={job}
                onChanged={scheduleRefresh}
                selected={selectedIds.includes(job.id)}
                onSelect={
                  job.state === "done" && job.dir_name
                    ? (checked) =>
                        setSelectedIds((prev) =>
                          checked ? [...prev, job.id] : prev.filter((i) => i !== job.id),
                        )
                    : undefined
                }
              />
            ))}
          </div>

          <InstallExtension />
        </>
      )}
    </div>
  );
}
