import { cp, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";

import { ROOT, startProject, stopProject, uv } from "./daemon";

const WORKFLOW = `
from dataclasses import dataclass
from pydantic import BaseModel, ConfigDict
from scopecat.api.procedures import LabProcedureContext
from scopecat.automation import procedure
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.setup import SetupRevisionRef
from .ordinary import signal

class Intent(BaseModel):
    model_config = ConfigDict(frozen=True)
    config: ConfigProfileSnapshot
    setup: SetupRevisionRef

@dataclass
class Wiring:
    confirmed: bool

@procedure(id="browser.manual-line", version="1", intent=Intent)
def measure_line(context: LabProcedureContext, intent: Intent) -> None:
    context.run("measure", signal, config=intent.config, setup=intent.setup,
        manual_line_id="stable-line-1", name="Reading line 1")
`;

const PREPARE = `
import json, sys, time
import scopecat as sc
from scopecat.analysis.facts import AnalysisFactSchema
project = sc.open_project(sys.argv[1])
project.load_application()
with project.authoring() as author:
    author.refresh()
    from ui_signal.application import initial_parameters
    from ui_signal.manual_line import Intent, Wiring, measure_line
    content = initial_parameters()
    saved = author.parameters.save(name="line-inputs", catalog=content.catalog, parameters=content.parameters)
    setup = author.setup.get("initial")
    resolved = author.parameters.resolve(saved, setup=setup)
    task = author.procedures.prepare(measure_line, Intent(config=resolved.config, setup=setup.ref), request_key="browser-line").submit()
    deadline = time.monotonic() + 45
    while task.snapshot.state != "waiting_for_input" or task.progress().dispatch.worker_running:
        assert time.monotonic() < deadline, task.progress()
        time.sleep(.05)
    task.pending_input(schema=AnalysisFactSchema("scopecat.manual-line-wiring.v1", Wiring)).respond(Wiring(True), actor="original-operator")
    print(task.id)
`;

const RESUME = `
import json, sys, time
import scopecat as sc
with sc.open_project(sys.argv[1]).authoring() as author:
    task = author.procedures.get(sys.argv[2])
    task.resume()
    deadline = time.monotonic() + 45
    while task.snapshot.state != "attention_required" or task.progress().dispatch.worker_running:
        assert time.monotonic() < deadline, task.progress()
        time.sleep(.05)
    [retained_child] = task.progress().child_runs
    child = retained_child.run
    assert child.control.attention_reason == "manual_line_confirmation_expired"
    print(json.dumps({"run": child.snapshot.run_id, "config": child.snapshot.config_content_hash}))
`;

for (const width of [1280, 390]) {
  test(`manual line reopens and survives a lost reconfirmation response at ${width}px`, async ({
    page,
  }) => {
    test.setTimeout(180_000);
    const project = await mkdtemp(join(tmpdir(), "scopecat-manual-line-"));
    let started = false;
    try {
      for (const name of ["src", "scopecat.toml"])
        await cp(join(ROOT, "testing/fixtures/retained-signal", name), join(project, name), {
          recursive: true,
        });
      await writeFile(join(project, "src/ui_signal/manual_line.py"), WORKFLOW);
      const application = join(project, "src/ui_signal/application.py");
      await writeFile(
        application,
        (await readFile(application, "utf8"))
          .replace(
            "    from .comparison import comparison_provider",
            "    from .comparison import comparison_provider\n    from .manual_line import measure_line",
          )
          .replace(
            "        comparison_provider=comparison_provider,",
            "        comparison_provider=comparison_provider,\n        procedures=(measure_line,),",
          ),
      );
      await startProject(project);
      started = true;
      const procedure = uv(["python", "-c", PREPARE, project]);
      stopProject(project);
      started = false;
      const baseUrl = await startProject(project);
      started = true;
      const seed = JSON.parse(uv(["python", "-c", RESUME, project, procedure])) as {
        run: string;
        config: string;
      };
      let lost = false;
      const commands: unknown[] = [];
      await page.route(`**/runs/${seed.run}/attention`, async (route) => {
        commands.push(route.request().postDataJSON());
        if (!lost) {
          lost = true;
          await route.fetch();
          await route.abort("failed");
        } else await route.continue();
      });
      await page.setViewportSize({ width, height: 900 });
      await page.goto(`${baseUrl}/?run=${seed.run}#runs`);
      const region = page.getByRole("region", { name: "Confirm current line wiring" });
      await expect(region).toBeVisible();
      await region.getByLabel("Line confirmation operator").fill("second-operator");
      await region.getByRole("checkbox").check();
      await region.getByRole("button", { name: "Confirm wiring and continue" }).click();
      await expect(region.getByRole("status")).toContainText("did not respond");
      await page.reload();
      await expect(region).toBeVisible();
      await expect(region.getByLabel("Line confirmation operator")).toHaveValue("second-operator");
      expect(commands).toHaveLength(1);
      await region.getByRole("checkbox").check();
      await region.getByRole("button", { name: "Retry saved confirmation" }).click();
      await expect
        .poll(
          async () => {
            const response = await page.request.get(`${baseUrl}/api/v1/procedures/${procedure}`);
            return (await response.json()).closure?.status;
          },
          { timeout: 45_000 },
        )
        .toBe("succeeded");
      expect(commands).toHaveLength(2);
      expect(commands[1]).toEqual(commands[0]);
      const run = await (await page.request.get(`${baseUrl}/api/v1/runs/${seed.run}`)).json();
      expect(run.snapshot.config_content_hash).toBe(seed.config);
      expect(run.control.admission.manual_line_confirmation.actor).toBe("original-operator");
      const segments = await (
        await page.request.get(`${baseUrl}/api/v1/runs/${seed.run}/execution-segments`)
      ).json();
      expect(segments.items).toHaveLength(1);
      const runs = await (await page.request.get(`${baseUrl}/api/v1/runs`)).json();
      expect(runs.items).toHaveLength(1);
    } finally {
      try {
        if (started) stopProject(project);
      } finally {
        await rm(project, { recursive: true, force: true });
      }
    }
  });
}
