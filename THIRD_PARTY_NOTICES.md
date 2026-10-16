# Third-party notices

## Anatomy

- Upstream: https://github.com/wheresryan22/anatomy
- Author/copyright: Ryan, 2026
- License: MIT; the original notice is preserved in [vendor/anatomy/LICENSE](vendor/anatomy/LICENSE).
- Pinned commit: `bc943e40c59998be5da61f9d39c6a983b1116099`.
- Included files and SHA-256 hashes: [upstream.lock.json](upstream.lock.json).

The selected geometry kit, references, example and supporting tools are included under their original license. Vendored files are unchanged. The new video workflow, TTS adapters, runtime, theme data and validation tools are maintained by gnipbao. This project is not an official Anatomy product.

## Services and optional integrations

Doubao TTS is an external service requiring the user's own account and credentials; no credentials or service entitlement are distributed here. grill-me/grilling is an optional external Skill; this repository documents a composition protocol without bundling its prompt. The user-provided reference video, its soundtrack and imagery are not distributed.

## Optional local model runtime

The local adapter targets [mlx-audio](https://github.com/Blaizzy/mlx-audio) and the community [VoxCPM2-8bit conversion](https://huggingface.co/mlx-community/VoxCPM2-8bit) of [OpenBMB VoxCPM2](https://github.com/OpenBMB/VoxCPM). Model weights are Apache-2.0 according to the model card and are not included here. Optional Python packages are installed separately into a dedicated environment and retain their own licenses. The pinned model revision, download URLs and file digests are recorded in `models/voxcpm2-8bit.manifest.json`; no private reference recording is distributed.
