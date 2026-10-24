#!/bin/zsh
set -eu
JULIUS_PROJECT_DIR="${0:A:h}"
cd "$JULIUS_PROJECT_DIR"
case "${JULIUS_PROFILE:-cloud}" in
  balanced) PROFILE_BACKEND=ollama; PROFILE_MODEL=qwen3:8b; PROFILE_VOICE=kokoro ;;
  fast) PROFILE_BACKEND=ollama; PROFILE_MODEL=llama3.2:3b; PROFILE_VOICE=kokoro ;;
  cloud) PROFILE_BACKEND=cloud; PROFILE_MODEL=claude-haiku-5-5; PROFILE_VOICE=qwen-mlx
    export JULIUS_KEEP_VOICE="${JULIUS_KEEP_VOICE:-1}"        # no local brain: the voice stays loaded between turns
    export JULIUS_MLX_KEEP_WORKER="${JULIUS_MLX_KEEP_WORKER:-1}" JULIUS_MLX_ASR_KEEP_VOICE="${JULIUS_MLX_ASR_KEEP_VOICE:-1}"
    ;;
  cloud-torch) PROFILE_BACKEND=cloud; PROFILE_MODEL=claude-haiku-5-5; PROFILE_VOICE=qwen-clone   # slower Torch voice, kept as a fallback
    export JULIUS_KEEP_VOICE="${JULIUS_KEEP_VOICE:-1}"
    ;;
  custom-voice) PROFILE_BACKEND=ollama; PROFILE_MODEL=qwen3:4b-instruct-2507-q4_K_M; PROFILE_VOICE=qwen-clone ;;
  conversation-test|mlx-conversation-test)
    PROFILE_BACKEND=ollama; PROFILE_MODEL=qwen3:4b-instruct-2507-q4_K_M; PROFILE_VOICE=qwen-mlx
    if [[ "${JULIUS_PROFILE}" == mlx-conversation-test ]]; then
      PROFILE_BACKEND=mlx; PROFILE_MODEL="$JULIUS_PROJECT_DIR/.cache/julius-mlx-brain"
      export JULIUS_MLX_BRAIN_MEMORY_GB=5
      export JULIUS_MLX_BRAIN_PREPARE=1
    fi
    export JULIUS_CONVERSATION_STYLE="${JULIUS_CONVERSATION_STYLE:-brief}"
    export JULIUS_LLM_BATCH_SIZE="${JULIUS_LLM_BATCH_SIZE:-128}"
    export JULIUS_MLX_KEEP_WORKER="${JULIUS_MLX_KEEP_WORKER:-1}"
    export JULIUS_MLX_ASR_KEEP_VOICE=0
    export JULIUS_ASR_RELEASE_LLM=1
    export JULIUS_ASR_RESIDENT=0
    ;;
  mlx) PROFILE_BACKEND=mlx; PROFILE_MODEL=mlx-community/Qwen3-8B-4bit; PROFILE_VOICE=kokoro ;;
  *) print -u2 'Use JULIUS_PROFILE=cloud, cloud-torch, balanced, fast, custom-voice, conversation-test, mlx-conversation-test, or mlx'; exit 2 ;;
esac
export JULIUS_BACKEND="${JULIUS_BACKEND:-$PROFILE_BACKEND}"
export JULIUS_MODEL="${JULIUS_MODEL:-$PROFILE_MODEL}"
export JULIUS_FALLBACK_MODEL="${JULIUS_FALLBACK_MODEL:-llama3.2:3b}"
export JULIUS_VISION="${JULIUS_VISION:-0}"
export JULIUS_TTS="${JULIUS_TTS:-$PROFILE_VOICE}"
exec /opt/miniconda3/envs/torch/bin/python -u "$JULIUS_PROJECT_DIR/julius_candidate.py"
