import { Button, Notice, Select } from "../../ui/Controls";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { type LessonTopic, useDesktopAvailable } from "../application/DesktopSession";
import { navigate, navigateLink, useLocationUrl } from "../../lib/navigation";
import { NotebookCleanup } from "./NotebookCleanup";

const lessons: Record<LessonTopic, { title: string; goal: string; description: string }> = {
  parameters: {
    title: "参数与扫描",
    goal: "运行、查看结果、编辑 Python，再次运行。",
    description: "从这里开始：运行七点合成扫描，在应用中查看结果，再编辑 Python 并比较新旧运行。",
  },
  refresh: {
    title: "编辑与刷新实验",
    goal: "比较源码修改并添加实验。",
    description: "完成参数与扫描后：保存源码修改，比较新旧请求，并在保留早期运行的同时添加实验。",
  },
  compute: {
    title: "平均 IQ 与类型化结果",
    goal: "计算平均 IQ 并读取类型化结果。",
    description:
      "基础扫描后的可选课程，建议具备 NumPy 基础。比较逐次 IQ 与平均值，并用类型化 reader 重新打开精确保存的运行。",
  },
  groups: {
    title: "分组分析与历史",
    goal: "按组分析曲线并重新打开保存的分析。",
    description:
      "基础扫描后的可选课程：分组分析两条合成曲线，加入第三条，无需重新采集即可打开保存的分析。",
  },
  procedure: {
    title: "流程与人工决策",
    goal: "扫描、检查证据、回答类型化问题，再明确继续。",
    description: "基础课程之后：编辑小型合成流程，捕获待回答问题并记录答案，不涉及校准或任务调度。",
  },
  calibration: {
    title: "参数校准与恢复",
    goal: "采用修正建议前先验证。",
    description:
      "高级课程，需先理解保存的参数与分析。比较通过验证并发布的修正，以及被拒绝、保持分支不变的修正。",
  },
  "joint-calibration": {
    title: "联合校准与耦合检查",
    goal: "检查组合修正是否协同生效。",
    description: "参数校准之后：理解单独修正通过但组合失败的原因，并检查耦合决策。",
  },
  "task-calibration": {
    title: "后台校准与发布",
    goal: "区分任务进度与验证后的发布。",
    description: "联合校准之后：比较接受、拒绝和分支冲突任务；区分阶段完成与验证后的发布。",
  },
};

const courseGroups: { title: string; hint: string; topics: LessonTopic[] }[] = [
  { title: "基础", hint: "从这里开始，继续编写实验。", topics: ["parameters", "refresh"] },
  {
    title: "可选",
    hint: "选择下一步需要的技能。",
    topics: ["compute", "groups", "procedure"],
  },
  {
    title: "高级",
    hint: "组合已验证的修正。",
    topics: ["calibration", "joint-calibration", "task-calibration"],
  },
];

function lessonAt(location: URL): LessonTopic {
  const selected = location.searchParams.get("lesson");
  return selected && Object.hasOwn(lessons, selected) ? (selected as LessonTopic) : "parameters";
}

export function NotebookJourneyPanel({ reachable }: { reachable: boolean }) {
  const topic = lessonAt(useLocationUrl());
  const [busy, setBusy] = useState(false);
  return (
    <section
      aria-labelledby="notebook-journey-heading"
      className="grid gap-3 rounded-lg border border-line bg-panel p-4"
    >
      <h3 id="notebook-journey-heading" className="font-semibold">
        通过 Notebook 学习
      </h3>
      <p>从“参数与扫描”开始，或继续已准备的课程。</p>
      <CourseBrowser topic={topic} busy={busy} />
      <NotebookJourneyCourse
        key={topic}
        topic={topic}
        reachable={reachable}
        onBusyChange={setBusy}
      />
    </section>
  );
}

