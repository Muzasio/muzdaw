# How to use muzdaw

muzdaw is a music maker (a DAW) that works like FL Studio.
You make **patterns** (short loops of notes), put them on the **playlist** (the song timeline), and press **Space** to listen.

Open this guide any time with **F1** (Help → How to use muzdaw).

---

## 1. Start

Open a terminal in the muzdaw folder and type:

```
.venv/bin/muzdaw-ui
```

- The **first time**, you get a new song with 5 ready instruments: kick, clap, hat, bass, piano.
- **After that**, it opens your last song again.
- Every change **saves itself**. There is no "Save" button you have to remember.

---

## 2. The window

| Part | Key | What it is for |
|---|---|---|
| **Playlist** (top) | F5 | The song timeline: which pattern plays when |
| **Channel Rack** | F6 | Your instruments + a quick beat maker (16 boxes) |
| **Piano Roll** | F7 | Draw melodies and chords for one instrument |
| **Mixer** | F9 | Volume, left/right, and effects (reverb, delay, ...) |
| **Toolbar** (very top) | | Play, Loop, Metronome, Tempo, Master volume, Export WAV, typing keyboard |

The three bottom panels (Channel Rack, Piano Roll, Mixer) are tabs: click a tab or press its key.

---

## 3. Make a beat (Channel Rack, F6)

Each row is one instrument. The 16 boxes are 16 steps (one bar).

| Do this | Result |
|---|---|
| Click a box | It lights up = that instrument plays on that step |
| Click it again | It turns off |
| Green dot on the left | On = you hear it, grey = muted |
| The two small knobs | Volume and left/right (pan) |
| Click a name | Selects that instrument (the Piano Roll and keyboards use it) |
| Double-click a name | Opens it in the Piano Roll |
| Right-click a name | Menu: Open in Piano Roll, **Rename**, **Delete instrument** (Ctrl+Z brings it back) |

**First beat to try:**

| Row | Light up boxes |
|---|---|
| kick | 1, 5, 9, 13 |
| clap | 5, 13 |
| hat | 1, 3, 5, 7, 9, 11, 13, 15 |

Then press **Space** to hear it, and Space again to stop.

**Add an instrument:** pick a sound in the list next to **+ channel** (piano, strings, flute, surge-pad, ...), then click **+ channel**.
**New pattern:** click **+ pattern**. The **Pattern** list picks which pattern you are editing.

---

## 4. Draw a melody (Piano Roll, F7)

It shows the notes of the **selected instrument** in the **selected pattern**.

| Do this | Result |
|---|---|
| Left click on the grid | Add a note (it gets selected) |
| Click a note | Select it (white border) |
| **Shift** + click a note | Add it to the selection (or take it out) |
| **Ctrl** + drag on an empty spot | Draw a box: selects every note inside |
| Drag a note | Move it and all selected notes (up/down = higher/lower, left/right = earlier/later) |
| Drag the right edge of a note | Make it (and all selected notes) longer or shorter |
| Right click a note | Delete it (all selected notes, if it is selected) |
| Hold **right** button and sweep over notes | Erases every note you pass over (like FL Studio) |
| Hold **Alt** while you click or drag | No snapping (free placement) |
| **Snap** list | How notes line up: 1/4 beat (default), 1/8, 1/3 (triplets), ... |
| **Quantize** button (or **Alt+Q**) | Pull all notes of this instrument onto the snap grid |
| **Zoom** list | Make the grid wider or narrower |
| **🔊** button | On = you hear each note while you place or move it |

**Keys in the Piano Roll** (click the grid first):

| Key | What it does |
|---|---|
| **Ctrl+A** | Select all notes |
| **Ctrl+C** / **Ctrl+V** | Copy / paste. Paste lands right after the copied notes; press Ctrl+V again to keep going |
| **Ctrl+D** | Duplicate the selected notes (one copy right after them) |
| **Delete** | Delete the selected notes |
| **Up / Down** | Selected notes one semitone higher / lower (hold **Shift** = one octave) |
| **Left / Right** | Selected notes one snap step earlier / later |
| **Esc** | Clear the selection |

**Velocity (how hard each note plays):** the strip under the grid shows one bar per note.
Drag up or down over a bar to make that note louder or softer (only the selected notes, if you selected some).
Or hold **Alt** and turn the mouse wheel over a note.

It opens around **C4 – C5** (middle of the piano), or around your notes.

The grid fills the whole panel. The **orange line** marks the end of the pattern; drawing notes past it makes the pattern longer (in whole bars).

