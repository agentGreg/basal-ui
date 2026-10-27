# Basal decisions and serving

Basal evaluates questions against supplied context and serves the resulting decisions through its interfaces.

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
A local inference engine and its dependencies, such as PyTorch or MLX. A checkpoint format is not a backend group.

**Execution mode**:
A particular way Basal runs a model with a backend, such as eager or fast for PyTorch.

**Machine compatibility**:
Whether this Basal integration supports an engine on the app host's platform and hardware. Separate from whether its packages are installed.

**Model compatibility**:
Whether a model's architecture and format can run with an engine and execution mode after required dependencies are installed. Separate from memory fit.

**Inference test**:
An explicit, isolated check of one downloaded model, revision and execution mode using a fixed short decision. Passing is not a general accuracy or performance certification.
