import { useState } from "react";
import type { ReactElement } from "react";
import { Lock, MessageSquare, Pin } from "lucide-react";

import { listThreads, THREAD_PAGE } from "../api";
import type { Thread } from "../api";
import { fmtMoment } from "../format";
import { ChatMenu } from "../components/ChatMenu";
import { Page } from "../shell";
import { PrivateBadge } from "../ui";
import { useAction } from "../useAction";
import { useLoad } from "../useLoad";
import { useThreadsChanged } from "../useThreadsChanged";

export function Chats(): ReactElement {
  const threads = useLoad(() => listThreads(), "threads");
  const [older, setOlder] = useState<Thread[]>([]);
  const [lastPage, setLastPage] = useState<number>();
  const shown = [...(threads.data ?? []), ...older];
  const more = useAction(async () => {
    const page = await listThreads(shown.length);
    setOlder((had) => [...had, ...page]);
    setLastPage(page.length);
  });
  useThreadsChanged(() => {
    setOlder([]);
    setLastPage(undefined);
    threads.reload();
  });
  const mayHaveMore = (lastPage ?? threads.data?.length) === THREAD_PAGE;
  return (
    <Page title="Chats">
      {threads.error && <p role="alert">{threads.error}</p>}
      <div className="card" style={{ padding: 8, maxWidth: 760 }}>
        {threads.data?.length === 0 && (
          <p className="muted small" style={{ padding: 8 }}>
            No chats yet.
          </p>
        )}
        {shown.map((thread) => (
          <div key={thread.id} className="list-row">
            <a
              href={`#/ask/${thread.id}`}
              className="hstack"
              style={{
                flex: 1,
                minWidth: 0,
                gap: "var(--s3)",
                textDecoration: "none",
                color: "inherit",
              }}
            >
              {thread.private_tainted ? (
                <Lock size={14} aria-hidden="true" />
              ) : (
                <MessageSquare size={14} aria-hidden="true" />
              )}
              <span style={{ flex: 1, minWidth: 0 }}>
                <span className="title" style={{ display: "block" }}>
                  {thread.title}
                </span>
                <span className="small muted">{thread.scope.label}</span>
              </span>
              {thread.pinned && <Pin size={12} aria-label="Pinned" />}
              {thread.private_tainted && <PrivateBadge />}
              <span className="small muted">
                {fmtMoment(thread.updated_at)}
              </span>
            </a>
            <ChatMenu thread={thread} label={`Actions for ${thread.title}`} />
          </div>
        ))}
        {mayHaveMore && (
          <button
            type="button"
            className="btn sm ghost"
            onClick={() => {
              void more.run();
            }}
          >
            Show older chats
          </button>
        )}
        {more.error && <p role="alert">{more.error}</p>}
      </div>
    </Page>
  );
}