**Plugin sounds** (surge-…, odin2, dexed, cardinal, zyn) play live: the first key after you pick one of them can take 1–2 seconds while it loads, then every key is instant.

---

## 5. Try notes before you place them

### Click the piano keys (left side of the Piano Roll)

| How you click | What you hear |
|---|---|
| Quick click | A short hit |
| Click and **hold** | The note keeps sounding until you let go, then fades |
| Click the **left end** of a key | Soft, light |
| Click the **right end** of a key (next to the grid) | Hard, heavy, sharp |
| Hold and drag up/down | Slide over the keys |

Clicking the keys only plays sound. It does not add notes to your song.

### Play with your computer keyboard (typing keyboard)

While the muzdaw window is active, your letter keys play the selected instrument.

| Row | Keys | Notes (default) |
|---|---|---|
| Top | q w e r t y u i o p [ ] | C5 to B5 (one full octave) |
| Middle | a s d f g h j k l ; ' | C4 to A#4 |
| Bottom | z x c v b n m , . / | C3 to A3 |

| Control | What it does |
|---|---|
| **⌨ Keys on / off** (toolbar) | Turn the typing keyboard on or off |
| **PageUp / PageDown** | All rows one octave up / down |
| **⚙** (toolbar) or Options → Typing keyboard | Start note of each row (e.g. top row = C4, bottom row = C2), velocity (1 soft – 127 hard), octave shift, Restore Defaults |

- Hold a key = long note. Several keys at once = a chord.
- It does nothing while you type in a box (like Tempo), so typing numbers is safe.
- Your settings are remembered.

### Record what you play (● Rec)

1. Pick the instrument (Channel Rack) and the pattern.
2. Click **● Rec** in the toolbar (or **Ctrl+Shift+R**).
3. You hear 4 clicks (count-in), then recording starts and **the pattern plays** (all its instruments), so you play along with the beat. A red line runs through the Piano Roll.
4. Play with the typing keyboard. When the pattern ends, it starts again from the beginning and keeps adding your notes (loop recording).
5. Press **Space** or **● Rec** again to stop. Your notes appear in the Piano Roll, selected.

- **Quantize rec** (toolbar, on by default) snaps the take to the Piano Roll snap grid.
- Tick **Metronome** to keep hearing clicks while you record.
- One take = one undo step (Ctrl+Z removes the whole take).

**Music basics:** one octave = 12 notes (7 white + 5 black keys): C C# D D# E F F# G G# A A# B, then C again one octave higher. Middle C = C4.

---

## 5b. Audio samples (your own WAV, MP3, FLAC, OGG files)

| What | How |
|---|---|
| **Sample instrument** (drum hit, SFX, one-shot) | Channel Rack → **+ sample** (or drag files from your file manager onto the Channel Rack). It becomes a row: click steps, or draw notes. **C4** plays it at its own pitch; higher notes play it higher and shorter |
| **Audio clip** (music, vocals, long SFX) | Drag an audio file from your file manager onto the **Playlist** (it lands where you drop it), or File → **Add audio clip to Playlist…**. Drag it to move, right-click to remove |

- Files are used where they are (never copied or changed), so keep them in place.
- Audio clips play in **SONG** mode and in the export.

## 6. Build the song (Playlist, F5)

| Do this | Result |
|---|---|
| Left click an empty spot | Places the selected pattern there (snaps to the bar) |
| Drag a pattern block | Move it |
| Right click a block | Remove it |
| Click a block | Selects that pattern for editing |
| Double-click a block | Opens that pattern in the Piano Roll |

Use several tracks (rows) for patterns that play at the same time (e.g. drums on Track 1, melody on Track 2).

---

## 7. Mix (Mixer, F9)

Every instrument has a strip with volume, left/right, mute and an **output** (master or a bus).

| Do this | Result |
|---|---|
| Pick an effect in the list (reverb, delay, eq-peak, compressor, ...) and click **+** | Adds the effect to that strip |
| Click an effect in the strip's list | Its settings show below (knobs) |
| **−** | Remove the effect |
| **on/off** | Bypass the effect (hear without it) |
| **↑ / ↓** | Change the effect order |
| **+ bus** | A group strip: send several instruments into it (e.g. all drums) |

**Knobs:** drag up/down (hold **Shift** = fine), mouse wheel, double-click = back to default,
or click the number box and type an exact value: `0.75`, `-6dB`, `440Hz`, `30L`, `C`.

---

## 8. Listen and export

