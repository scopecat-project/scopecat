import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useLocationUrl } from "../../lib/navigation";
import { Button, Field, Input, Notice } from "../../ui/Controls";
import { detailCard, pageContent, pageTitle, sectionTitle, secondaryButton } from "../../ui/styles";
import { apparatusLocation, selectedRef, selectObject } from "./apparatus-location";
import {
  attachmentUrl,
  findObjects,
  getObservation,
  objectHistory,
  resolveObject,
  type ObjectRef,
  type ObjectRevision,
  type Observation,
} from "./apparatus-api";

export function ApparatusHistory({
  projectId,
  daemonUnavailable,
  onOpenRun,
}: {
  projectId: string;
  daemonUnavailable: boolean;
  onOpenRun: (runId: string) => void;
}) {
  const params = useLocationUrl().searchParams;
  const space = params.get("apparatus-space");
  const wrongSpace = space !== null && space !== projectId;
  const query = params.get("apparatus-query") ?? "";
  const before = cursor(params.get("apparatus-objects-before"));
  const ref = selectedRef(params);
  const objects = useQuery({
    queryKey: ["apparatus", projectId, "objects", query, before],
    queryFn: ({ signal }) => findObjects(query, before, signal),
    enabled: !daemonUnavailable && !wrongSpace,
  });
  return (
    <section className={pageContent} aria-label="实验装置历史">
      <h2 className={pageTitle}>实验装置历史</h2>
      <p>
        实验装置对象（apparatus object）是线路、线缆、端口组或其他非样品部件，例如冰箱输入线。
        它不等于设备与驱动中的可控仪器，也不替代样品目录。
      </p>
      <Notice>历史记录不代表当前接线或校准有效性。这里只搜索当前数据空间的目录。</Notice>
      {wrongSpace ? (
        <Notice role="alert">
          此历史位置属于另一个数据空间，未加载同名对象。
          <Button
            onClick={() =>
              apparatusLocation({
                space: projectId,
                catalog: undefined,
                object: undefined,
                revision: undefined,
                hash: undefined,
                observation: undefined,
                before: undefined,
                query: undefined,
                "objects-before": undefined,
              })
            }
          >
            浏览当前数据空间
          </Button>
        </Notice>
      ) : (
        <>
          <Search key={query} query={query} unavailable={daemonUnavailable} projectId={projectId} />
          {daemonUnavailable && <Notice role="status">服务离线，历史读取暂不可用。</Notice>}
          {objects.error && <Notice role="alert">对象搜索失败：{objects.error.message}</Notice>}
          <div className="grid gap-4 min-[1000px]:grid-cols-[minmax(240px,1fr)_minmax(0,2fr)]">
            <aside className={`${detailCard} self-start`} aria-label="实验装置对象搜索结果">
              <h3 className={sectionTitle}>对象</h3>
              {objects.isFetching && <p role="status">正在搜索…</p>}
              {!objects.error && objects.data?.items.length === 0 && <p>没有符合条件的对象。</p>}
              <ul className="space-y-3">
                {objects.data?.items.map((object) => (
                  <li key={JSON.stringify(object.ref)}>
                    <Button
                      disabled={daemonUnavailable}
                      onClick={() => selectObject(projectId, object.ref)}
                    >
                      {object.content.name} · {object.ref.object_id}
                    </Button>
                    <p className="mt-2 text-xs break-all">目录：{object.ref.catalog_id}</p>
                  </li>
                ))}
              </ul>
              <div className="mt-3 flex gap-2">
                {before && (
                  <Button onClick={() => apparatusLocation({ "objects-before": undefined })}>
                    对象首页
                  </Button>
                )}
                {objects.data?.next_cursor != null && (
                  <Button
                    disabled={objects.isFetching || daemonUnavailable}
                    onClick={() =>
                      apparatusLocation({ "objects-before": objects.data!.next_cursor! })
                    }
                  >
                    更多对象
                  </Button>
                )}
              </div>
            </aside>
            {ref ? (
              <ObjectHistory
                key={JSON.stringify([projectId, ref])}
                projectId={projectId}
                subject={ref}
                unavailable={daemonUnavailable}
                onOpenRun={onOpenRun}
              />
            ) : (
              <p>
                {params.has("apparatus-object")
                  ? "对象引用不完整，无法读取。请从搜索结果明确选择对象。"
                  : "选择一个对象查看历史。"}
              </p>
            )}
          </div>
        </>
      )}
    </section>
  );
}

