import { useCallback, useEffect, useState } from "react";
import { api, ApiError, ApiKeyCreated, ApiKeyInfo } from "./api";

export function KeysPanel() {
  const [keys, setKeys] = useState<ApiKeyInfo[]>([]);
  const [name, setName] = useState("");
  const [scope, setScope] = useState<"submit" | "admin">("submit");
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setKeys(await api.listKeys());
      setError(null);
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 403
          ? "Key management needs an admin-scoped key."
          : "Failed to load keys.",
      );
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const create = async () => {
    try {
      const k = await api.createKey(name.trim(), scope);
      setCreated(k);
      setName("");
      void refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "create failed");
    }
  };

  if (error) {
    return (
      <div className="panel">
        <p>{error}</p>
      </div>
    );
  }

  return (
    <div className="panel">
      <div className="submit-row" style={{ marginTop: 0 }}>
        <input
          type="text"
          placeholder="Key name (e.g. chrome-extension)"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <select
          value={scope}
          onChange={(e) => setScope(e.target.value as "submit" | "admin")}
        >
          <option value="submit">submit</option>
          <option value="admin">admin</option>
        </select>
        <button className="primary" disabled={!name.trim()} onClick={create}>
          Create
        </button>
      </div>

      {created && (
        <div className="token-reveal">
          <p>Copy this token now — it will not be shown again.</p>
          {created.token}
        </div>
      )}

      <table className="keys" style={{ marginTop: "1rem" }}>
        <thead>
          <tr>
            <th>Name</th>
            <th>Scope</th>
            <th>Created</th>
            <th>Last used</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {keys.map((k) => (
            <tr key={k.id}>
              <td>{k.name}</td>
              <td>{k.scope}</td>
              <td>{new Date(k.created_at).toLocaleDateString()}</td>
              <td>{k.last_used_at ? new Date(k.last_used_at).toLocaleString() : "never"}</td>
              <td>
                <button
                  onClick={async () => {
                    await api.deleteKey(k.id).catch(() => {});
                    void refresh();
                  }}
                >
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
