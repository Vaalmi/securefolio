import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { ReactElement, RefObject } from "react";

import type { AskRoute } from "../api";
import { useAskRoute, routeQuery } from "../askRoute";
import { ALL_PRIVATE, optionOf, scopeOptions } from "../askScopes";
import { AskDock } from "../components/AskDock";
import { ConsentDeep, ConsentPrivate } from "../components/AskParts";
import {
  AnswerTurn,
  EmptyChat,
  Foot,
  Header,
  Pending,
} from "../components/AskThread";
import { SourcesPanel } from "../components/SourcesPanel";
import { go, withParams } from "../router";
import { Page } from "../shell";
import { useApp } from "../storeContext";
import { useAsk } from "./useAsk";

type Ask = ReturnType<typeof useAsk>;

function Cards({ ask }: Readonly<{ ask: Ask }>): ReactElement {
  const { card, askedPrivate, answerCard, changeScope, stop } = ask;
  return (
    <>
      {card?.kind === "private_in_scope" && (
        <ConsentPrivate
          titles={card.documents.map((d) => d.title)}
          onLocal={() => {
            void answerCard.run("local");
          }}
          onLeave={() => {
            void answerCard.run("leave");
          }}
        />
      )}
      {card?.kind === "deep_cost" && (
        <ConsentDeep
          card={card}
          onRun={() => {
            void answerCard.run("deep");
          }}
          onStandard={() => {
            void answerCard.run("standard");
          }}
          onCancel={() => {
            void stop.run();
          }}
        />
      )}
      {askedPrivate && (
        <ConsentPrivate
          titles={[]}
          onLocal={() => {
            void changeScope.run(askedPrivate, true);
          }}
          onLeave={ask.dismissPrivate}
        />
      )}
    </>
  );
}

function Turns({
  ask,
  activeCite,
  onCite,
  route,
}: Readonly<{
  ask: Ask;
  activeCite: string | null;
  onCite: (key: string) => void;
  route: AskRoute | undefined; // who answers this thread's next question
}>): ReactElement {
  const deepCloud = route?.deep_cloud ?? false;
  const { thread, runView, pendingQuestion } = ask;
  const messages = ask.detail.data?.messages ?? [];
  const running = Boolean(pendingQuestion) || runView.following;
  const last = messages.at(-1);
  const waitingOn =
    pendingQuestion ??
    (running && last?.role === "user" ? last.content : undefined);
  const shown =
    running && last?.role === "user" ? messages.slice(0, -1) : messages;
  const questionOf = (at: number): string => messages[at - 1]?.content ?? "";
  return (
    <>
      {shown.map((message, at) => {
        if (message.role === "user") {
          return (
            <div key={message.id} className="q-bubble">
              {message.content}
            </div>
          );
        }
        if (!message.answer || !thread) return null;
        return (
          <AnswerTurn
            key={message.id}
            message={message}
            answer={message.answer}
            thread={thread}
            deepCloud={deepCloud}
            activeCite={activeCite}
            onCite={onCite}
            onRetry={() => {
              void ask.send(questionOf(at));
            }}
            onDeep={() => {
              void ask.send(questionOf(at), "deep");
            }}
            onAskLater={() => {
              ask.askLater(questionOf(at));
            }}
            onIncludePrivate={() => {
              void ask.changeScope.run(ALL_PRIVATE, false);
            }}
          />
        );
      })}
      {running && (
        <Pending
          question={waitingOn}
          steps={runView.steps}
          text={runView.text}
        />
      )}
    </>
  );
}

const NOT_FINISHED = "This answer couldn't be finished.";

function RunFailure({ ask }: Readonly<{ ask: Ask }>): ReactElement | null {
  const failed = ask.detail.data?.last_failure;
  if (!failed) return null;
  const { question } = failed;
  return (
    <div role="alert" className="stack" style={{ gap: 8 }}>
      <p className="small">{failed.error ?? NOT_FINISHED}</p>
      {question && (
        <div>
          <button
            type="button"
            className="btn sm"
            onClick={() => {
              void ask.send(question);
            }}
          >
            Ask again
          </button>
        </div>
      )}
    </div>
  );
}

// Within this distance of the end, the owner is reading the newest turn.
const NEAR_END_PX = 48;

