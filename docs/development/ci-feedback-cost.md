# CI core feedback cost

This is a bounded two-shard experiment for #520, based on public main
`322caf6f78ba4dd9d7852fc15a16b3129469e18c`. It changes scheduling, not test tiers,
assertions, deadlines or retries. Both core shards are required by the existing
CI gate; each keeps two pytest workers and diagnostics. The pandas adapter check
runs once, after shard 1. Ubuntu quality/UI/docs and Windows/macOS smoke remain
required on PRs, merge groups and main.

## Baseline

GitHub job/step timestamps give seconds below. Elapsed is first job start through
CI gate completion, including inter-job scheduling gaps, excluding initial queue
wait. Runner seconds sum job durations and are not rounded billable minutes.

| Run | Event | Core job | Test step | Elapsed | Runner seconds |
| --- | --- | ---: | ---: | ---: | ---: |
| [37602289733](https://github.com/scopecat-project/scopecat/actions/runs/37602289733) | #908 PR | 382 | 363 | 389 | 714 |
| [37607723406](https://github.com/scopecat-project/scopecat/actions/runs/37607723406) | #909 PR | 420 | 397 | 427 | 761 |
| [37628689684](https://github.com/scopecat-project/scopecat/actions/runs/37628689684) | #913 PR | 427 | 410 | 435 | 755 |
| [37630321335](https://github.com/scopecat-project/scopecat/actions/runs/37630321335) | merge group | 398 | 382 | 406 | 700 |
| [37631300685](https://github.com/scopecat-project/scopecat/actions/runs/37631300685) | main push | 327 | 312 | 335 | 669 |

The last two runs use the same SHA, yet core differs by 71 seconds. These samples
do not establish continuing regression. UI took 104–116 seconds, quality 60–89,
docs 25–30, Windows smoke 67–89 and macOS smoke 30–45. Core was always the critical
path. Reusing a UI build or removing a few setup seconds would not resolve it.

The last run's `tests-ubuntu-latest-core` timing artifact records 416 files,
4054 passing cases, 290.12 seconds of pytest wall time, 18.95 seconds to collection
ready and 522.45 summed setup/call/teardown seconds. Three existing conditional
skips are the Windows install location, native macOS delegate signature and the
parameterized capture-import case. The baseline is not an all-platform pass;
this change neither adds skips nor loosens their conditions. Platform smoke
continues requiring actual passes with its existing no-skip runner.

## Bounded change

Reuse the existing deterministic file sharder. Update measured core weights for
files taking at least five summed seconds; smaller/new files retain default
weights. Under the old weights the proposed two shards would account for
149.6/372.8 measured phase seconds. The updated weights distribute all 416 files
as 202/214 with 256.8/265.6 phase seconds. These are projections from one sample,
not measured shard wall times. Tests assert that core shards are nonempty,
disjoint and exactly cover core; qualification coverage remains unchanged.

This trades a second runner's setup and collection for shorter feedback. It does
not claim less computation. File weights are scheduling hints, not coverage
filters; future files remain classified by the existing tier rules. No extra
acceptance suite, runner purchase or cross-job environment cache is introduced.

A fresh unchanged-main dispatch and candidate runs compare the same source tests
and locked dependencies on hosted Ubuntu with two workers per job. Record cache,
job, phase, wall and outcome evidence before interpreting a speedup. Hosted-runner
variation prevents precise causal attribution from a single pair. The five-minute
target remains a measurement target, not a timeout or a guaranteed result.
