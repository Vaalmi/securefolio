import type { ReactElement } from "react";

import { listSources, updateSource } from "../../api";
import { SensitivityPicker } from "../../components/FolderSettings";
import { installExtension } from "../../desktop";
import { updateSettings, getSettings } from "../../api";
import { toast } from "../../toast";
import { PrivateBadge, Switch } from "../../ui";
import { useAction } from "../../useAction";
import { useLoad } from "../../useLoad";
import { Row } from "./Row";

export function Privacy(): ReactElement {
  const settings = useLoad(getSettings, "settings");
  const sources = useLoad(listSources, "sources");
  const setDefault = useAction(updateSettings, settings.reload);
  const setMcp = useAction(
    (id: string, on: boolean) => updateSource(id, { mcp_enabled: on }),
    sources.reload,
  );
  return (
    <>
      <div className="card">
        <h2 style={{ marginBottom: 8 }}>Default sensitivity for new folders</h2>
        {settings.data && (
          <SensitivityPicker
            value={settings.data.default_sensitivity}
            name="new folders"
            onChange={(value) => {
              void setDefault.run({ default_sensitivity: value });
            }}
          />
        )}
      </div>
      <div className="card">
        <h2 style={{ marginBottom: 4 }}>MCP access</h2>
        <p className="small" style={{ color: "var(--ink-2)", marginBottom: 8 }}>
          Which folders Claude Desktop and Claude Code can search through
          SecureFolio. Read-only. Private folders are never available.
        </p>
        {(sources.data ?? []).map((source) => (
          <Row
            key={source.id}
            title={source.name}
            desc={<span className="mono">{source.path}</span>}
          >
            {source.sensitivity === "private" ? (
              <PrivateBadge label="Private · never" />
            ) : (
              <Switch
                hideLabel
                label={`MCP access for ${source.name}`}
                checked={source.mcp_enabled}
                onChange={(value) => {
                  void setMcp.run(source.id, value);
                }}
              />
            )}
          </Row>
        ))}
        {(setMcp.error ?? setDefault.error) && (
          <p role="alert" className="small">
            {setMcp.error ?? setDefault.error}
          </p>
        )}
        <Row
          title="Claude Desktop extension"
          desc="SecureFolio answers even when its window is closed"
        >
          <button
            type="button"
            className="btn sm"
            onClick={() => {
              void installExtension().then(() => {
                toast("Opened the SecureFolio extension in Claude Desktop.");
              });
            }}
          >
            Install
          </button>
        </Row>
      </div>
    </>
  );
}
