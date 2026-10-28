# \<title\>

One line: what the engine cannot do today, and for whom.

## Use case

The user story that needs the change: the concrete workload, on which hardware and model.

## Proposal

What changes in the engine: modules, flags, wire format, refusals. Name what basal-ui will call
(`engine.feature("<name>")`) and what stays out.

## Acceptance check

Stated before implementation, in the maintainer's style: the measurable criteria that decide the PR — byte-identical
outputs, agreement rates, covered engines — agreed with the issue.

## Upstream

Links to the issue and the PR on rkinas/basal, and the release scheduled to ship the change.

## Measurements

The evidence, as a table (hardware, model, check, result):

| check | result |
|---|---|

## Outcome

Merged, released or withdrawn — and what basal-ui does with it: feature probe removed, `SUPPORTED` bumped, control
kept or dropped. Filled in when the release lands.
