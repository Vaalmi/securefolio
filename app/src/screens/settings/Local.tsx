import type { ReactElement } from "react";
import { WifiOff } from "lucide-react";

import { LocalModelRow, SearchModelsRow } from "../../components/ModelRows";
import { getModels, getSettings, setOffline } from "../../api";
import type { ModelsStatus } from "../../api";
import { isDownloading } from "../../downloads";
import { Switch } from "../../ui";
import { useAction } from "../../useAction";
import { useLoad } from "../../useLoad";
import { InstalledCard, JobsCard, PullCard } from "./LocalModels";
import { Row } from "./Row";

const POLL_MS = 1000;
const NOT_READY =
  "Available once the search, OCR and page-reading models and the local model are installed. Download them above.";
const READY =
  "Nothing connects off this Mac, from any part of SecureFolio. Local AI answers on this Mac, with a Local AI badge. Connections to Ollama on this Mac still work.";

function OfflineCard({
  ready,
  on,
  onChanged,
}: Readonly<{
  ready: boolean;
  on: boolean;
  onChanged: () => void;
}>): ReactElement {
  const offline = useAction(setOffline, onChanged);
  return (
    <div className="card">
      <h2 style={{ marginBottom: 8 }}>Offline</h2>
      <Row
        title="Offline mode"
        desc={<span id="offline-why">{ready ? READY : NOT_READY}</span>}
      >
        <Switch
          hideLabel
          label="Offline mode"
          checked={on}
          disabled={!ready && !on}
          describedBy="offline-why"
          onChange={(value) => {
            void offline.run(value);
          }}
        />
      </Row>
      {on && (
        <p className="small hstack" role="status" style={{ gap: 6 }}>
          <WifiOff size={14} aria-hidden="true" /> Offline is on. Cloud models,
          MCP over the network and cloud organizing are paused.
        </p>
      )}
      {offline.error && (
        <p role="alert" className="small">
          {offline.error}
        </p>
      )}
    </div>
  );
}

function LocalCards({
  data,
  on,
  onChanged,
}: Readonly<{
  data: ModelsStatus;
  on: boolean;
  onChanged: () => void;
}>): ReactElement {
  const { running } = data.ollama;
  // A model some job can use: until there is one, the download that suits this Mac,
  // or what Ollama needs first. The jobs, the installed list and the download field
  // show whenever Ollama is running, even with nothing installed, so a removed
  // model's warning and what's left stay in view.
  const usable = running && data.installed.local.some((m) => m.jobs.length > 0);
  return (
    <>
      {!usable && (
        <div className="card">
          <h2 style={{ marginBottom: 8 }}>Local model</h2>
          <LocalModelRow models={data} onChanged={onChanged} />
        </div>
      )}
      {running && (
        <>
          <JobsCard models={data} onChanged={onChanged} />
          <InstalledCard models={data} />
          <PullCard models={data} offline={on} onStarted={onChanged} />
        </>
      )}
      <div className="card">
        <h2 style={{ marginBottom: 8 }}>Search and OCR models</h2>
        <SearchModelsRow models={data} onChanged={onChanged} />
      </div>
    </>
  );
}

export function Local(): ReactElement {
  const models = useLoad(getModels, "models", {
    every: POLL_MS,
    while: isDownloading,
  });
  const settings = useLoad(getSettings, "settings");
  const data = models.data;
  const ready =
    data?.search.state === "installed" &&
    data.page.state === "installed" &&
    data.local.installed;
  const on = settings.data?.offline === true;
  return (
    <>
      {models.error && <p role="alert">{models.error}</p>}
      {data && <LocalCards data={data} on={on} onChanged={models.reload} />}
      <OfflineCard ready={ready} on={on} onChanged={settings.reload} />
    </>
  );
}
