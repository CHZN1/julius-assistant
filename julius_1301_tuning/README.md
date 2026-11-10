# Julius voice reference (seed 1301)

`1301_a_balanced.wav` and `1301_b_more_lift.wav` are the two reference clips the voice is cloned from (`julius/audio/voice_identity.py`: `BALANCED_REFERENCE`, `LIFT_REFERENCE`). Both were **generated** with Qwen3-TTS VoiceDesign (`Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`, seed 1301). They are synthetic speech; no recording of a real person is involved.

- Transcript of both clips: `REF_TEXT` in `julius/audio/voice_identity.py`.
- Licence: the Qwen3-TTS models and code are Apache-2.0, which places no restriction on redistributing audio they generate. The clips are covered by this repository's MIT licence.
- The runtime voice (`mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16`) clones from these clips. To use another voice, replace the two files and `REF_TEXT`.
- To regenerate similar candidates yourself: `python scripts/qwen3_voice_design.py` (downloads the 1.7B VoiceDesign model). Seeded generation is not bit-for-bit reproducible across hardware, so the shipped files are the reference.
