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
  const dataRoot = health?.details.data_root;
  return (
    <section aria-labelledby="help-heading" className="grid max-w-4xl gap-4 p-6">
      <h2 id="help-heading" className="text-lg font-semibold">
        Help and maintenance
      </h2>
      <section className={section} aria-label="Current application">
        <h3 className="font-semibold">Know which service you are using</h3>
        {health ? (
          <dl className="grid gap-2 break-all">
            <div>
              <dt>Project</dt>
              <dd>{health.projectName}</dd>
            </div>
            <div>
              <dt>Project directory</dt>
              <dd>{health.projectRoot}</dd>
            </div>
            {typeof dataRoot === "string" && (
              <div>
                <dt>Scientific data directory</dt>
                <dd>{dataRoot}</dd>
              </div>
            )}
          </dl>
        ) : (
          <p>Current service information is unavailable.</p>
        )}
        {!reachable && (
          <p role="status">
            The service cannot currently be reached. Any project details above are from the last
            successful connection.
          </p>
        )}
        <p>
          Closing this browser tab does not stop the experiment service. Use the application manager
          when you need to stop it, after finishing measurements and Notebook work.
        </p>
      </section>
      <section className={section}>
        <h3 className="font-semibold">Choose code and measurement context</h3>
        <ol className="list-decimal space-y-2 pl-5">
          <li>
            In{" "}
            <a className="underline" href="#launch">
              Experiments
            </a>
            , select a registered Code workspace, then an experiment. Refresh author code prepares
            that workspace's current source. A saved plan pinned to a code revision keeps that
            revision until you choose Use current source.
          </li>
          <li>
            Choose the sample or exact registered target, working point and batch for the
            measurement. Operator and record collection are independent choices. Preview checks
            their compatibility before starting.
          </li>
          <li>
            To add or reconnect a code directory, ask the maintainer to register it from the local
            environment with the service stopped. The Code workspace menu selects existing
            registrations; it does not register filesystem paths.
          </li>
        </ol>
        <a
          className="underline"
          href={`${docs}reference/project-layout/`}
          target="_blank"
          rel="noopener noreferrer"
        >
          Code workspace registration and project layout
        </a>
      </section>
      <section className={section}>
        <h3 className="font-semibold">Maintain setup and saved parameters</h3>
        <p>
          In{" "}
          <a className="underline" href="#configuration">
            Configuration
          </a>
          , save and review an executable setup before explicitly selecting it. Setup controls
          topology, routing and instrument declarations. Parameter defaults and saved working points
          are selected separately.
        </p>
        <p>
          Existing working points retain their setup. Explicitly rebind their parameters when moving
          to a different setup; rebinding does not carry calibration acceptance forward. Finish
          active measurements and close instrument sessions before changes that require the devices
          to be free.
        </p>
        <a
          className="underline"
          href={`${docs}how-to/maintain-executable-setup/`}
          target="_blank"
          rel="noopener noreferrer"
        >
          Maintain executable setup
        </a>
      </section>
      <section className={section}>
        <h3 className="font-semibold">Open application management and tutorials</h3>
        <ol className="list-decimal space-y-2 pl-5">
          <li>
            If the application manager is still open in another tab, return to that tab. Starting a
            service there provides a separate Open workbench link.
          </li>
          <li>
            For an installed delivery, reopen its original launcher: double-click{" "}
            <code>lab.cmd</code> on Windows, or run <code>python lab.py</code> from that
            installation directory. The launcher uses that installation's environment and management
            home.
          </li>
          <li>
            In a Python environment with <code>scopecat-lab-tools</code> installed,{" "}
            <code>scopecat app</code> reopens its manager. If you used a custom home or source
            checkout, reuse your original <code>--home</code> and <code>--source</code> arguments.
          </li>
          <li>
            In the manager, expand Help · Teaching and practice (帮助 · 教学与练习). Available
            tutorials prepare disposable synthetic-data sandboxes. Teaching requires an installed
            tutorial delivery or a configured source checkout; an experiment-only environment may
            have no tutorials.
          </li>
        </ol>
        <p>Use the original launcher to reopen the manager for the correct installation.</p>
        <a
          className="underline"
          href={`${docs}tutorials/teaching-sandboxes/`}
          target="_blank"
          rel="noopener noreferrer"
        >
          Teaching sandbox guide
        </a>
      </section>
      <section className={section}>
        <h3 className="font-semibold">Stop, update and retain records</h3>
        <p>
          Finish active work and close Notebook connections before stopping a service or replacing
          its environment. Keep the installation's project and data locations recorded. After an
          environment change, use the trusted local registration flow to check the interpreter and
          GUI before reopening.
        </p>
        <p>
          Removing a service registration preserves project files and scientific records. Resetting
          a tutorial creates a new copy; old copies remain until you explicitly delete an eligible
          one. These are separate actions.
        </p>
        <a
          className="underline"
          href={`${docs}how-to/backup-and-restore/`}
          target="_blank"
          rel="noopener noreferrer"
        >
          Back up and restore scientific records
        </a>
        <a
          className="underline"
          href={`${docs}development/architecture/application-host/`}
          target="_blank"
          rel="noopener noreferrer"
        >
          Application management
        </a>
      </section>
    </section>
  );
}
