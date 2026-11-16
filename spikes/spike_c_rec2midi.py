"""Spike (c): audio -> notes with basic-pitch (ONNX model, no TensorFlow).

Usage (run with the pitch venv):
    .venv-pitch/bin/python spikes/spike_c_rec2midi.py <input.wav> <expected.mid>
Prints the detected notes and how many expected notes were found.
"""
import sys
from pathlib import Path

import pretty_midi
from basic_pitch import FilenameSuffix, build_icassp_2022_model_path
from basic_pitch.inference import predict


def main(wav: Path, expected_mid: Path) -> int:
    model = build_icassp_2022_model_path(FilenameSuffix.onnx)
    _, midi, _ = predict(str(wav), model)
    got = sorted((round(n.start, 2), n.pitch) for i in midi.instruments for n in i.notes)
    want = sorted((round(n.start, 2), n.pitch)
                  for i in pretty_midi.PrettyMIDI(str(expected_mid)).instruments for n in i.notes)
    # a note counts as found if same pitch starts within 60 ms
    found = sum(any(p == wp and abs(t - wt) <= 0.06 for t, p in got) for wt, wp in want)
    extra = len(got) - found
    print(f"expected {len(want)} notes, found {found}, extra {extra}")
    print("detected:", got)
    return 0 if found == len(want) else 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
