import type { ProjectHealth } from "../../types";

const docs = "https://scopecat-project.github.io/scopecat/";
const section = "grid gap-3 rounded-lg border border-line bg-panel p-4";

export function HelpWorkspace({
  health,
  reachable,
}: {
  health?: ProjectHealth;
  reachable: boolean;
}) {
  return (
    <section aria-labelledby="help-heading" className="grid max-w-4xl gap-4 p-6">
      <h2 id="help-heading" className="text-lg font-semibold">
        Help
      </h2>
      {!reachable && (
        <p role="status">
          The application cannot currently be reached. Reopen Scopecat and use its recovery action
          to stop and restart the recorded background process.
        </p>
      )}
      <section className={section}>
        <h3 className="font-semibold">Start an experiment</h3>
        <ol className="list-decimal space-y-2 pl-5">
          <li>
            Open your code folder in VS Code. Select the Python interpreter shown in{" "}
            <a className="underline" href="#settings">
              Application settings
            </a>
            . Python files and notebooks use the same application as this window.
          </li>
          <li>
            In{" "}
            <a className="underline" href="#launch">
              Experiments
            </a>
            , select your code and experiment. Refresh author code after editing; this does not
            rebuild dependencies.
          </li>
          <li>
            Choose the setup and saved parameters for this measurement, then preview and submit.
            Reopening a result does not run it again.
          </li>
        </ol>
      </section>
      <section className={section}>
        <h3 className="font-semibold">Devices and measurement context</h3>
        <p>
          Maintain devices in{" "}
          <a className="underline" href="#instruments">
            Devices and drivers
          </a>
          . Setup selects device revisions, topology and routing. Parameter branches and saved
          working points are independent selections for each task.
        </p>
        <p>
          Finish active measurements and close instrument sessions before changing connections or
          applying a software update.
        </p>
        <a
          className="underline"
          href={docs + "how-to/maintain-executable-setup/"}
          target="_blank"
          rel="noopener noreferrer"
        >
          Maintain executable setup
        </a>
      </section>
      <section className={section}>
        <h3 className="font-semibold">Finish work or keep it running</h3>
        <p>
          Save your files and close Python sessions. Closing the Scopecat window lets you stop the
          application, keep it running in the background, or cancel. Closing a browser tab only
          closes that view.
        </p>
        <p>
          Application settings shows software updates, source registration and data locations.
          Updates preserve records and source files. After switching environments, reopen Python
          kernels with the selected interpreter.
        </p>
        {health && typeof health.details.data_root === "string" && (
          <p>
            Scientific data: <code>{health.details.data_root}</code>
          </p>
        )}
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
          Maintain the application
        </a>
      </section>
      <section className={section}>
        <h3 className="font-semibold">Practice without devices</h3>
        <p>
          Tutorial deliveries include editable synthetic-data examples. The current tutorial command
          prints a folder to open in VS Code; it does not open a management page.
        </p>
        <a
          className="underline"
          href={docs + "tutorials/teaching-sandboxes/"}
          target="_blank"
          rel="noopener noreferrer"
        >
          Teaching sandbox guide
        </a>
      </section>
    </section>
  );
}
