import { spawnSync } from "node:child_process";
import { cp, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";
import { readOnlyCode } from "../src/lib/read-only-code";

const ROOT = resolve(process.cwd(), "../..");
function uv(args: string[]): string {
  const env = { ...process.env };
  delete env.SCOPECAT_DAEMON_URL;
  const result = spawnSync("uv", ["run", "--locked", "--project", ROOT, ...args], {
    encoding: "utf8",
    env,
    timeout: 90_000,
  });
  if (result.error || result.status !== 0)
    throw new Error(`${result.error?.message ?? ""}\n${result.stdout}\n${result.stderr}`);
  return result.stdout.trim();
}

// Test-owned simulated acquisition and publications; no acceptance provider changes.
const SEED = `
import json, sys
import scopecat as sc
project = sc.open_project(sys.argv[1])
project.load_application()
from ui_signal.signal import signal, FREQUENCY, AMPLITUDE
from ui_signal.application import save_inputs
with project.connect() as lab:
    run = lab.run(signal.build().with_axis(sc.axis(FREQUENCY.ref, [sc.Quantity(4.8, "GHz")])).with_axis(sc.axis(AMPLITUDE.ref, [sc.Quantity(.1, "V")])), config=save_inputs(lab))
    publications = {}
    for kind, owner in (("run", run), ("project", lab)):
        publications[kind] = []
        for version in (1, 2):
            context = owner.analysis(f"Saved {kind} version {version}", key="copy-fit")
            context.measurements(run, id="source")
            publication = context.result().fact("version", version).save()
            publications[kind].append(publication.id)
    print(json.dumps({"runId": run.id, **publications}))
`;

const KERNEL = `
import json, os, sys
from pathlib import Path
from nbclient import NotebookClient
from nbformat import v4, write
from lab_tools.notebook import kernel_command
root = Path(sys.argv[1])
snippets = json.loads((root / "copied.json").read_text())
_, environment = kernel_command(root, source_path=False)
os.environ["JUPYTER_PATH"] = environment["JUPYTER_PATH"]
connection = "import scopecat as sc\\nsession = sc.notebook(" + repr(str(root)) + ", live=False)\\nbefore = session.list_runs(limit=100).model_dump(mode='json')"
cells = [v4.new_code_cell(connection)]
for item in snippets:
    cells.append(v4.new_code_cell(item["code"]))
    assertion = "assert run.id == " + repr(item["runId"]) if item["kind"] == "run" else ""
    if "publicationId" in item:
        assertion += "\\nassert publication.id == " + repr(item["publicationId"])
        assertion += "\\nassert publication.fact('version').value == 1"
        assertion += "\\nassert len(publication.outputs) == 1"
    cells.append(v4.new_code_cell(assertion))
cells.append(v4.new_code_cell("assert session.list_runs(limit=100).model_dump(mode='json') == before\\nassert not any(name.startswith('ui_signal') for name in __import__('sys').modules)\\nsession.close()"))
document = v4.new_notebook(cells=cells)
try:
    NotebookClient(document, timeout=60, kernel_name="scopecat-lab", resources={"metadata": {"path": str(root)}}).execute()
finally:
    write(document, root / "copied-code-executed.ipynb")
print("Exact old publications reopened in a fresh kernel; run history unchanged; no author module imports")
`;

test("copies exact read-only code for both owners and executes it in a fresh kernel", async ({
  page,
}, testInfo) => {
  test.setTimeout(120_000);
  const project = await mkdtemp(join(tmpdir(), "scopecat-copy-code-"));
  let passed = false;
  try {
    for (const name of ["src", "scopecat.toml"])
      await cp(join("e2e/fixtures/retained-signal", name), join(project, name), {
        recursive: true,
      });
    uv(["scopecat", "start", project, "--port", "0", "--static-dir", resolve("dist")]);
    const endpoint = JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8")) as {
      base_url: string;
    };
    const saved = JSON.parse(uv(["python", "-c", SEED, project])) as {
      runId: string;
      run: string[];
      project: string[];
    };
    const publicationHistory = async () =>
      Promise.all(
        [`/api/v1/runs/${saved.runId}/analyses?limit=100`, "/api/v1/analyses?limit=100"].map(
          async (path) => {
            const response = await page.request.get(`${endpoint.base_url}${path}`);
            expect(response.ok()).toBe(true);
            return response.json();
          },
        ),
      );
    const publicationsBefore = await publicationHistory();
    expect(saved.run).toEqual(["analysis-copy-fit-r1", "analysis-copy-fit-r2"]);
    expect(saved.project).toEqual(saved.run);
    // Exercise browser interactions without touching the host/system clipboard.
    await page.addInitScript(`
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: {
          writeText: async (code) => {
            document.documentElement.dataset.copied = code;
          },
        },
      });
    `);
    const snippets: {
      kind: "run" | "project";
      code: string;
      runId?: string;
      publicationId?: string;
    }[] = [];
    await page.goto(`${endpoint.base_url}/?run=${encodeURIComponent(saved.runId)}#runs`);
    const header = page.getByTestId("run-detail-header");
    await header.getByRole("button", { name: "Copy read-only code" }).click();
    await expect(header.getByRole("status")).toHaveText("Read-only code copied.");
    const runCode = await page.locator("html").getAttribute("data-copied");
    expect(runCode).toBe(readOnlyCode({ kind: "run", runId: saved.runId }));
    snippets.push({ kind: "run", runId: saved.runId, code: runCode! });

    const old = page.locator("details").filter({ hasText: "Saved run version 1" });
    await old.locator("summary").click();
    await expect(old.getByTestId("publication-id")).toHaveText(saved.run[0]);
    await old.getByRole("button", { name: "Copy read-only code" }).click();
    await expect(old.getByRole("status")).toHaveText("Read-only code copied.");
    const analysisCode = await page.locator("html").getAttribute("data-copied");
    expect(analysisCode).toBe(
      readOnlyCode({ kind: "run", runId: saved.runId, publicationId: saved.run[0] }),
    );
    snippets.push({
      kind: "run",
      runId: saved.runId,
      publicationId: saved.run[0],
      code: analysisCode!,
    });

    await page.goto(
      `${endpoint.base_url}/?analysis=${encodeURIComponent(saved.project[0])}#analyses`,
    );
    await expect(page.getByTestId("publication-id")).toHaveText(saved.project[0]);
    // Denial and missing API both expose exactly the same manual-copy code.
    for (const mode of ["denied", "missing"]) {
      await page.evaluate(
        (clipboardMode) =>
          Object.defineProperty(navigator, "clipboard", {
            configurable: true,
            value:
              clipboardMode === "missing"
                ? undefined
                : {
                    writeText: async () => {
                      throw new DOMException("Denied", "NotAllowedError");
                    },
                  },
          }),
        mode,
      );
      await page.getByRole("button", { name: "Copy read-only code" }).click();
      const field = page.getByRole("textbox", { name: "Read-only code" });
      const code = readOnlyCode({ kind: "project", publicationId: saved.project[0] });
      await expect(field).toHaveValue(code);
      await field.focus();
      expect(
        await field.evaluate(
          (element: { selectionEnd: number; selectionStart: number }) =>
            element.selectionEnd - element.selectionStart,
        ),
      ).toBe(code.length);
      if (mode === "denied")
        snippets.push({
          kind: "project",
          publicationId: saved.project[0],
          code: await field.inputValue(),
        });
    }
    await page.screenshot({ path: testInfo.outputPath("manual-copy.png"), fullPage: true });
    await writeFile(join(project, "copied.json"), JSON.stringify(snippets));
    const evidence = uv(["python", "-c", KERNEL, project]);
    await testInfo.attach("kernel evidence", { body: evidence, contentType: "text/plain" });
    await testInfo.attach("executed copied cells", {
      path: join(project, "copied-code-executed.ipynb"),
      contentType: "application/x-ipynb+json",
    });
    const history = await page.request.get(`${endpoint.base_url}/api/v1/runs?limit=100`);
    expect((await history.json()).items).toHaveLength(1);
    expect(await publicationHistory()).toEqual(publicationsBefore);
    passed = true;
  } catch (error) {
    await testInfo.attach("original failure", { body: String(error), contentType: "text/plain" });
    throw error;
  } finally {
    uv(["scopecat", "stop", project]);
    if (passed) await rm(project, { recursive: true, force: true });
    else
      await testInfo.attach("preserved test project", { body: project, contentType: "text/plain" });
  }
});