| Do this | Key | Result |
|---|---|---|
| Play / Stop | **Space** | Plays (see PAT / SONG below) |
| **PAT / SONG** button | **Ctrl+L** | **PAT** = only the selected pattern, on repeat (for building a beat). **SONG** = the whole Playlist |
| Loop song | | SONG mode starts again at the end |
| Metronome | | Adds a click on every beat |
| Tempo knob | | Song speed in BPM |
| Master knob | | Overall volume |
| Export WAV (toolbar) or File → Export audio | **Ctrl+R** | Saves the music as a .wav file |
| File → Export MIDI | | Saves the notes as a .mid file (for other music programs) |

- What you hear is **exactly** what Export WAV makes (same sounds, same mix).
- **Change things while it plays**: add a note, move a knob, add an effect — you hear it a moment later (big songs: up to about a second).
- In PAT mode, picking another pattern plays that one.
- Green **playhead**: a line in the Playlist and Piano Roll, and the Channel Rack outlines the step that is playing.
- The first play of a big song takes a few seconds while muzdaw prepares the sound ("preparing…"); after that, changes are quick.

Turn your speakers down a little before the first play.

---

## 9. Files

| Menu item | Key | What it does |
|---|---|---|
| New | **Ctrl+N** | New song (with the 5 ready instruments) |
| Open... | **Ctrl+O** | Open a song (.muz.json file) |
| Save | **Ctrl+S** | Changes already save themselves; a new song asks for a name and folder |
| Save as... | **Ctrl+Shift+S** | Keep the song under a new name / folder |
| Import notes from audio... | **Ctrl+Shift+A** | Pick a WAV recording; its notes go into the selected instrument + pattern |

A new song lives in a hidden folder until you use **Save as**. The window title tells you when a song is not saved yet.

muzdaw remembers its window size, place, and how you arranged and sized the panels, and opens the same way next time.

---

## 10. Undo

| Key | What it does |
|---|---|
| **Ctrl+Z** | Undo the last change |
| **Ctrl+Y** or **Ctrl+Shift+Z** | Redo |

---

## 11. All keys

| Key | What it does |
|---|---|
| Space | Play / stop (stops a recording) |
| Ctrl+L | PAT / SONG mode |
| Ctrl+Shift+R | Record the typing keyboard (● Rec) |
| F1 | This guide |
| F5 / F6 / F7 / F9 | Playlist / Channel Rack / Piano Roll / Mixer |
| Ctrl+N / Ctrl+O / Ctrl+S / Ctrl+Shift+S | New / Open / Save / Save as |
| Ctrl+R | Export WAV |
| Ctrl+Shift+A | Import notes from audio |
| Ctrl+Z / Ctrl+Y | Undo / Redo |
| Alt+Q | Quantize (Piano Roll) |
| Ctrl+A / Ctrl+C / Ctrl+V / Ctrl+D | Piano Roll: select all / copy / paste / duplicate |
| Delete, arrows, Esc | Piano Roll: delete / move selected notes / clear selection |
| PageUp / PageDown | Typing keyboard octave up / down |
| q-row, a-row, z-row | Play notes (typing keyboard on) |
| Ctrl+Q | Quit |

---

## 12. Command line (for scripts)

Everything can also be done from the terminal, e.g.:

```
muzdaw info song.muz.json                         # what is in the song
muzdaw render song.muz.json song.wav              # export WAV
muzdaw quantize song.muz.json "Pattern 1" --grid 1/16
muzdaw rec2midi song.muz.json take.wav piano REC  # recording -> notes
muzdaw record-midi song.muz.json piano REC --bars 4   # record a MIDI keyboard
muzdaw --help                                     # all commands
```

(`muzdaw` lives in the muzdaw folder, in `.venv/bin/`.)

---

## 13. Problems

| Problem | Fix |
|---|---|
| A button does nothing | Look at the bottom line of the window: it says why (e.g. "add a channel first") |
| Piano Roll says "(no channel)" | Add or click an instrument in the Channel Rack (F6) |
| No sound on Space | In PAT mode: does the selected pattern have notes? In SONG mode: is the pattern on the Playlist (F5)? Is the green dot on? Is Master turned up? |
| "audio file not found" | The sample/clip file was moved or renamed: put it back, or add it again |
| Typing keys play nothing | Is **⌨ Keys on**? Is the muzdaw window active? Is the cursor in a text box? |
| A Surge sound is slow the first time | Normal: about 1 second the first time, then instant |
| Something went wrong | Ctrl+Z undoes it |
