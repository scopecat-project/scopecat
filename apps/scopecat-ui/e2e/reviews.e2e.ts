import { cp, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { ROOT, startProject, stopProject } from "./daemon";

// Real daemon and deterministic worker completion; not native lifecycle qualification.
test("exact review identity and shared latest-wins across browser views", async ({
  page,
  context,
  request,
}, testInfo) => {
  const project = await mkdtemp(join(tmpdir(), "scopecat-reviews-"));
  let started = false;
  let heartbeat: ReturnType<typeof setInterval> | undefined;
  try {
    for (const name of ["src", "scopecat.toml"])
      await cp(join(ROOT, "testing/fixtures/retained-signal", name), join(project, name), {
        recursive: true,
      });
    const base = await startProject(project);
    started = true;
    const api = `${base}/api/v1/reviews`;
    const result = (id: string, value: number) => ({
      request_id: id,
      completed_at: "2026-10-26T10:00:00Z",
      point: point(value),
      inspections: [
        {
          operation_id: "capture",
          point_index: value,
          target_id: "synthetic",
          artifact_id: "artifact",
          artifact_fingerprint: "sha256:artifact",
          content: {
            schema_id: "scopecat.compiled_artifact_inspection.v2",
            kind: "synthetic.waveform",
            facts: [],
            work_estimates: [],
            warnings: [],
            point_count: 1,
            points_truncated: false,
            bounds: { max_points: 1, max_waveforms_per_point: 1, max_samples_per_waveform: 4 },
            points: [
              {
                realization_fingerprint: "sha256:realization",
                target_entry_id: "point",
                facts: [],
                waveform_count: 1,
                waveforms_truncated: false,
                warnings: [],
                waveforms: [
                  {
                    channel_id: "awg/output",
                    instrument_id: "awg",
                    peak_abs: 0.5,
                    rms: 0.25,
                    source_sample_count: 4,
                    samples_sha256: "samples",
                    sample_indices: [0, 1, 2, 3],
                    samples: [0, 0.5, -0.5, 0],
                    downsampling: "none",
                  },
                ],
              },
            ],
          },
        },
      ],
    });
    const create = async (id: string) => {
      const response = await request.post(api, {
        data: {
          session_id: id,
          worker_id: `worker-${id}`,
          title: `检查 ${id}`,
          experiment_id: "synthetic-review",
          experiment_kind: "experiment",
          coordinates: [{ id: "amplitude", kind: "int", finite: true, sampled_values: [0, 1, 2] }],
          planned_points: [point(0), point(1), point(2)],
          initial_result: result("initial", 0),
        },
      });
      expect(response.status(), await response.text()).toBe(201);
    };
    await page.goto(`${base}/#reviews`);
    await expect(page.getByText("暂无实验检查会话")).toBeVisible();
    await create("a");
    heartbeat = setInterval(() => {
      void request.post(`${api}/a/worker/heartbeat?worker_id=worker-a`);
    }, 3000);
    await expect(page).toHaveURL(/#reviews\/a$/);
    await page.goto(`${base}/#reviews/missing`);
    await expect(page.getByText("指定的检查会话不存在")).toBeVisible();
    await page.reload();
    await expect(page.getByText("指定的检查会话不存在")).toBeVisible();
    await expect(page).toHaveURL(/#reviews\/missing$/);
    await page.getByRole("button", { name: /检查 a/ }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByText("编译选点", { exact: true })).toBeVisible();
    await page.goBack();
    await expect(page.getByText("指定的检查会话不存在")).toBeVisible();
    await page.goForward();
    await expect(page.getByText("编译选点", { exact: true })).toBeVisible();
    await create("b");
    await expect(page.getByRole("button", { name: /检查 b/ })).toBeVisible();
    await expect(page).toHaveURL(/#reviews\/a$/);
    const other = await context.newPage();
    await other.goto(`${base}/#reviews/a`);
    const claim = async () => {
      const response = await request.post(`${api}/a/worker/claim?worker_id=worker-a`);
      expect(response.ok()).toBeTruthy();
      return response.json();
    };
    const complete = async (id: string, value: number) => {
      const response = await request.post(`${api}/a/worker/complete`, {
        data: { worker_id: "worker-a", result: result(id, value) },
      });
      expect(response.ok(), await response.text()).toBeTruthy();
      return response.json();
    };
    const first = await submit(page, 1);
    expect((await claim()).request_id).toBe(first);
    const latest = await submit(other, 2);
    expect((await claim()).request_id).toBe(latest);
    for (const view of [page, other]) {
      await expect(view.getByText(`请求 ${latest}`, { exact: true })).toBeVisible();
      await expect(view.getByText(/上一结果（新请求尚未完成）/)).toBeVisible();
      await expect(view.getByText("结果请求：initial")).toBeVisible();
      await expect(view.getByRole("img", { name: "编译后的物理波形" })).toBeVisible();
      await expect(view.getByText("计划点 #1", { exact: true })).toBeVisible();
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.screenshot({
      path: testInfo.outputPath("reviews-wide-pending.png"),
      fullPage: true,
    });
    await other.setViewportSize({ width: 390, height: 844 });
    await expect(other.getByRole("button", { name: "编译波形", exact: true })).toBeVisible();
    expect(await other.evaluate("document.documentElement.scrollWidth <= window.innerWidth")).toBe(
      true,
    );
    await expect
      .poll(async () => {
        const chart = await other.getByRole("img", { name: "编译后的物理波形" }).boundingBox();
        const svg = await other
          .getByRole("img", { name: "编译后的物理波形" })
          .locator("svg")
          .boundingBox();
        return chart != null && svg != null && svg.x + svg.width <= 390 && svg.width <= chart.width;
      })
      .toBe(true);
    await other.screenshot({
      path: testInfo.outputPath("reviews-narrow-pending.png"),
      fullPage: true,
    });
    await complete(latest, 2);
    expect((await complete(first, 1)).latest_result.request_id).toBe(latest);
    for (const view of [page, other]) {
      await expect(view.getByText(`结果请求：${latest}`)).toBeVisible();
      await expect(view.getByText("最新请求：已完成")).toBeVisible();
    }
    await expect(page.getByRole("spinbutton", { name: "amplitude" })).toHaveValue("1");
    await other.close();
    expect((await (await request.get(`${api}/a`)).json()).active).toBe(true);
    const reopened = await context.newPage();
    await reopened.goto(`${base}/#reviews/a`);
    await expect(reopened.getByText(`结果请求：${latest}`)).toBeVisible();
    await reopened.close();
    clearInterval(heartbeat);
    heartbeat = undefined;
    await request.post(`${api}/a/worker/close?worker_id=worker-a`);
    // Exercise actual bounded retention, rather than mocking disappearance.
    for (let index = 0; index < 33; index++) {
      const id = `retained-${index}`;
      await create(id);
      await request.post(`${api}/${id}/worker/close?worker_id=worker-${id}`);
    }
    await expect(page.getByText("指定的检查会话不存在")).toBeVisible();
    await expect(page).toHaveURL(/#reviews\/a$/);
  } finally {
    if (heartbeat) clearInterval(heartbeat);
    if (started) stopProject(project);
    await rm(project, { recursive: true, force: true });
  }
});

const point = (value: number) => ({
  point_index: value,
  coordinates: { amplitude: value },
  source: "author",
});

const submit = async (view: Page, value: number) => {
  await view.getByRole("spinbutton", { name: "amplitude" }).fill(String(value));
  const response = view.waitForResponse(
    (r) => r.url().endsWith("/a/compile") && r.request().method() === "POST",
  );
  await view.getByRole("button", { name: "编译波形", exact: true }).click();
  const receipt = await response;
  expect(receipt.status()).toBe(202);
  return (await receipt.json()).request_id as string;
};
