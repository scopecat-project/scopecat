import { pageTitle, sectionTitle, pageContent, classes } from "../../ui/styles";
import { Notice } from "../../ui/Controls";
import type { ProjectHealth } from "../../types";
import { NotebookJourneyPanel } from "./NotebookJourneyPanel";
import { PracticePanel } from "./PracticePanel";

const docs = "https://scopecat-project.github.io/scopecat/";
const section = "grid gap-3 rounded-lg border border-line bg-panel p-4";

export function HelpWorkspace({ reachable }: { health?: ProjectHealth; reachable: boolean }) {
  return (
    <section aria-labelledby="help-heading" className={classes(pageContent, "max-w-4xl")}>
      <h2 id="help-heading" className={pageTitle}>
        帮助
      </h2>
      {!reachable && (
        <Notice role="status">
          当前无法连接应用。请重新打开 Scopecat，使用恢复操作停止并重启已记录的后台进程。
        </Notice>
      )}
      <NotebookJourneyPanel reachable={reachable} />
      <PracticePanel reachable={reachable} />
      <section className={section}>
        <h3 className={sectionTitle}>运行自己的实验</h3>
        <ol className="list-decimal space-y-2 pl-5">
          <li>
            在 VS Code 中打开代码目录。Notebook kernel 请使用该目录的 Notebook 解释器，路径见{" "}
            <a className="underline" href="#settings">
              应用设置
            </a>
            .
          </li>
          <li>
            在{" "}
            <a className="underline" href="#launch">
              实验
            </a>
            中选择代码与实验。修改代码后使用“刷新作者代码”。
          </li>
          <li>选择 setup 和参数，再点击“开始采集”。开始时会检查输入；预览为可选步骤。</li>
        </ol>
      </section>
      <nav aria-label="更多帮助" className={section}>
        <h3 className={sectionTitle}>配置与维护</h3>
        <a
          className="underline"
          href={docs + "how-to/maintain-executable-setup/"}
          target="_blank"
          rel="noopener noreferrer"
        >
          配置设备与连接
        </a>
        <a
          className="underline"
          href={docs + "how-to/backup-and-restore/"}
          target="_blank"
          rel="noopener noreferrer"
        >
          备份与恢复科学记录
        </a>
        <a
          className="underline"
          href={docs + "how-to/maintain-application/"}
          target="_blank"
          rel="noopener noreferrer"
        >
          关闭、重启或更新应用
        </a>
      </nav>
    </section>
  );
}
