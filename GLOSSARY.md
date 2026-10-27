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
