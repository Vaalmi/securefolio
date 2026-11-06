import { useEffect, useState } from "react";
import type { ReactElement } from "react";
import { ArrowLeft, Star } from "lucide-react";

import {
  cloudReadPage,
  getChunkElement,
  getCitedElement,
  getCloudPageCost,
  getDocument,
  markDocumentOpened,
  setDocumentStar,
} from "../api";
import type {
  DocumentDetail,
  DocumentRow,
  Locator,
  PageCost,
  StoredElement,
} from "../api";
import { ConfirmLeaving, MoreMenu } from "../components/DocumentActions";
import type { Confirm } from "../components/DocumentActions";
import { AskPanel, RelatedPanel } from "../components/DocumentAsk";
import { CardPanel } from "../components/DocumentPanels";
import {
  CloudPageModal,
  NotDownloaded,
  OriginalGone,
  Pager,
  Viewer,
} from "../components/DocumentViewer";
import { NotesPanel } from "../components/NotesPanel";
import { pageOf } from "../format";
import { useRoute } from "../router";
import { Page } from "../shell";
import { toast } from "../toast";
import { KindIcon, PendingBadge, PrivateBadge, StateBadge, Tabs } from "../ui";
import { useAction } from "../useAction";
import { useLoad } from "../useLoad";
import { StoredPage, StoredTextView } from "../viewers/StoredText";

function PagedBody({
  detail,
  cited,
  locator,
  page,
  setPicked,
  onChanged,
  onRemove,
}: Readonly<{
  detail: DocumentDetail;
  cited: StoredElement | null | undefined;
  locator: Locator | undefined;
  page: number;
  setPicked: (page: number) => void;
  onChanged: () => void;
  onRemove: () => void;
}>): ReactElement {
  const doc = detail.document;
  const id = doc.id;
  const [pages, setPages] = useState(doc.pages ?? 1);
  const [zoom, setZoom] = useState(100);
  const state = detail.view_state;
  const setPage = (next: number): void => {
    setPicked(Math.max(1, Math.min(pages, next)));
  };
  if (state === "not_downloaded") {
    return <NotDownloaded id={id} onChanged={onChanged} />;
  }
  if (state === "stored") return <StoredTextView id={id} />;
  if (state === "missing" || state === "changed") {
    return (
      <>
        <OriginalGone
          detail={detail}
          changed={state === "changed"}
          onChanged={onChanged}
          onRemove={onRemove}
        />
        {doc.file_kind === "PDF" ? (
          <>
            <Pager
              key={page}
              page={page}
              pages={pages}
              unit="Page"
              setPage={setPage}
              zoom={null}
              setZoom={setZoom}
            />
            <StoredPage id={id} page={page} />
          </>
        ) : (
          <StoredTextView id={id} />
        )}
      </>
    );
  }
  if (doc.active_revision_id === null) {
    return (
      <p className="muted" role="status">
        {doc.status_reason ?? "This document isn't indexed yet."}
      </p>
    );
  }
  return (
    <Viewer
      detail={detail}
      page={page}
      setPage={setPage}
      pages={pages}
      setPages={setPages}
      zoom={zoom}
      setZoom={setZoom}
      citedId={cited?.id ?? null}
      citedOrdinal={cited?.ordinal ?? null}
      locator={locator}
    />
  );
}

function SidePanel({
  detail,
  onChanged,
  initialTab,
}: Readonly<{
  detail: DocumentDetail;
  onChanged: () => void;
  initialTab: string;
}>): ReactElement {
  const [tab, setTab] = useState(initialTab);
  return (
    <>
      <div className="panel-head" style={{ paddingRight: 48 }}>
        <h2>About this document</h2>
      </div>
      <Tabs
        idBase="doc"
        label="Document panel"
        value={tab}
        onChange={setTab}
        tabs={[
          { id: "card", label: "Card" },
          { id: "ask", label: "Ask" },
          { id: "notes", label: "Notes" },
          { id: "related", label: "Related" },
        ]}
      />
      <div
        className="panel-body"
        role="tabpanel"
        id="doc-panel"
        aria-labelledby={`doc-tab-${tab}`}
      >
        {tab === "card" && <CardPanel detail={detail} onChanged={onChanged} />}
        {tab === "ask" && <AskPanel detail={detail} />}
        {tab === "notes" && (
          <NotesPanel
            documentId={detail.document.id}
            isPrivate={detail.document.sensitivity === "private"}
          />
        )}
        {tab === "related" && <RelatedPanel id={detail.document.id} />}
      </div>
    </>
  );
}

const FOLLOW_MS = 1500;

function useCitation(
  id: string,
  citedId: string | null,
  revision: string | null,
) {
  const cited = useLoad(
    () => (citedId ? getCitedElement(id, citedId) : Promise.resolve(undefined)),
    `${citedId ?? ""}-${String(revision)}`,
  );
  const current = cited.data?.current === true;
  return {
    element: current ? cited.data?.element : null,
    stale: current ? null : cited.data?.element,
  };
}

function TitleBadges({ doc }: Readonly<{ doc: DocumentRow }>): ReactElement {
  return (
    <span className="hstack" style={{ gap: 6 }}>
      {doc.sensitivity === "private" && <PrivateBadge />}
      <StateBadge state={doc.status} />
    </span>
  );
}