function CourseBrowser({ topic, busy }: { topic: LessonTopic; busy: boolean }) {
  const courseId = useId();
  return (
    <label className="grid gap-1.5">
      <span id={`${courseId}-label`}>课程</span>
      <Select
        aria-labelledby={`${courseId}-label`}
        disabled={busy}
        value={topic}
        aria-describedby={`${courseId}-goal`}
        onChange={(event) => {
          const next = new URL(window.location.href);
          next.searchParams.set("lesson", event.target.value);
          navigate(next, { replace: true });
        }}
      >
        {courseGroups.map((group) => (
          <optgroup key={group.title} label={group.title}>
            {group.topics.map((value) => (
              <option key={value} value={value}>
                {lessons[value].title}
              </option>
            ))}
          </optgroup>
        ))}
      </Select>
      <span id={`${courseId}-goal`} className="text-sm text-text-dim">
        {lessons[topic].goal}
      </span>
    </label>
  );
}

function NotebookJourneyCourse({
  topic,
  reachable,
  onBusyChange,
}: {
  topic: LessonTopic;
  reachable: boolean;
  onBusyChange: (busy: boolean) => void;
}) {
  const desktop = useDesktopAvailable();
  const native = desktop ? window.pywebview?.api : undefined;
  const client = useQueryClient();
  const active = useRef(false);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  const stillSelected = () => active.current && lessonAt(new URL(window.location.href)) === topic;
  const [parent, setParent] = useState<string>();
  const [clearing, setClearing] = useState(false);
  const status = useQuery({
    queryKey: ["notebook-journey", topic],
    queryFn: () => native!.notebook_journey(topic),
    enabled: !!native,
    refetchInterval: 1_000,
  });
  const open = useMutation({
    mutationFn: (requested: LessonTopic) => native!.open_lesson_notebook(requested),
  });
  const prepare = useMutation({
    mutationFn: (request: { topic: LessonTopic; parent?: string; repair?: boolean }) =>
      request.repair
        ? native!.prepare_notebook_journey(request.parent, request.topic, true)
        : native!.prepare_notebook_journey(request.parent, request.topic),
    onSuccess: (journey, request) => {
      client.setQueryData(["notebook-journey", request.topic], { state: "ready", journey });
      if (stillSelected()) open.mutate(request.topic);
    },
    onSettled: (_journey, _error, request) =>
      client.invalidateQueries({ queryKey: ["notebook-journey", request.topic] }),
  });
  const choose = useMutation({
    mutationFn: () => native!.choose_directory(),
    onSuccess: (directory) => {
      if (directory && stillSelected()) setParent(directory);
    },
  });
  const journey = status.data?.journey;
  const phase = status.data?.state;
  const previousPhase = useRef(phase);
  const { isError: preparationFailed, reset: resetPreparation } = prepare;
  useEffect(() => {
    if (
      preparationFailed &&
      previousPhase.current === "retryable" &&
      (phase === "preparing" || phase === "ready")
    ) {
      resetPreparation();
    }
    previousPhase.current = phase;
  }, [phase, preparationFailed, resetPreparation]);
  const preparing = prepare.isPending || status.data?.state === "preparing";
  const busy = preparing || open.isPending || choose.isPending || clearing;
  useEffect(() => {
    onBusyChange(busy);
    return () => onBusyChange(false);
  }, [busy, onBusyChange]);
  const error = prepare.error ?? open.error ?? choose.error ?? status.error;
  return (
    <section className="grid gap-3 border-t border-line pt-4" aria-label="选中课程">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="font-semibold">{lessons[topic].title}</h4>
        <span className="rounded border border-line px-2 py-1 text-text-soft">
          {!native
            ? "需要桌面应用"
            : preparing
              ? "正在准备"
              : status.isError
                ? "状态不可用"
                : status.isPending
                  ? "正在检查准备状态…"
                  : journey?.cleared
                    ? "课程已清理"
                    : journey?.ready
                      ? "可以继续"
                      : journey
                        ? "准备未完成"
                        : "尚未准备"}
        </span>
      </div>
      <p>{lessons[topic].description} 使用合成数据，无需设备。</p>
      {!native ? (
        <p>请在 Scopecat 桌面应用中打开帮助，以准备此 Notebook。</p>
      ) : (
        <>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="primary"
              disabled={!reachable || busy || status.isPending || status.isError}
              onClick={() => {
                open.reset();
                prepare.mutate({ topic, parent });
              }}
            >
              {preparing
                ? "正在准备 Notebook…"
                : journey?.cleared
                  ? "开始新课程"
                  : journey?.ready
                    ? `继续${lessons[topic].title} Notebook`
                    : journey
                      ? "重试准备"
                      : `开始${lessons[topic].title} Notebook`}
            </Button>
            {!journey && status.isSuccess && !preparing && (
              <Button disabled={busy} onClick={() => choose.mutate()}>
                选择其他保存位置…
              </Button>
            )}
          </div>
          {!journey && status.isSuccess && !preparing && (
            <p>{parent ? `保存位置：${parent}` : "默认使用应用主目录下的新目录。"}</p>
          )}
        </>
      )}
      {busy && (
        <Notice role="status">
          {preparing
            ? "正在准备代码与 Python，可能需要几分钟。"
            : choose.isPending
              ? "正在选择保存位置…"
              : "正在打开编辑器…"}
        </Notice>
      )}
      {open.isSuccess && !busy && (
        <Notice role="status">
          已请求打开编辑器。请选择下方 Python kernel。kernel 重启后，请用连接和历史单元格恢复；Run
          All 会发起新任务。
        </Notice>
      )}
      {journey && (
        <div className="grid gap-1 break-words">
          {(preparing || !journey.ready) && (
            <p>
              {preparing
                ? "正在准备，完成后此目录即可使用。"
                : journey.cleared
                  ? "课程已清理。重新准备会创建新目录。"
                  : "准备未完成。重试会使用同一目录并保留修改。"}
            </p>
          )}
          {(open.isError || !journey.ready) && (
            <>
              <p>
                代码目录： <code className="break-all">{journey.directory}</code>
              </p>
              <p>
                Notebook： <code className="break-all">{journey.notebook}</code>
              </p>
            </>
          )}
          {(open.isSuccess || open.isError) && (
            <p>
              Python kernel： <code className="break-all">{journey.python}</code>
            </p>
          )}
          <a
            className="underline"
            href={
              journey.scope_id
                ? `?course=${encodeURIComponent(journey.scope_id)}#history`
                : "#history"
            }
            onClick={navigateLink}
          >
            {journey.scope_id ? "在数据页查看此课程" : "在数据页查找已保存结果"}
          </a>
          <NotebookCleanup
            topic={topic}
            journey={journey}
            disabled={busy}
            onBusyChange={setClearing}
          />
          <details>
            <summary>修复 Notebook 环境</summary>
            <p>
              如导入失败或 Python 环境不完整，请先关闭此目录的 Notebook 和 Python
              终端，再修复。修复保留已编辑文件、保存结果及原本地环境，并为 Notebook
              和后续后台任务准备匹配依赖。已有任务保留原执行环境。完成后请重新安装自行添加的包。
            </p>
            <Button
              disabled={!reachable || busy || status.isError}
              onClick={() => {
                open.reset();
                prepare.mutate({ topic, repair: true });
              }}
            >
              修复环境并打开 Notebook
            </Button>
          </details>
          {journey.ready && (
            <>
              <p>
                继续编辑 <code>src/my_experiment</code> ，位于此课程代码目录中。
              </p>
              <p>
                <a
                  className="underline"
                  href={`?lesson=${topic}&source=${encodeURIComponent(journey.directory)}#settings`}
                  onClick={navigateLink}
                >
                  在设置中管理此代码目录
                </a>
              </p>
            </>
          )}
        </div>
      )}
      {error && (
        <Notice role="alert">{error instanceof Error ? error.message : String(error)}</Notice>
      )}
    </section>
  );
}
