"""Spike (b): Carla plays a plugin live (PulseAudio driver -> PipeWire).

Usage: .venv/bin/python spikes/spike_b_carla_live.py [driver] <plugin> [<plugin> ...]
  plugin = path to .vst3 / VST2 .so, or an LV2 URI (lv2:<uri>)
Plays C-E-G on each plugin and prints Carla's output peak while the notes sound.
"""
import sys
import time

sys.path.insert(0, "/usr/share/carla")
from carla_backend import (BINARY_NATIVE, ENGINE_OPTION_PATH_BINARIES,  # noqa: E402
                           ENGINE_OPTION_PATH_RESOURCES, ENGINE_OPTION_PROCESS_MODE,
                           ENGINE_PROCESS_MODE_CONTINUOUS_RACK, PLUGIN_LV2, PLUGIN_VST2, PLUGIN_VST3,
                           CarlaHostDLL)

DRIVERS = ("PulseAudio", "JACK", "ALSA", "SDL")


def main(argv: list[str]) -> int:
    driver = argv.pop(0) if argv and argv[0] in DRIVERS else "PulseAudio"
    h = CarlaHostDLL("/usr/lib/carla/libcarla_standalone2.so", False)
    h.set_engine_option(ENGINE_OPTION_PATH_BINARIES, 0, "/usr/lib/carla")
    h.set_engine_option(ENGINE_OPTION_PATH_RESOURCES, 0, "/usr/share/carla/resources")
    h.set_engine_option(ENGINE_OPTION_PROCESS_MODE, ENGINE_PROCESS_MODE_CONTINUOUS_RACK, "")
    t = time.perf_counter()
    if not h.engine_init(driver, "muzdaw-spike"):
        print("engine_init failed:", h.get_last_error())
        return 1
    print(f"engine {driver} up in {time.perf_counter()-t:.2f}s  sr {h.get_sample_rate()}  "
          f"buffer {h.get_buffer_size()}")
    ok = True
    try:
        for pid, plug in enumerate(argv):
            if plug.startswith("lv2:"):
                added = h.add_plugin(BINARY_NATIVE, PLUGIN_LV2, "", "", plug[4:], 0, None, 0)
            else:
                ptype = PLUGIN_VST3 if plug.endswith(".vst3") else PLUGIN_VST2
                added = h.add_plugin(BINARY_NATIVE, ptype, plug, "", "", 0, None, 0)
            if not added:
                print(f"{plug}: add failed: {h.get_last_error()}")
                ok = False
                continue
            h.set_volume(pid, 0.5)
            time.sleep(0.3)
            for n in (60, 64, 67):
                h.send_midi_note(pid, 0, n, 90)
            peak = 0.0
            for _ in range(20):  # 1 s
                time.sleep(0.05)
                peak = max(peak, h.get_output_peak_value(pid, True), h.get_output_peak_value(pid, False))
            for n in (60, 64, 67):
                h.send_midi_note(pid, 0, n, 0)
            time.sleep(0.5)
            print(f"{plug}: live peak {peak:.3f}  {'SOUND' if peak > 0.01 else 'SILENT'}")
            ok &= peak > 0.01
            h.set_active(pid, False)
    finally:
        h.engine_close()
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1:]))