function Search({
  query,
  projectId,
  unavailable,
}: {
  query: string;
  projectId: string;
  unavailable: boolean;
}) {
  const [text, setText] = useState(query);
  return (
    <form
      className="flex flex-wrap items-end gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        apparatusLocation({ space: projectId, query: text.trim(), "objects-before": undefined });
      }}
    >
      <Field>
        查找对象
        <Input
          aria-label="查找实验装置对象"
          maxLength={200}
          value={text}
          placeholder="名称、别名或对象 ID"
          onChange={(event) => setText(event.target.value)}
        />
      </Field>
      <Button type="submit" disabled={unavailable}>
        搜索对象
      </Button>
    </form>
  );
}

function ObjectHistory({
  projectId,
  subject,
  unavailable,
  onOpenRun,
}: {
  projectId: string;
  subject: ObjectRef;
  unavailable: boolean;
  onOpenRun: (id: string) => void;
}) {
  const params = useLocationUrl().searchParams;
  const before = cursor(params.get("apparatus-before"));
  const observationId = params.get("apparatus-observation");
  const object = useQuery({
    queryKey: ["apparatus", projectId, "description", subject],
    queryFn: ({ signal }) => resolveObject(subject, signal),
    enabled: !unavailable,
  });
  const history = useQuery({
    queryKey: ["apparatus", projectId, subject.catalog_id, subject.object_id, "history", before],
    queryFn: ({ signal }) => objectHistory(subject.object_id, before, signal),
    enabled: !unavailable && object.isSuccess,
  });
  return (
    <div className="min-w-0 space-y-4">
      <div className={detailCard}>
        {object.error && <Notice role="alert">对象描述不可用：{object.error.message}</Notice>}
        {object.isPending && <p>正在读取精确对象描述…</p>}
        {object.data && <Description revision={object.data} />}
      </div>
      {object.isSuccess && (
        <>
          <div className={detailCard}>
            <h3 className={sectionTitle}>观察记录</h3>
            <p>按记录顺序分页；游标不表示物理时间。测量时间与记录时间分别保留。</p>
            <p>更正从新记录链接到旧记录；本页不判断哪些旧记录有后继，也不声明当前有效记录。</p>
            {history.error && <Notice role="alert">观察读取失败：{history.error.message}</Notice>}
            {history.isFetching && <p role="status">正在读取观察…</p>}
            {history.data?.items.length === 0 && <p>此页没有观察记录。</p>}
            <ul className="space-y-3">
              {history.data?.items.map((item) => (
                <li key={item.id}>
                  <Button
                    disabled={unavailable}
                    onClick={() => apparatusLocation({ observation: item.id })}
                  >
                    {item.draft.title} · {item.id}
                  </Button>
                  <Times observation={item} />
                </li>
              ))}
            </ul>
            <div className="mt-3 flex gap-2">
              {before && (
                <Button onClick={() => apparatusLocation({ before: undefined })}>观察首页</Button>
              )}
              {history.data?.next_cursor != null && (
                <Button
                  disabled={history.isFetching || unavailable}
                  onClick={() => apparatusLocation({ before: history.data!.next_cursor! })}
                >
                  更早观察
                </Button>
              )}
            </div>
          </div>
          {observationId && (
            <ObservationDetail
              key={JSON.stringify([projectId, subject, observationId])}
              projectId={projectId}
              subject={subject}
              id={observationId}
              unavailable={unavailable}
              onOpenRun={onOpenRun}
            />
          )}
        </>
      )}
    </div>
  );
}

