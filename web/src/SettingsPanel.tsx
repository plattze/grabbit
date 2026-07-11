import { useEffect, useState } from "react";
import { api, ApiError, Settings } from "./api";

const SECTION_LABELS: Record<string, string> = {
  server: "Server",
  downloads: "Downloads",
  engine: "Engine",
  logging: "Logging",
  metrics: "Metrics",
  mcp: "MCP",
};

function fmtValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (Array.isArray(v)) return v.join(", ");
  if (typeof v === "boolean") return v ? "enabled" : "disabled";
  return String(v);
}

export function SettingsPanel() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .settings()
      .then(setSettings)
      .catch((e) =>
        setError(
          e instanceof ApiError && e.status === 403
            ? "Settings need an admin-scoped key."
            : "Failed to load settings.",
        ),
      );
  }, []);

  if (error) {
    return (
      <div className="panel">
        <p>{error}</p>
      </div>
    );
  }
  if (!settings) return <div className="panel">Loading…</div>;

  const sections = Object.entries(settings.read_only).filter(
    ([, v]) => typeof v === "object" && v !== null,
  ) as [string, Record<string, unknown>][];

  return (
    <div className="panel">
      <p className="hint">
        Read-only: these values come from <code>config.yaml</code>, <code>GRABBIT_*</code>{" "}
        environment variables, or the Docker deployment (e.g. the download destination is a
        volume mount). Change them there and restart.
      </p>
      {sections.map(([name, values]) => (
        <div key={name} className="settings-section">
          <h3>{SECTION_LABELS[name] ?? name}</h3>
          <table className="keys">
            <tbody>
              {Object.entries(values).map(([k, v]) => (
                <tr key={k}>
                  <td className="setting-name">{k.replace(/_/g, " ")}</td>
                  <td>{fmtValue(v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
      <div className="settings-section">
        <h3>Data directory</h3>
        <table className="keys">
          <tbody>
            <tr>
              <td className="setting-name">data dir</td>
              <td>{fmtValue(settings.read_only.data_dir)}</td>
            </tr>
            <tr>
              <td className="setting-name">version</td>
              <td>{settings.version}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}