// The chat opens at its newest turn, and goes there again when a question starts.
// Anything else that grows it, an answer being written or finishing, keeps the
// newest turn in view only if the owner hasn't scrolled up to read. Returns the ref
// for the pane that scrolls.
function useNewestInView(
  opened: boolean, // the chat's turns have loaded
  asking: boolean, // a question is being answered
): RefObject<HTMLDivElement | null> {
  const pane = useRef<HTMLDivElement>(null);
  const atEnd = useRef(true);
  useLayoutEffect(() => {
    const element = pane.current;
    if (!opened || !element) return;
    element.scrollTop = element.scrollHeight;
    atEnd.current = true;
  }, [opened]);
  useLayoutEffect(() => {
    const element = pane.current;
    if (!asking || !element) return;
    element.scrollTop = element.scrollHeight;
    atEnd.current = true;
  }, [asking]);
  useEffect(() => {
    const element = pane.current;
    const content = element?.firstElementChild;
    if (!element || !content) return;
    // Only scrolling up stops the following: content that grew past a jump to the
    // end hasn't moved the owner.
    let lastTop = element.scrollTop;
    const onScroll = (): void => {
      const left =
        element.scrollHeight - element.scrollTop - element.clientHeight;
      const up = element.scrollTop < lastTop;
      atEnd.current = left < NEAR_END_PX || (atEnd.current && !up);
      lastTop = element.scrollTop;
    };
    const grew = new ResizeObserver(() => {
      if (atEnd.current) element.scrollTop = element.scrollHeight;
    });
    element.addEventListener("scroll", onScroll);
    grew.observe(content);
    return () => {
      element.removeEventListener("scroll", onScroll);
      grew.disconnect();
    };
  }, []);
  return pane;
}

function placeholderOf(count: number): string {
  return count > 0 ? "Ask a follow-up" : "Ask a question";
}

function errorOf(ask: Ask): string | undefined {
  return (
    ask.failure ??
    ask.stop.error ??
    ask.answerCard.error ??
    ask.changeScope.error ??
    ask.detail.error
  );
}

// Who answers this thread's next question, in its mode and scope.
function useThreadRoute(ask: Ask) {
  const app = useApp();
  return useAskRoute(routeQuery(app.mode, ask.shownScope, ask.thread?.id));
}

export function Ask({ id }: Readonly<{ id: string }>): ReactElement {
  const ask = useAsk(id);
  const { route, thread, shownScope } = ask;
  const messages = ask.detail.data?.messages ?? [];
  const error = errorOf(ask);
  const [tab, setTab] = useState(route.params.get("tab") ?? "cited");
  const activeCite = route.params.get("cite");
  const onCite = (key: string): void => {
    go(withParams(route, key ? { cite: key, panel: "1" } : { cite: null }));
  };
  const options = scopeOptions(shownScope, ask.collections.data ?? []);
  const askRoute = useThreadRoute(ask);
  const running = Boolean(ask.pendingQuestion) || ask.runView.following;
  const pane = useNewestInView(messages.length > 0, running);
  return (
    <Page
      paneRef={pane}
      title={thread?.title ?? "New chat"}
      titleExtra={
        thread ? (
          <Header thread={thread} onChanged={ask.detail.reload} />
        ) : undefined
      }
      rightLabel="Sources"
      paneClass="ask-pane"
      right={
        <SourcesPanel
          messages={messages}
          activeKey={activeCite}
          onCite={onCite}
          tab={tab}
          setTab={setTab}
        />
      }
    >
      <div
        style={{ display: "flex", flexDirection: "column", minHeight: "100%" }}
      >
        <div className="thread" style={{ flex: 1, width: "100%" }}>
          {messages.length === 0 && !running && !ask.card && (
            <EmptyChat scope={shownScope} />
          )}
          <Turns
            ask={ask}
            activeCite={activeCite}
            onCite={onCite}
            route={askRoute.data}
          />
          <Cards ask={ask} />
          {error && (
            <p role="alert" className="small">
              {error}
            </p>
          )}
          {!running && <RunFailure ask={ask} />}
        </div>
        <div
          className="ask-dock"
          style={{
            position: "sticky",
            bottom: 0,
            background: "var(--page)",
            maxWidth: 792,
            margin: "0 auto",
            width: "100%",
          }}
        >
          <AskDock
            scope={optionOf(shownScope)}
            scopeOptions={options.map((o) => o.option)}
            onScope={(option) => {
              const chosen = options.find((o) => o.option.id === option.id);
              if (chosen) void ask.changeScope.run(chosen.scope, false);
            }}
            threadPrivate={ask.threadPrivate}
            route={askRoute.data}
            routeError={askRoute.error}
            running={running}
            onSend={(text) => {
              void ask.send(text);
            }}
            onStop={() => {
              void ask.stop.run();
            }}
            placeholder={placeholderOf(messages.length)}
            focusOnOpen={!thread && !ask.incoming}
          />
          <Foot route={askRoute.data} />
        </div>
      </div>
    </Page>
  );
}
