"""Spike (a): DawDreamer renders a VST3/LV2 instrument plugin to WAV.

Usage: .venv/bin/python spikes/spike_a_plugin_render.py <plugin path> <out.wav>
Prints load time, param count, peak and RMS. Exit 0 if the audio is not silent and not clipping.
"""
import sys
import time

import numpy as np
from scipy.io import wavfile


def main(plugin: str, out: str) -> int:
    import dawdreamer as daw
    sr = 44100
    eng = daw.RenderEngine(sr, 512)
    eng.set_bpm(120)
    t = time.perf_counter()
    synth = eng.make_plugin_processor("synth", plugin)
    load = time.perf_counter() - t
    params = synth.get_parameters_description()
    for i, n in enumerate([60, 64, 67, 72]):
        synth.add_midi_note(n, 100, i * 0.5, 0.45)
    eng.load_graph([(synth, [])])
    t = time.perf_counter()
    eng.render(3.0)
    rtime = time.perf_counter() - t
    a = eng.get_audio()
    peak, rms = float(np.abs(a).max()), float(np.sqrt((a ** 2).mean()))
    wavfile.write(out, sr, a.T.astype(np.float32))
    print(f"load {load:.2f}s render 3s in {rtime:.3f}s  ins/outs {synth.get_num_input_channels()}/"
          f"{synth.get_num_output_channels()}  params {len(params)}  peak {peak:.3f} rms {rms:.4f}")
    return 0 if 0.01 < peak <= 1.0 else 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2]))
