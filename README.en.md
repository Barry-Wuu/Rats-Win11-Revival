# Rats! — Win11 Revival

<a href="README.md">简体中文</a> ｜ <b>English</b>

Bringing **Rats!** (1996) back to life on modern Windows 11, with a graphical level selector that lets you jump to any of the 1–40 levels.

> **Rats!** is a free Windows strategy game by **Sean O'Connor**, released in 1996:
> place bombs, poison gas, radioactive waste, gender-changing chemicals and other
> gadgets in a maze to wipe out the rats before they breed and overrun it.
> The original has a **button hit-testing bug** in the main menu, which makes the
> chapter buttons for the last three chapters unresponsive.

## Quick Start

```
Double-click Rats.exe        # just play
Double-click 选关器.bat       # open the level selector (levels 1–40)
```

> If you don't have Python, run `python 选关器.py` instead (requires Python 3).
> The selector first kills any running Rats.exe, then launches the game and waits for
> the main window. Enter a level (1–40) and click the jump button.

## What Was Fixed

| # | Problem | Fix | Status |
|---|---|---|---|
| 1 | **Chapter buttons for the last three chapters don't respond**: the main-menu button hit test is broken (`i` never increments; the whole loop is missing), so the Chapter 2/3/4 buttons are dead | The selector bypasses the hit test and sends `WM_COMMAND(0xD2 + i)` directly | ✅ |
| 2 | **Shareware limit blocks the last three chapters**: the game is a 30-level freeware build (`DLG_DEMO`); the original shows a "trial over" dialog when `clear counter > 7 AND current level index >= 9`, locking you out after chapter 1 | Patched `Rats.exe` to raise the threshold (9 → 99); the selector needs no in-memory handling | ✅ |
| 3 | **No level-select mechanism**: the chapter-switch code hard-codes the level index to the first level of each chapter (0/10/20/30) | The selector rewrites the corresponding branch's imm32 at runtime, jumping to any level (1–40), then restores it | ✅ |

## How the Selector Works

The main-menu button hit test is broken, but the **actual chapter-switch code is intact**.
The four chapter branches hard-code the level index to the first level of each chapter:

```
0x40983C  mov [0x4199A8], 0     ; Chapter 1 -> level 1
0x409868  mov [0x4199A8], 0xA   ; Chapter 2 -> level 11
0x409894  mov [0x4199A8], 0x14  ; Chapter 3 -> level 21
0x4098C0  mov [0x4199A8], 0x1E  ; Chapter 4 -> level 31
```

`选关器.py` does the following:

1. Locates the imm32 address of `mov [0x4199A8], imm32` in the target chapter's branch
2. Uses `VirtualProtectEx` to temporarily unprotect the code section, then `WriteProcessMemory` to replace the imm32 with the target level index
3. Sends `WM_COMMAND(0xD2 + chap)` so the game's main thread loads the level itself — no injection, no thread-safety issues
4. Restores the imm32 to its original value after the jump

Key addresses:

| Address | Meaning |
|---|---|
| `[0x415570]` | Actually loaded level (-1 = not loaded) |
| `[0x4199A8]` | Chapter index |
| `[0x4199A4]` | Chapter bitmask (1 / 2 / 4 / 8) |
| `[0x4199C4]` | Unlock flag |

> Note: the level-loading function `0x40A884` clears `[0x4199C4]`, but the chapter-switch
> gate requires it to be 1, so the selector re-writes `C4=1` before every jump.

## Repository Layout

```
├── Rats.exe              # main executable (★trial-limit threshold patched 9→99)
├── 选关器.py             # ★free level selector 1–40 (tkinter GUI)
├── 选关器.bat            # double-click launcher for the selector
├── Level.lvl             # level data
├── rats.dat              # configuration data
├── RATS.HLP              # help file
└── *.WAV                 # all sound effects (BIRTH / BOMB / CHOKE / VICTORY / WEWIN ...)
```

## Disclaimer

This project is for **technical research and digital preservation**. Rats! and all of
its assets are the property of the original author **Sean O'Connor**. This repository
archives legacy assets that are lost or hard to obtain, and documents the complete
reverse-engineering findings.