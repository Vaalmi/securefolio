import { useEffect, useRef, useState } from "react";

import {
  askWhenIndexed,
  createThread,
  getThread,
  listCollections,
  resumeThread,
  sendMessage,
  setThreadScope,
  stopThread,
} from "../api";
import type { Mode, Run, RunCard, Thread, ThreadScope } from "../api";
import { scopeFromParam } from "../askScopes";
import { go, useRoute } from "../router";
import { useApp } from "../storeContext";
import { toast } from "../toast";
import { useAction } from "../useAction";
import { useLoad } from "../useLoad";
import { useRun } from "../useRun";

function openCard(
  live: RunCard | null,
  run: Run | null | undefined,
  answered: RunCard | null,
): RunCard | null {
  const stored = run?.state === "paused" ? run.card : null;
  const shown = live ?? stored;
  return shown && shown.id !== answered?.id ? shown : null;
}

function isPrivate(thread: Thread | undefined, scope: ThreadScope): boolean {
  if (thread) return thread.private_tainted;
  return scope.kind === "library" && scope.include_private;
}

// Everything the Ask screen does: loading its chat, following a run, what the owner
// answers. The screen draws it.
export function useAsk(id: string) {
  const route = useRoute();
  const app = useApp();
  const isNew = id === "new";
  const detail = useLoad(
    () => (isNew ? Promise.resolve(undefined) : getThread(id)),
    id,
  );
  const collections = useLoad(listCollections, "collections");
  const [answeredCard, setAnsweredCard] = useState<RunCard | null>(null);
  const [liveCard, setLiveCard] = useState<RunCard | null>(null);
  const [askedPrivate, setAskedPrivate] = useState<ThreadScope | null>(null);
  const [pendingQuestion, setPendingQuestion] = useState<string>();
  const [failure, setFailure] = useState<string>();
  const [newScope, setNewScope] = useState<ThreadScope | null>(null);
  const attached = useRef<string | null>(null);
  const run = useRun((finished) => {
    if (finished) setPendingQuestion(undefined);
    else attached.current = null;
    detail.reload();
  }, setLiveCard);
  const thread = detail.data?.thread;
  const activeRun = detail.data?.active_run;
  const card = openCard(liveCard, activeRun, answeredCard);
  const shownScope =
    thread?.scope ??
    newScope ??
    scopeFromParam(route.params.get("scope"), collections.data ?? []);
  const threadPrivate = isPrivate(thread, shownScope);

  useEffect(() => {
    if (activeRun?.state === "running" && attached.current !== activeRun.id) {
      attached.current = activeRun.id;
      run.follow(activeRun.id);
    }
  }, [activeRun, run]);

  const send = async (text: string, mode: Mode = app.mode): Promise<void> => {
    setFailure(undefined);
    setLiveCard(null);
    setPendingQuestion(text);
    try {
      const target = thread ?? (await createThread(shownScope, mode));
      const started = await sendMessage(target.id, text, mode);
      attached.current = started.id;
      run.follow(started.id);
      if (!thread) go(`/ask/${target.id}`);
    } catch (problem) {
      setPendingQuestion(undefined);
      setFailure(problem instanceof Error ? problem.message : String(problem));
    }
  };

  const incoming = route.params.get("q");
  const startedFor = useRef<string | null>(null);
  useEffect(() => {
    if (
      isNew &&
      incoming &&
      startedFor.current !== incoming &&
      collections.data
    ) {
      startedFor.current = incoming;
      void send(incoming);
    }
    // Runs once per incoming question.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isNew, incoming, collections.data]);

  const answerCard = useAction(
    async (choice: "local" | "leave" | "deep" | "standard") => {
      if (!card || !thread) return;
      const resumed = await resumeThread(thread.id, card.id, choice);
      setAnsweredCard(card);
      setLiveCard(null);
      attached.current = resumed.id;
      run.follow(resumed.id);
    },
  );
  const stop = useAction(async () => {
    if (thread) await stopThread(thread.id);
    run.clear();
    if (card) setAnsweredCard(card);
    setLiveCard(null);
    setPendingQuestion(undefined);
    attached.current = null;
    detail.reload();
    toast("Stopped. The chat is back to how it was before that question.");
  });
  const changeScope = useAction(
    async (scope: ThreadScope, consented: boolean) => {
      if (!thread) {
        setNewScope(scope);
        return;
      }
      const answer = await setThreadScope(thread.id, scope, consented);
      if (answer.needs_consent) {
        setAskedPrivate(scope);
        return;
      }
      setAskedPrivate(null);
      detail.reload();
    },
  );
  const askLater = (text: string): void => {
    if (!thread) return;
    void askWhenIndexed(thread.id, text).then(() => {
      toast("SecureFolio will ask again when indexing finishes.");
    });
  };
  return {
    route,
    thread,
    detail,
    collections,
    card,
    askedPrivate,
    dismissPrivate: () => {
      setAskedPrivate(null);
    },
    pendingQuestion,
    runView: run.view,
    shownScope,
    threadPrivate,
    incoming,
    send,
    answerCard,
    stop,
    changeScope,
    askLater,
    failure,
  };
}
