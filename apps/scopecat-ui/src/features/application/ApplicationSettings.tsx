import { OperatorSettings } from "./OperatorSettings";
import { ActionRow, Notice, Button, Field, Input, Select } from "../../ui/Controls";
import { pageTitle, sectionTitle, pageContent, classes } from "../../ui/styles";
import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { ProjectHealth } from "../../types";
import { errorMessage } from "../../lib/presentation";
import type { InstallationStatus } from "./DesktopSession";

const section =
  "grid min-w-0 gap-3 rounded-lg border border-line bg-panel p-4 [overflow-wrap:anywhere]";

export function ApplicationSettings({ health }: { health?: ProjectHealth }) {
  const [native, setNative] = useState(window.pywebview?.api);
  const [source, setSource] = useState(
    new URLSearchParams(window.location.search).get("source") ?? "",
  );
  const [creating, setCreating] = useState(false);
  const [interpreter, setInterpreter] = useState("");
  const [parent, setParent] = useState("");
  const [name, setName] = useState("experiments");
  useEffect(() => {
    const ready = () => setNative(window.pywebview?.api);
    window.addEventListener("pywebviewready", ready);
    return () => window.removeEventListener("pywebviewready", ready);
  }, []);
  const status = useQuery({
    queryKey: ["application-installation"],
    queryFn: () => native!.status(),
    enabled: !!native,
  });
  const operation = useMutation({
    mutationFn: async (
      action:
        | "source"
        | "environment"
        | "create"
        | "restart"
        | "backup"
        | "dependencies"
        | "client"
        | "rebuild-client",
    ) => {
      if (!native) return undefined;
      if (action === "create") return native.create_source(parent, name.trim());
      else if (action === "source") await native.register_source(source.trim(), interpreter.trim());
      else if (action === "environment")
        return native.select_source_environment(source.trim(), interpreter.trim());
      else if (action === "dependencies") return native.prepare_author_environment(source.trim());
      else if (action === "client") return native.create_author_environment(source.trim());
      else if (action === "rebuild-client")
        return native.create_author_environment(source.trim(), true);
      else if (action === "backup") await native.backup_application();
      else await native.restart();
      return undefined;
    },
    onSuccess: (_result, action) => {
      // Backup replaces this page after its bridge reply; do not start a new callback.
      if (action !== "backup") void status.refetch();
    },
  });
  const picker = useMutation({
    mutationFn: () => native!.choose_directory(),
    onSuccess: (path) => {
      if (path) {
        if (creating) setParent(path);
        else setSource(path);
      }
    },
  });
  const busy = operation.isPending || picker.isPending;
  const selected = status.data?.sources.find((item) => item.directory === source.trim());
  const dataRoot = health?.details.data_root;
  return (
    <section
      className={classes(pageContent, "max-w-4xl")}
      aria-labelledby="application-settings-heading"
    >
      <h2 id="application-settings-heading" className={pageTitle}>
        应用设置
      </h2>
      <OperatorSettings />
      {operation.error && <Notice role="alert">{errorMessage(operation.error)}</Notice>}
      {picker.error && <Notice role="alert">{errorMessage(picker.error)}</Notice>}
      {operation.data && <Notice role="status">{operation.data}</Notice>}
      {status.error && <Notice role="alert">{errorMessage(status.error)}</Notice>}
      <section className={section}>
        <h3 className={sectionTitle}>软件与数据</h3>
        {status.data && (
          <p>
            应用主目录： <code>{status.data.home}</code>
          </p>
        )}
        {typeof dataRoot === "string" && (
          <p>
            科学数据： <code>{dataRoot}</code>
          </p>
        )}
        {status.data && (
          <details>
            <summary>技术诊断</summary>
            <InstallationDetails installation={status.data.installation} />
          </details>
        )}
        <p>
          更新 Scopecat
          时，请先退出应用、安装新版本，再重新打开。启动时直接使用已安装版本。更新保留科学数据和源码目录；重新打开不会重复测量。
        </p>
        {native && (
          <>
            <p>
              请在空闲时备份科学记录与保留源码。外部代码目录、Python
              环境和应用安装程序需另行备份。服务会保持停止，直到你从备份结果页重新打开。
            </p>
            <Button disabled={busy} onClick={() => operation.mutate("backup")}>
              备份应用数据…
            </Button>
          </>
        )}
        {!native && <p>请打开 Scopecat 桌面应用，管理本地源码目录与 Python 环境。</p>}
      </section>
      {native && (
        <>
          <section className={section}>
            <h3 className={sectionTitle}>作者代码 · Author code</h3>
            <p>
              创建不需要设备的合成示例目录，或添加已有 Scopecat 代码。Notebook 环境用于在 VS Code
              交互编辑；独立的后台环境负责验证与执行实验。两者都使用本应用的数据，均不是应用自用的
              Python。连接后可用 print(session) 核对数据身份、作者目录及当前内核来源。
            </p>
            <ActionRow>
              <Button disabled={busy} onClick={() => setCreating(true)}>
                新建代码目录
              </Button>
              <Button disabled={busy} onClick={() => setCreating(false)}>
                使用已有目录
              </Button>
            </ActionRow>
            {creating ? (
              <div className="grid gap-3">
                <Button disabled={busy} onClick={() => picker.mutate()}>
                  选择保存位置…
                </Button>
                {parent && (
                  <p>
                    保存位置： <code>{parent}</code>
                  </p>
                )}
                <Field>
                  新目录名称
                  <Input
                    value={name}
                    disabled={busy}
                    onChange={(event) => setName(event.target.value)}
                  />
                </Field>
                <p>
                  为新目录准备 Notebook
                  环境和独立的后台执行环境。保留已有文件，不连接设备或发起采集。
                  合成示例转为真实实验时，需要按实验职责替换 setup、参数、驱动与依赖，不能只改地址。
                </p>
                <Button
                  disabled={busy || !parent || !name.trim()}
                  onClick={() => operation.mutate("create")}
                >
                  创建目录并准备 Python
                </Button>
              </div>
            ) : (
              <>
                {!!status.data?.sources.length && (
                  <Field>
                    代码目录
                    <Select
                      className="min-w-0 max-w-full"
                      value={selected ? source.trim() : ""}
                      disabled={busy}
                      onChange={(event) => setSource(event.target.value)}
                    >
                      <option value="">选择目录</option>
                      {status.data.sources.map((item) => (
                        <option key={item.directory} value={item.directory}>
                          {item.directory}
                        </option>
                      ))}
                    </Select>
                  </Field>
                )}
                <Button disabled={busy} onClick={() => picker.mutate()}>
                  浏览代码目录…
                </Button>
                <Field>
                  作者目录
                  <Input
                    value={source}
                    onChange={(event) => setSource(event.target.value)}
                    disabled={busy}
                    placeholder="包含 scopecat.toml 的目录"
                  />
                </Field>
                {!selected && (
                  <>
                    <Field>
                      执行 Python
                      <Input
                        value={interpreter}
                        disabled={busy}
                        onChange={(event) => setInterpreter(event.target.value)}
                        placeholder="python 或 python.exe 的完整路径"
                      />
                    </Field>
                    <p className="text-sm text-text-dim">
                      填写用于后台验证与执行的 Python 路径。登记已有目录不会安装依赖；Notebook
                      内核另行准备和选择。
                    </p>
                    <Button
                      disabled={busy || !source.trim() || !interpreter.trim()}
                      onClick={() => operation.mutate("source")}
                    >
                      添加代码目录
                    </Button>
                  </>
                )}
                {selected && (
                  <Notice role="status">
                    作者目录已登记，请在 VS Code 中打开。
                    {selected.python ? (
                      <>
                        {" "}
                        为 Python 或 Notebook 选择该目录的 .venv。新示例从{" "}
                        <code>notebooks/02_edit_scan.py</code> 开始，逐个运行单元格。
                      </>
                    ) : (
                      <> 请先在下方创建 Notebook 环境。</>
                    )}
                  </Notice>
                )}
                <p>
                  创建本目录的 Python 环境后，在 VS Code 中选择其 .venv；其中包含 Scopecat API 和
                  Notebook 内核。依赖声明放在
                  pyproject.toml；修改后明确重建客户端并准备后台执行环境。 标准工具重建方法见
                  author-environments/README.md，保留 requirements-client.txt 及其来源目录。临时 pip
                  安装不会写入此锁；已有任务保留原执行环境。
                </p>
                <Button
                  disabled={busy || !selected || !!selected.python}
                  onClick={() => operation.mutate("client")}
                >
                  创建本地 Python 环境
                </Button>
                <Button
                  disabled={busy || !source.trim()}
                  onClick={() => operation.mutate("dependencies")}
                >
                  按 pyproject.toml 准备执行环境
                </Button>
                <details>
                  <summary>查看或修改 Python 环境</summary>
                  {selected && (
                    <div className="mt-3 grid gap-3">
                      <p>
                        已配置的后台执行 Python：<code>{selected.execution_python}</code>
                        。这是保存的配置，不表示进程正在运行或环境可用。
                      </p>
                      {selected.python && (
                        <p>
                          Notebook Python：<code>{selected.python}</code>
                        </p>
                      )}
                      <Field>
                        执行 Python
                        <Input
                          value={interpreter}
                          disabled={busy}
                          onChange={(event) => setInterpreter(event.target.value)}
                          placeholder="python 或 python.exe 的完整路径"
                        />
                      </Field>
                      <Button
                        disabled={busy || !interpreter.trim()}
                        onClick={() => operation.mutate("environment")}
                      >
                        使用此执行 Python
                      </Button>
                    </div>
                  )}
                  <h4 className="mt-3 font-semibold">修复 Notebook 环境</h4>
                  <p>
                    修复本地 Python 前，先关闭该环境的终端和 Notebook
                    kernel。重建会另存旧环境并保留源码。 完成后重新选择
                    kernel，显式连接原应用；不要重新提交实验来找回结果。
                  </p>
                  <Button
                    disabled={busy || !selected}
                    onClick={() => operation.mutate("rebuild-client")}
                  >
                    重建本地 Python 环境
                  </Button>
                </details>
              </>
            )}
          </section>
          <section className={section}>
            <h3 className={sectionTitle}>重启应用</h3>
            <p>
              重启可恢复连接或应用本地设置修改。此操作会停止正在进行的任务并释放设备，保留已保存记录和源码文件。
            </p>
            <Button disabled={busy} onClick={() => operation.mutate("restart")}>
              停止任务并重启
            </Button>
          </section>
        </>
      )}
      {busy && <Notice role="status">正在处理…可保持窗口打开以查看结果。</Notice>}
    </section>
  );
}

function InstallationDetails({ installation }: { installation: InstallationStatus }) {
  return (
    <dl className="grid gap-2 break-all">
      <div>
        <dt>应用 Python（受管理；不是 Notebook kernel）</dt>
        <dd>
          <code>{installation.python}</code>
        </dd>
      </div>
      <div>
        <dt>运行时</dt>
        <dd>{installation.environment.python}</dd>
      </div>
      <div>
        <dt>能力标识</dt>
        <dd>
          <code>{installation.adapter_identity ?? "未选择可选能力"}</code>
        </dd>
      </div>
    </dl>
  );
}
