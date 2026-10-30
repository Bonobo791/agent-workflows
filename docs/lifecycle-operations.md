# Worker lifecycle operations

The control socket supports manual stop and restart guidance in either mode. Automatic control is optional and defaults off. No live controller, observer, token or worker is configured by this repository.

## Policy and mode

The trusted operator configuration identifies the exact Coolify HTTPS origin, service and application IDs, controller implementation, permission scope and independent verifier implementation. Its policy hash binds all six fields. `lifecycle_config` displays the policy and hash without credentials. Automatic mode requires a one-use `enable_lifecycle` approval for run `controller` and that exact hash, plus a trusted verifier of already completed target, permission and lifecycle tests. `enable_lifecycle_auto` consumes that approval; `disable_lifecycle_auto` disables it. `restore_lifecycle_auto` can restore a previously approved policy only after the trusted verifier passes again. No control action creates credentials, tests against production or changes Coolify configuration.

The observer must provide incarnation identity, an independently verified stop-and-fence result, replacement identity verification, and a real atomic or externally enforced `run_if_current` / `run_if_stopped` guard. These are trusted infrastructure contracts, not values accepted from a model or a user's free-text assertion. The Coolify API's documented response does not provide those guarantees by itself. The fixture observer in tests is not a production implementation.

## Manual emergency stop

`manual_stop` records the request, fences workflow writes and returns the exact origin, service ID, application ID and original worker incarnation. Before stopping the application through the approved operator channel, independently verify that the target still identifies that incarnation. Independently verify that incarnation and all descendants have terminated and that it cannot restart, then call `confirm_stopped` with the evidence locator accepted by the configured independent observer. A request or a Coolify 200 is not stop confirmation. An expired queue remains uncertain and blocks dispatch until independently resolved. Manual guidance is available even while automatic mode is enabled.

After a verified stop, `manual_restart` gives target and old-incarnation guidance. `confirm_restarted` requires an independently verified, distinct replacement incarnation. The old lifecycle record closes; register the replacement against a new run before using it. A restarted worker does not automatically replay an uncertain job or external mutation.

## Automatic control

`automatic_stop` and `automatic_restart` use only the configured controller and target after the policy has been explicitly approved and revalidated. The supplied Coolify adapter calls the documented per-service-application stop and restart endpoints. A successful HTTP response records `requested`; it does not release a queue lease or prove the worker is gone. Timeouts, process crashes and ambiguous responses remain `uncertain`; duplicate requests do not retry. The target remains reserved across policies until a trusted observer confirms the transition. Manual guidance remains available to resolve an uncertain operation.

In-flight controller calls are tracked durably. After an interrupted or timed-out call, resolving it requires an independent verifier to establish that the caller and remote request are quiescent; a timeout alone is insufficient. Disabling automatic mode is immediate. A subsequent explicit approval may re-enable it after the policy verifier passes again.

No live operation can be enabled safely until the target-scoped controller, its actual team-level permissions, credential separation, guarded lifecycle behavior, incarnation observer and container cleanup have been independently tested and approved. Controller credentials must remain outside job containers. The current worker bootstrap does not configure any of these providers; the APIs are locally testable integration code, not a completed deployment.
