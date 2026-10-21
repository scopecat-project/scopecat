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
        Application settings
      </h2>
      <OperatorSettings />
      {operation.error && <Notice role="alert">{errorMessage(operation.error)}</Notice>}
      {picker.error && <Notice role="alert">{errorMessage(picker.error)}</Notice>}
      {operation.data && <Notice role="status">{operation.data}</Notice>}
      {status.error && <Notice role="alert">{errorMessage(status.error)}</Notice>}
      <section className={section}>
        <h3 className={sectionTitle}>Software and data</h3>
        {status.data && (
          <p>
            Application home: <code>{status.data.home}</code>
          </p>
        )}
        {typeof dataRoot === "string" && (
          <p>
            Scientific data: <code>{dataRoot}</code>
          </p>
        )}
        {status.data && (
          <details>
            <summary>Technical diagnostics</summary>
            <InstallationDetails installation={status.data.installation} />
          </details>
        )}
        <p>
          To update Scopecat, quit the application, install the new version, then reopen it. Startup
          uses the installed version directly. Updates keep scientific data and source folders.
          Reopening does not repeat measurements.
        </p>
        {native && (
          <>
            <p>
              Back up scientific records and retained source while idle. External code folders,
              Python environments and the application installer need separate copies. The service
              stays stopped until you reopen it from the backup result page.
            </p>
            <Button disabled={busy} onClick={() => operation.mutate("backup")}>
              Back up application data…
            </Button>
          </>
        )}
        {!native && (
          <p>
            Open the Scopecat desktop application to manage local source folders and Python
            environments.
          </p>
        )}
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
                New code folder
              </Button>
              <Button disabled={busy} onClick={() => setCreating(false)}>
                Use existing folder
              </Button>
            </ActionRow>
            {creating ? (
              <div className="grid gap-3">
                <Button disabled={busy} onClick={() => picker.mutate()}>
                  Choose save location…
                </Button>
                {parent && (
                  <p>
                    Save in: <code>{parent}</code>
                  </p>
                )}
                <Field>
                  New folder name
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
                  Create folder and prepare Python
                </Button>
              </div>
            ) : (
              <>
                {!!status.data?.sources.length && (
                  <Field>
                    Your code folders
                    <Select
                      className="min-w-0 max-w-full"
                      value={selected ? source.trim() : ""}
                      disabled={busy}
                      onChange={(event) => setSource(event.target.value)}
                    >
                      <option value="">Choose a folder</option>
                      {status.data.sources.map((item) => (
                        <option key={item.directory} value={item.directory}>
                          {item.directory}
                        </option>
                      ))}
                    </Select>
                  </Field>
                )}
                <Button disabled={busy} onClick={() => picker.mutate()}>
                  Browse for code folder…
                </Button>
                <Field>
                  Author directory
                  <Input
                    value={source}
                    onChange={(event) => setSource(event.target.value)}
                    disabled={busy}
                    placeholder="Folder containing scopecat.toml"
                  />
                </Field>
                {!selected && (
                  <>
                    <Field>
                      Execution Python
                      <Input
                        value={interpreter}
                        disabled={busy}
                        onChange={(event) => setInterpreter(event.target.value)}
                        placeholder="Full path to python or python.exe"
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
                      Add code folder
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
                  Create local Python environment
                </Button>
                <Button
                  disabled={busy || !source.trim()}
                  onClick={() => operation.mutate("dependencies")}
                >
                  Prepare execution environment from pyproject.toml
                </Button>
                <details>
                  <summary>Inspect or change Python environments</summary>
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
                        Execution Python
                        <Input
                          value={interpreter}
                          disabled={busy}
                          onChange={(event) => setInterpreter(event.target.value)}
                          placeholder="Full path to python or python.exe"
                        />
                      </Field>
                      <Button
                        disabled={busy || !interpreter.trim()}
                        onClick={() => operation.mutate("environment")}
                      >
                        Use this execution Python
                      </Button>
                    </div>
                  )}
                  <h4 className="mt-3 font-semibold">Repair notebook environment</h4>
                  <p>
                    修复本地 Python 前，先关闭该环境的终端和 Notebook
                    kernel。重建会另存旧环境并保留源码。 完成后重新选择
                    kernel，显式连接原应用；不要重新提交实验来找回结果。
                  </p>
                  <Button
                    disabled={busy || !selected}
                    onClick={() => operation.mutate("rebuild-client")}
                  >
                    Rebuild local Python environment
                  </Button>
                </details>
              </>
            )}
          </section>
          <section className={section}>
            <h3 className={sectionTitle}>Restart application</h3>
            <p>
              Restart to recover the connection or apply changes to local settings. This stops
              active work and releases devices. Saved records and source files are retained.
            </p>
            <Button disabled={busy} onClick={() => operation.mutate("restart")}>
              Stop work and restart
            </Button>
          </section>
        </>
      )}
      {busy && (
        <Notice role="status">Working… You can keep this window open to see the result.</Notice>
      )}
    </section>
  );
}

function InstallationDetails({ installation }: { installation: InstallationStatus }) {
  return (
    <dl className="grid gap-2 break-all">
      <div>
        <dt>Application Python (managed; not a notebook kernel)</dt>
        <dd>
          <code>{installation.python}</code>
        </dd>
      </div>
      <div>
        <dt>Runtime</dt>
        <dd>{installation.environment.python}</dd>
      </div>
      <div>
        <dt>Capability identity</dt>
        <dd>
          <code>{installation.adapter_identity ?? "No optional capability selected"}</code>
        </dd>
      </div>
    </dl>
  );
}
