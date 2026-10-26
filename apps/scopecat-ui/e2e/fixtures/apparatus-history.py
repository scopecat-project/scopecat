"""Synthetic non-sample lines and retained software run for the browser journey."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import scopecat as sc

project = sc.open_project(sys.argv[1])
project.load_application()
from ui_signal.application import save_inputs  # noqa: E402
from ui_signal.signal import AMPLITUDE, FREQUENCY, signal  # noqa: E402

with project.connect() as lab:
    run = lab.run(
        signal.build()
        .with_axis(sc.axis(FREQUENCY.ref, [sc.Quantity(4.8, "GHz")]))
        .with_axis(sc.axis(AMPLITUDE.ref, [sc.Quantity(0.1, "V")])),
        config=save_inputs(lab),
    )
    a = lab.apparatus.create(
        "line-a",
        name="Shared input line",
        kind="microwave-line",
        aliases=("blue",),
        description="Original A description",
        actor="fixture",
    )
    b = lab.apparatus.create(
        "line-b",
        name="Shared input line",
        kind="cable",
        aliases=("blue",),
        description="Independent B description",
        actor="fixture",
    )
    path = Path(sys.argv[1]) / "same.pptx"
    path.write_bytes(b"synthetic A original\x00\xff")
    attachment_a = lab.apparatus.import_file(path)
    path.write_bytes(b"synthetic B original\x00\xfe")
    attachment_b = lab.apparatus.import_file(path)
    path.unlink()
    original = lab.apparatus.observe(
        a.ref,
        title="Original room-temperature note",
        actor="fixture",
        observation_id="original-a",
        observed_at=datetime(2020, 1, 2, tzinfo=UTC),
        conditions={"temperature": "room temperature", "connection": "unconfirmed"},
        note="Reference only. Original A note.",
        attachments=(attachment_a,),
        run_ids=(run.id,),
    )
    current = lab.apparatus.revise(
        a.ref, description="Revised A description", actor="fixture"
    )
    for index in range(21):
        lab.apparatus.observe(
            current.ref,
            title=f"Intermediate note {index}",
            actor="fixture",
            observation_id=f"middle-{index}",
        )
    correction = lab.apparatus.observe(
        current.ref,
        title="Correction of original note",
        actor="fixture",
        observation_id="correction-a",
        supersedes=original.id,
        note="Corrected wording; original retained.",
    )
    lab.apparatus.observe(
        b.ref,
        title="Independent B note",
        actor="fixture",
        observation_id="original-b",
        attachments=(attachment_b,),
    )
    print(
        json.dumps(
            {
                "runId": run.id,
                "a": current.model_dump(mode="json"),
                "b": b.model_dump(mode="json"),
                "original": original.model_dump(mode="json"),
                "correction": correction.model_dump(mode="json"),
            }
        )
    )
