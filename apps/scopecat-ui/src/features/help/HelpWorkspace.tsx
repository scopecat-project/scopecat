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
        Help
      </h2>
      {!reachable && (
        <Notice role="status">
          The application cannot currently be reached. Reopen Scopecat and use its recovery action
          to stop and restart the recorded background process.
        </Notice>
      )}
      <NotebookJourneyPanel reachable={reachable} />
      <PracticePanel reachable={reachable} />
      <section className={section}>
        <h3 className={sectionTitle}>Run your own experiment</h3>
        <ol className="list-decimal space-y-2 pl-5">
          <li>
            Open your code folder in VS Code. For the Notebook kernel, use the folder’s Notebook
            interpreter shown in{" "}
            <a className="underline" href="#settings">
              Application settings
            </a>
            .
          </li>
          <li>
            In{" "}
            <a className="underline" href="#launch">
              Experiments
            </a>
            , select your code and experiment. After editing code, use Refresh author code.
          </li>
          <li>
            Choose the setup and parameters, then Start acquisition. Start checks your inputs;
            Preview is optional.
          </li>
        </ol>
      </section>
      <nav aria-label="More help" className={section}>
        <h3 className={sectionTitle}>Setup and maintenance</h3>
        <a
          className="underline"
          href={docs + "how-to/maintain-executable-setup/"}
          target="_blank"
          rel="noopener noreferrer"
        >
          Set up devices and connections
        </a>
        <a
          className="underline"
          href={docs + "how-to/backup-and-restore/"}
          target="_blank"
          rel="noopener noreferrer"
        >
          Back up and restore scientific records
        </a>
        <a
          className="underline"
          href={docs + "how-to/maintain-application/"}
          target="_blank"
          rel="noopener noreferrer"
        >
          Close, restart or update the application
        </a>
      </nav>
    </section>
  );
}
