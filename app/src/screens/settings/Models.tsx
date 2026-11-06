import type { ReactElement } from "react";
import { AlertTriangle } from "lucide-react";

import { getSettings, listTaskModels, updateSettings } from "../../api";
import type { CloudModel, TaskModelList, TaskModels } from "../../api";
import { useAction } from "../../useAction";
import { useLoad } from "../../useLoad";
import { KeysCard } from "./Keys";
import { TASKS } from "./keyLooks";
import { Row } from "./Row";

// Settings, Models for each task.

function dollars(usd: number): string {
  return `$${usd.toFixed(2)}`;
}

function price(model: CloudModel): string {
  if (model.input_usd === null || model.output_usd === null)
    return "No known price";
  return `${dollars(model.input_usd)} in · ${dollars(model.output_usd)} out per million tokens`;
}

function optionLabel(model: CloudModel): string {
  const cost =
    model.input_usd === null || model.output_usd === null
      ? "no known price"
      : `${dollars(model.input_usd)} in, ${dollars(model.output_usd)} out`;
  const notes = [
    model.measured ? "" : " · not measured",
    model.listed ? "" : " · no longer listed",
  ].join("");
  return `${model.name} · ${cost}${notes}`;
}

type Provider = TaskModels["providers"][number];

// What a provider with no ready key lacks: the key, or the ID its test found it needs.
function missing(provider: Provider): string {
  return provider.needs_id
    ? `add its ${provider.id_label ?? "ID"}`
    : "add a key";
}

function article(name: string): string {
  return /^[AEIOU]/i.test(name) ? "an" : "a";
}

function NoKeyNote({
  task,
  provider,
}: Readonly<{
  task: TaskModelList["task"];
  provider: Provider;
}>): ReactElement {
  const needs = provider.needs_id
    ? `The ${provider.name} key needs its ${provider.id_label ?? "ID"}.`
    : `Needs ${article(provider.name)} ${provider.name} key.`;
  return (
    <span className="hstack needs-key" style={{ gap: 4 }}>
      <AlertTriangle size={12} aria-hidden="true" />
      {task === "page"
        ? `${needs} Until then, reading a page in the cloud is unavailable; Re-read this page on your Mac still works.`
        : `${needs} Until then, this runs on the local model.`}
    </span>
  );
}

function TaskNotes({
  list,
  all,
}: Readonly<{ list: TaskModelList; all: TaskModels }>): ReactElement {
  const chosen = list.models.find((model) => model.id === list.chosen);
  const provider = all.providers.find((p) => p.id === chosen?.provider);
  return (
    <>
      <span>{TASKS[list.task].desc}</span>
      {chosen && (
        <span className="hstack wrap" style={{ gap: 6 }}>
          <span className="mono">{price(chosen)}</span>
          {!chosen.measured && (
            <span
              className="badge outline"
              title="npm run eval can measure it before you rely on it"
            >
              Not measured
            </span>
          )}
        </span>
      )}
      {chosen && !chosen.listed && (
        <span className="hstack needs-key" style={{ gap: 4 }}>
          <AlertTriangle size={12} aria-hidden="true" />
          {chosen.name} is no longer in OpenRouter's list. It keeps its last
          price until you choose another model.
        </span>
      )}
      {provider && !provider.key_ready && (
        <NoKeyNote task={list.task} provider={provider} />
      )}
      {list.task === "helper" && (
        <span>
          Cloud cards use the provider's batch price where it has one: Anthropic
          and OpenAI. Not OpenRouter.
        </span>
      )}
    </>
  );
}

function TaskSelect({
  list,
  all,
  onChoose,
  saving,
}: Readonly<{
  list: TaskModelList;
  all: TaskModels;
  onChoose: (model: string) => void;
  saving: boolean; // a choice still being saved: no other is sent past it
}>): ReactElement {
  const id = `m-${list.field}`;
  return (
    <>
      <label className="sr-only" htmlFor={id}>
        Model for {TASKS[list.task].title}
      </label>
      <select
        id={id}
        className="select"
        style={{ width: 300 }}
        value={list.chosen}
        disabled={saving}
        onChange={(event) => {
          onChoose(event.target.value);
        }}
      >
        {all.providers.map((provider) => {
          const models = list.models.filter((m) => m.provider === provider.id);
          if (models.length === 0) return null;
          return (
            <optgroup
              key={provider.id}
              label={
                provider.key_ready
                  ? provider.name
                  : `${provider.name}: ${missing(provider)} to choose`
              }
            >
              {models.map((model) => (
                <option
                  key={model.id}
                  value={model.id}
                  disabled={!provider.key_ready && model.id !== list.chosen}
                >
                  {optionLabel(model)}
                </option>
              ))}
            </optgroup>
          );
        })}
        {list.may_run_locally && (
          <optgroup label="On this Mac">
            <option value="local">Local model</option>
          </optgroup>
        )}
      </select>
    </>
  );
}

export function Models(): ReactElement {
  const settings = useLoad(getSettings, "settings");
  const lists = useLoad(listTaskModels, "task-models");
  const change = useAction(updateSettings, lists.reload);
  const all = lists.data;
  return (
    <>
      <KeysCard onChanged={lists.reload} />
      <div className="card">
        <h2 style={{ marginBottom: 4 }}>Models for each task</h2>
        <p className="small" style={{ color: "var(--ink-2)", marginBottom: 8 }}>
          Changes take effect from the next question, without a restart; a chat
          already answering finishes on its model. Prices are per million
          tokens. Prompt caching is used wherever a model offers it; estimates
          assume it saves nothing.
        </p>
        {all?.tasks.map((list) => (
          <Row
            key={list.field}
            title={TASKS[list.task].title}
            desc={<TaskNotes list={list} all={all} />}
          >
            <TaskSelect
              list={list}
              all={all}
              saving={change.busy}
              onChoose={(model) => {
                void change.run({ [list.field]: model });
              }}
            />
          </Row>
        ))}
        <Row title="Private and Offline answers" desc="Always on this Mac">
          <span className="small">
            Local model ({settings.data?.local_answers})
          </span>
        </Row>
        {(change.error ?? lists.error) && (
          <p role="alert" className="small">
            {change.error ?? lists.error}
          </p>
        )}
        <p className="small muted" style={{ marginTop: 8 }}>
          A model shows here only for tasks it can do: reading a page needs one
          that takes images, and the helpers need one that returns structured
          answers. OpenRouter's list is its own, with its published prices.
        </p>
      </div>
    </>
  );
}