function PathBar({
  detail,
}: Readonly<{ detail: DocumentDetail }>): ReactElement {
  const doc = detail.document;
  return (
    <div className="viewer-bar small muted">
      <KindIcon kind={doc.file_kind} size={14} />
      <span
        className="mono"
        style={{
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {detail.locations.at(0)?.path ?? doc.file_name}
      </span>
      <span className="spacer" />
      {detail.card?.state === "pending" && <PendingBadge />}
    </div>
  );
}

function Loaded({
  detail,
  reload,
  citedId,
}: Readonly<{
  detail: DocumentDetail;
  reload: () => void;
  citedId: string | null;
}>): ReactElement {
  const route = useRoute();
  const doc = detail.document;
  const id = doc.id;
  const state = detail.view_state;
  const { element, stale } = useCitation(id, citedId, doc.active_revision_id);
  const cost = useLoad(
    () => getCloudPageCost(id).catch(() => undefined),
    `${id}-${String(detail.cloud_page_allowed)}`,
  );
  const [picked, setPicked] = useState<number | null>(null);
  const [confirm, setConfirm] = useState<Confirm>(null);
  const locator = element?.locator;
  const page = picked ?? pageOf(locator) ?? 1;
  const star = useAction((next: boolean) => setDocumentStar(id, next), reload);
  const send = useAction(
    (shown: PageCost) =>
      cloudReadPage(id, page, shown, doc.active_revision_id ?? "").catch(
        (failed: unknown) => {
          cost.reload(); // a changed model or price: show the cost as it is now
          reload(); // and the document as it is now
          throw failed;
        },
      ),
    () => {
      setConfirm(null);
      toast(
        `Sent this page to Cloud AI (${cost.data?.model_name ?? "the page model"}). The view updates when it's read.`,
      );
      reload();
    },
  );
  useEffect(() => {
    if (locator) {
      document
        .getElementById("cited-passage")
        ?.scrollIntoView({ block: "center" });
    }
  }, [locator, page]);
  const closeConfirm = (): void => {
    setConfirm(null);
  };
  return (
    <Page
      title={doc.title}
      titleExtra={<TitleBadges doc={doc} />}
      rightLabel="Card and notes"
      right={
        <SidePanel
          detail={detail}
          onChanged={reload}
          initialTab={route.params.get("tab") ?? "card"}
        />
      }
      actions={
        <>
          <button
            type="button"
            className="btn"
            onClick={() => {
              window.history.back();
            }}
            title="Back to where you came from"
          >
            <ArrowLeft size={14} aria-hidden="true" /> Back
          </button>
          <button
            type="button"
            className="btn"
            aria-pressed={doc.starred}
            aria-label="Star"
            onClick={() => {
              void star.run(!doc.starred);
            }}
          >
            <Star
              size={14}
              aria-hidden="true"
              fill={doc.starred ? "currentColor" : "none"}
            />
          </button>
          <MoreMenu
            detail={detail}
            page={page}
            cost={cost.data}
            onConfirm={setConfirm}
            onChanged={reload}
          />
        </>
      }
    >
      <div className="viewer">
        <PathBar detail={detail} />
        {stale && (
          <p className="small muted" role="status">
            This citation is from an earlier version. {stale.text}
          </p>
        )}
        <PagedBody
          detail={detail}
          key={doc.active_revision_id ?? state}
          cited={element}
          locator={locator}
          page={page}
          setPicked={setPicked}
          onChanged={reload}
          onRemove={() => {
            setConfirm("remove");
          }}
        />
        {locator && <p className="small muted">The cited passage is marked.</p>}
      </div>
      {confirm === "cloud" && cost.data && (
        <CloudPageModal
          cost={cost.data}
          page={page}
          error={send.error}
          onClose={closeConfirm}
          onSend={() => {
            if (cost.data) void send.run(cost.data);
          }}
        />
      )}
      {(confirm === "remove" || confirm === "trash") && (
        <ConfirmLeaving id={id} kind={confirm} onClose={closeConfirm} />
      )}
    </Page>
  );
}

export function DocumentView({ id }: Readonly<{ id: string }>): ReactElement {
  const route = useRoute();
  // A securefolio:// link names a passage: open at its first element.
  const chunk = route.params.get("chunk");
  const fromLink = useLoad(
    () =>
      chunk
        ? getChunkElement(id, chunk).then((found) => found.element_id)
        : Promise.resolve(null),
    chunk ?? "",
  );
  // While a job works on it, keep looking, so a new revision shows with no refresh.
  const detail = useLoad(() => getDocument(id), id, {
    every: FOLLOW_MS,
    while: (data) => data.has_live_work,
  });
  useEffect(() => {
    void markDocumentOpened(id).catch(() => undefined);
  }, [id]);
  if (detail.error) {
    return (
      <Page title="Document">
        <div className="empty" role="alert">
          <p>{detail.error}</p>
          <button type="button" className="btn sm" onClick={detail.reload}>
            Try again
          </button>
        </div>
      </Page>
    );
  }
  if (!detail.data)
    return (
      <Page title="Document">
        <p className="muted" role="status">
          Loading…
        </p>
      </Page>
    );
  return (
    <Loaded
      detail={detail.data}
      reload={detail.reload}
      citedId={route.params.get("el") ?? fromLink.data ?? null}
    />
  );
}
