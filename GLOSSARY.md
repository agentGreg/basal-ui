# basal-ui

basal-ui wraps the Engine in a local browser app: it picks a basal model for your hardware, serves it and lets you
try it.

## Language

**Decision**:
The result of evaluating a request's questions against its supplied context, including the evidence and uncertainty available for those answers.

**Replay**:
Preparing a new request from a recorded decision request for review before submission. A record missing required attachments cannot be replayed completely.

**Active load settings**:
The settings used by the currently running model instance.
_Avoid_: Saved settings when describing a running instance

**Saved load settings**:
The settings retained for a future model load; they may differ from the active load settings.

**Shipped calibration**:
Calibration supplied with a model for a particular execution configuration.

**Selected calibration**:
Calibration explicitly chosen for a model's execution configuration, potentially replacing its shipped calibration.

**Backend**:
A local inference runtime and its dependencies, such as PyTorch or MLX, that the Engine runs a model with. A checkpoint format is not a backend group.
_Avoid_: engine (that is the Engine)

**Execution mode**:
A particular way Basal runs a model with a backend, such as eager or fast for PyTorch.

**Machine compatibility**:
Whether basal-ui supports a backend on the app host's platform and hardware. Separate from whether its packages are installed.

**Model compatibility**:
Whether a model's architecture and format can run with a backend and execution mode after required dependencies are installed. Separate from memory fit.

**Inference test**:
An explicit, isolated check of one downloaded model, revision and execution mode using a fixed short decision. Passing is not a general accuracy or performance certification.

**Engine**:
The upstream basal distribution (library and command-line tools) that basal-ui wraps.
_Avoid_: backend, core, fork

**Engine release**:
A tagged version of the Engine.

**Supported engine range**:
The Engine releases a basal-ui release is tested with and accepts.

**Feature probe**:
basal-ui's check of whether the running Engine provides a capability, deciding whether the matching control is offered.

**Experimental engine**:
An Engine installed from a proposal branch rather than a release; outside the supported range unless the user opts in.

**Engine proposal**:
A change to the Engine submitted upstream, tracked from use case to merged release.

**Research note**:
The record of an Engine proposal's use case, measurements and outcome.
