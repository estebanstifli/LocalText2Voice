# RTX 50-Series Windows Beta Test

This test validates the Qwen3-TTS Blackwell runtime on a real `sm_120` GPU.
Passing the same runtime on an RTX 30/40 card confirms package compatibility,
but it does not certify the Windows Blackwell kernels.

## Prerequisites

- Windows 10 22H2 or a supported Windows 11 release.
- An RTX 50-series GPU.
- NVIDIA driver 580.88 or newer.
- The LocalText2Voice beta build containing the profiled Qwen runtime.

Do not replace PyTorch manually inside the application directory. The beta
installs the Blackwell runtime separately from the CUDA 12.6 runtime used by
older GPUs.

## Test

1. Open **Settings > TTS Engines > Qwen3 TTS**.
2. Select **Repair/Update** or **Install**.
3. Confirm that the installation log selects:

   ```text
   CUDA 13.0 / PyTorch 2.11 (Blackwell)
   ```

4. Generate a short sentence with Qwen3-TTS using `Device: Auto` and
   `dtype: Auto`.
5. Confirm that a non-empty WAV is created and can be played.
6. If Generation Review is enabled, test Faster Whisper separately. Use
   `CPU/int8` if its independent CTranslate2 CUDA backend rejects `float16`.

The Qwen worker should report values equivalent to:

```text
PyTorch: 2.11.0+cu130, CUDA build: 13.0
CUDA device: NVIDIA GeForce RTX 50...
Qwen device selected: cuda
Qwen3 TTS worker ready
```

## Result to Send Back

Copy the complete block from:

- System GPU detection.
- Selected Qwen runtime profile.
- Runtime installation and validation.
- Qwen worker startup through WAV creation or failure.

Also include:

- Exact GPU model.
- NVIDIA driver version from `nvidia-smi`.
- Windows version and build.
- Whether the Qwen WAV was created.
- Whether Faster Whisper was enabled, and its selected device/compute type.

The installer executes a real CUDA tensor operation before activating the
profile. If that probe fails, it preserves the diagnostic reason and activates
the isolated CPU profile instead of modifying runtimes used by other engines.
