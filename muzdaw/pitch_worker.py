"""Audio -> notes worker. Runs in the separate basic-pitch venv (Python 3.12).

Do not import muzdaw here: this file runs with `python -I` in another venv.
Usage: python -I pitch_worker.py WAV ONSET FRAME MIN_MS MIN_HZ MAX_HZ
Prints JSON: {"notes": [[start_s, end_s, pitch, amplitude], ...]}
"""
import contextlib
import io
import json
import logging
import sys


def main(argv: list[str]) -> int:
    if len(argv) != 6:
        print(__doc__, file=sys.stderr)
        return 2
    wav = argv[0]
    onset, frame, min_ms = float(argv[1]), float(argv[2]), float(argv[3])
    min_hz = float(argv[4]) or None
    max_hz = float(argv[5]) or None
    logging.disable(logging.WARNING)  # basic-pitch warns about missing TF/CoreML backends
    with contextlib.redirect_stdout(io.StringIO()):  # keep stdout for our JSON only
        from basic_pitch import FilenameSuffix, build_icassp_2022_model_path
        from basic_pitch.inference import predict
        _, _, events = predict(wav, build_icassp_2022_model_path(FilenameSuffix.onnx),
                               onset_threshold=onset, frame_threshold=frame,
                               minimum_note_length=min_ms, minimum_frequency=min_hz,
                               maximum_frequency=max_hz)
    notes = [[round(float(s), 4), round(float(e), 4), int(p), round(float(a), 4)] for s, e, p, a, *_ in events]
    json.dump({"notes": sorted(notes)}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
