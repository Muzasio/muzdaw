"""History store (one gzip file per step) + speed guard for big songs."""
import gzip
import json
import os
import random
import time

import pytest

from muzdaw import history
from muzdaw.model import Channel, Clip, Note, Pattern, Project, ProjectError, dumps, load, save


def song(tempo=120.0):
    p = Project(tempo=tempo)
    p.channels = [Channel("a", "sine")]
    p.patterns = [Pattern("A", 4, {"a": [Note(60, 0, 1)]})]
    p.playlist = [Clip("A", 0)]
    return p


def test_commit_undo_redo_files(tmp_path):
    f = tmp_path / "s.muz.json"
    save(song(), f)
    for t in (100, 110, 120.5):
        history.commit(f, load(f), song(t), f"tempo {t}")
    d = tmp_path / "s.muz.json.history.d"
    assert oct(d.stat().st_mode & 0o777) == "0o700"
    assert len(list((d / "undo").glob("*.json.gz"))) == 3
    assert history.undo(f) == "tempo 120.5" and load(f).tempo == 110
    assert history.undo(f) == "tempo 110" and load(f).tempo == 100
    assert history.redo(f) == "tempo 110" and load(f).tempo == 110
    history.commit(f, load(f), song(90), "tempo 90")  # new edit clears redo
    with pytest.raises(ProjectError):
        history.redo(f)


def test_limit_and_broken_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "LIMIT", 5)
    f = tmp_path / "s.muz.json"
    save(song(), f)
    for t in range(60, 72):
        history.commit(f, load(f), song(t), f"t{t}")
    undo = tmp_path / "s.muz.json.history.d" / "undo"
    files = sorted(undo.glob("*.json.gz"))
    assert len(files) == 5
    files[-1].write_bytes(b"not gzip")  # broken newest entry is skipped, not fatal
    assert history.undo(f) == "t70" and load(f).tempo == 69  # state before t70


def test_migrates_old_single_file(tmp_path):
    f = tmp_path / "s.muz.json"
    save(song(130), f)
    old = tmp_path / "s.muz.json.history.json"
    old.write_text(json.dumps({"undo": [{"label": "old edit", "state": song(80).to_dict()}], "redo": []}))
    assert history.undo(f) == "old edit" and load(f).tempo == 80
    assert not old.exists()


def test_before_text_is_used_as_is(tmp_path):
    f = tmp_path / "s.muz.json"
    save(song(), f)
    before = load(f)
    history.commit(f, before, song(99), "t", before_text=dumps(before))
    entry = next((tmp_path / "s.muz.json.history.d" / "undo").glob("*.gz"))
    head, state = gzip.decompress(entry.read_bytes()).decode().split("\n", 1)
    assert json.loads(head)["label"] == "t" and state == dumps(before)


def test_clone_and_to_dict_match_slow_versions():
    import copy
    from dataclasses import asdict
    p = song()
    p.buses = []
    p.master_fx = [{"type": "reverb", "params": {"room_size": 0.5}, "bypass": False}]
    assert p.clone() == copy.deepcopy(p) and p.to_dict() == asdict(p)
    c = p.clone()
    c.patterns[0].notes["a"][0].pitch = 1
    c.master_fx[0]["params"]["room_size"] = 0.9
    assert p.patterns[0].notes["a"][0].pitch == 60 and p.master_fx[0]["params"]["room_size"] == 0.5


def test_cli_edits_still_undo(tmp_path):
    from muzdaw.cli import main
    f = tmp_path / "s.muz.json"
    save(song(), f)
    assert main(["tempo", str(f), "140"]) == 0
    assert main(["undo", str(f)]) == 0 and load(f).tempo == 120
    assert main(["redo", str(f)]) == 0 and load(f).tempo == 140


def test_big_song_edits_are_fast(tmp_path):
    """UI edit path on 24 channels x 12k notes: was 0.8-1.5 s per click before the history rewrite."""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from muzdaw.ui.app import MainWindow
    app = QApplication.instance() or QApplication([])
    random.seed(1)
    p = Project()
    p.channels = [Channel(f"ch{i}", "sine") for i in range(24)]
    p.patterns = [Pattern(f"P{j}", 16, {f"ch{i}": [Note(random.randint(40, 80), k * 0.25, 0.25) for k in range(64)]
                                        for i in range(24)}) for j in range(8)]
    p.playlist = [Clip(f"P{j % 8}", j * 16, j % 6) for j in range(64)]
    f = tmp_path / "big.muz.json"
    save(p, f)
    w = MainWindow(f)
    w.resize(1400, 950)
    w.show()
    app.processEvents()

    def worst(fn, n=4):
        ts = []
        for _ in range(n):
            t = time.perf_counter()
            fn()
            app.processEvents()
            ts.append(time.perf_counter() - t)
        return max(ts)

    assert worst(lambda: w.rack.toggle("ch0", 3)) < 0.3
    assert worst(w.doc.undo) < 0.3
    assert worst(lambda: w.doc.select(channel="ch5")) < 0.15
    size = sum(os.path.getsize(os.path.join(r, x)) for r, _, fs in os.walk(str(f) + ".history.d") for x in fs)
    assert size < 2_000_000
    w.close()
    w.deleteLater()
    app.processEvents()
