from __future__ import annotations

import math
import struct
import wave
from pathlib import Path


def tone(freq: float, t: float) -> float:
    return 0.45 * math.sin(2.0 * math.pi * freq * t)


def main() -> None:
    sr = 44100
    duration = 12.0
    total = int(sr * duration)

    out_dir = Path("examples")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "example_abx_input.wav"

    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)

        for i in range(total):
            t = i / sr
            left = tone(440.0 + 30.0 * math.sin(2 * math.pi * 0.2 * t), t)
            right = tone(880.0 + 60.0 * math.sin(2 * math.pi * 0.17 * t), t)

            # Add transients and HF content to expose codec behavior.
            burst = 0.20 if (i % 22050) < 180 else 0.0
            noise = (math.sin(2 * math.pi * 6000 * t) + math.sin(2 * math.pi * 9000 * t)) * 0.03
            left = max(-1.0, min(1.0, left + burst + noise))
            right = max(-1.0, min(1.0, right + burst - noise))

            w.writeframesraw(
                struct.pack(
                    "<hh",
                    int(left * 32767.0),
                    int(right * 32767.0),
                )
            )

    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