function ObservationDetail({
  projectId,
  subject,
  id,
  unavailable,
  onOpenRun,
}: {
  projectId: string;
  subject: ObjectRef;
  id: string;
  unavailable: boolean;
  onOpenRun: (id: string) => void;
}) {
  const observation = useQuery({
    queryKey: ["apparatus", projectId, "observation", id, subject.catalog_id, subject.object_id],
    queryFn: async ({ signal }) => {
      const item = await getObservation(id, signal);
      if (
        item.draft.subject.catalog_id !== subject.catalog_id ||
        item.draft.subject.object_id !== subject.object_id
      )
        throw new Error("观察不属于所选目录和对象。");
      return item;
    },
    enabled: !unavailable,
  });
  const item = observation.data;
  const description = useQuery({
    queryKey: ["apparatus", projectId, "description", item?.draft.subject],
    queryFn: ({ signal }) => resolveObject(item!.draft.subject, signal),
    enabled: !!item && !unavailable,
  });
  return (
    <article className={`${detailCard} space-y-3`} aria-label="观察详情">
      {observation.error && (
        <Notice role="alert">
          观察不可用：{id} · {observation.error.message}
        </Notice>
      )}
      {observation.isPending && <p>正在读取观察…</p>}
      {item && (
        <>
          <h3 className={sectionTitle}>{item.draft.title}</h3>
          <p className="break-all">
            观察 ID：{item.id} · 记录人：{item.draft.actor}
          </p>
          <Times observation={item} />
          <h4>观察当时的对象描述</h4>
          {description.error && (
            <Notice role="alert">历史描述不可用：{description.error.message}</Notice>
          )}
          {description.isPending && <p>正在解析历史描述…</p>}
          {description.data && <Description revision={description.data} />}
          <h4>已知条件</h4>
          {Object.entries(item.draft.conditions ?? {}).length === 0 ? (
            <p>条件未记录。</p>
          ) : (
            <dl>
              {Object.entries(item.draft.conditions ?? {}).map(([key, value]) => (
                <div key={key}>
                  <dt className="font-semibold">{key}</dt>
                  <dd className="whitespace-pre-wrap break-words">{value || "未记录"}</dd>
                </div>
              ))}
            </dl>
          )}
          <h4>备注</h4>
          <p className="whitespace-pre-wrap break-words">{item.draft.note || "无备注。"}</p>
          {item.draft.supersedes && (
            <Button
              disabled={unavailable}
              onClick={() => apparatusLocation({ observation: item.draft.supersedes! })}
            >
              查看本记录更正的原记录：{item.draft.supersedes}
            </Button>
          )}
          <h4>原始附件</h4>
          <p>仅在点击后下载原文件；不在页面内预览或执行。</p>
          <ul className="space-y-3">
            {item.draft.attachments?.map((attachment) => (
              <li key={attachment.content_hash}>
                {unavailable ? (
                  <span>{attachment.filename}（离线）</span>
                ) : (
                  <a
                    className={secondaryButton}
                    href={attachmentUrl(item.id, attachment.content_hash)}
                    download={attachment.filename}
                  >
                    下载原文件：{attachment.filename}
                  </a>
                )}
                <p className="mt-2 text-xs break-all">
                  {attachment.size_bytes} bytes · {attachment.content_hash}
                </p>
              </li>
            ))}
          </ul>
          {!item.draft.attachments?.length && <p>无附件。</p>}
          <h4>关联 run</h4>
          <p>关联仅用于回看，不改变科学主体、参数或校准状态。返回 Data 或使用“后退”继续阅读。</p>
          <ul>
            {item.draft.run_ids?.map((runId) => (
              <li key={runId}>
                <Button disabled={unavailable} onClick={() => onOpenRun(runId)}>
                  打开 run：{runId}
                </Button>
              </li>
            ))}
          </ul>
          {!item.draft.run_ids?.length && <p>无关联 run。</p>}
        </>
      )}
    </article>
  );
}

function Description({ revision }: { revision: ObjectRevision }) {
  return (
    <div className="break-words">
      <h3 className={sectionTitle}>{revision.content.name}</h3>
      <p>
        {revision.content.kind} · 别名：{revision.content.aliases?.join("、") || "无"}
      </p>
      <p className="whitespace-pre-wrap">{revision.content.description || "无描述。"}</p>
      <dl className="mt-3 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs break-all">
        <dt>catalog_id</dt>
        <dd>{revision.ref.catalog_id}</dd>
        <dt>object_id</dt>
        <dd>{revision.ref.object_id}</dd>
        <dt>revision</dt>
        <dd>{revision.ref.revision}</dd>
        <dt>content_hash</dt>
        <dd>{revision.ref.content_hash}</dd>
      </dl>
    </div>
  );
}

function Times({ observation }: { observation: Observation }) {
  return (
    <dl className="text-sm">
      <dt>测量/观察时间（observed_at）</dt>
      <dd>{observation.draft.observed_at ?? "未知"}</dd>
      <dt>记录时间（recorded_at）</dt>
      <dd>{observation.recorded_at}</dd>
    </dl>
  );
}

function cursor(value: string | null): number | undefined {
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : undefined;
}
