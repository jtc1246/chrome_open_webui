[简体中文](README.md)

# Open WebUI-Optimized Chrome 

A customized Android browser prepared for Open WebUI: it turns the official Chromium build `ChromePublic.apk` into a Chromium dedicated to Open WebUI, fixing several stutter problems Open WebUI has in mobile Chrome. The output is an ordinary apk that installs directly on any Android device (no root, adb or other tools needed) and coexists with the Chrome already on the phone.

## Background and Motivation

To keep scrolling and jumping accurate, our Open WebUI frontend renders every message of a chat in full, so a long chat has tens of thousands of page elements. Under that condition, two kinds of Chrome behavior cause visible stutter, and neither can be reliably turned off from the page or the browser settings.

**1. Accessibility gets enabled automatically, opening/closing popups stalls for 200-300 ms**

Chrome keeps accessibility off by default, but some programs on the system (automation scripts, input methods, autofill/password-manager services, some security or remote-control software, etc., even when they are not being used on Chrome) cause it to be turned on automatically. Once on, every page change has to update an accessibility tree as large as the page itself, so in a long chat every popup open/close (model selector, draft board, ...) stalls for 200-300 ms. Many phones run such services by default without the user noticing. It can be turned off temporarily in `chrome://accessibility`, but that stops working after the browser restarts and accessibility gets enabled again.

**2. Scroll jank depends on the experiment group Google assigns to the device**

In long chats the page scrolls on the main thread. Chrome has several touch-scrolling optimizations (unthrottled touchmove, scroll event resampling, scroll prediction), but whether they are on is decided by experiment configs that Google's servers hand out per device at random: with the same Chrome version, some phones scroll smoothly and others drop frames, and the user cannot choose.

**3. Adding startup switches to release Chrome is impractical**

Both problems above could be solved with startup switches, but on Android, release Chrome only accepts switches through a command-line file written to the device via adb, so every device has to be connected to a computer, which is too cumbersome; and once switches are added, the browser shows an "unsupported command-line flag" warning every time it opens.

**4. The experiment config built into Chromium development builds**

Using the official Chromium build as is does not work either: it turns on the whole set of testing experiments shipped in the source tree (e.g. experimental UIs such as the bottom toolbar), so its UI and behavior differ a lot from release Chrome.

## Changes

The following switches are hard-coded into Chromium's startup, with no adb needed and no warning shown:

- **Experiment config pinned to one that scrolls smoothly**: the experiment config that Chrome 155 actually received on a phone that scrolls smoothly (Xiaomi 18) is used as is. It contains the scrolling optimizations above, and also keeps other features as close to release Chrome as possible.
- **Chromium's built-in testing experiment config is disabled** (`--disable-field-trial-config`), avoiding the experimental UIs of development builds.
- **Web content accessibility is disabled** (`--disable-renderer-accessibility`).

In addition, the accessibility entry point is cut off directly: the method through which the system (and the browser itself when an autofill service is on) asks a web page for its accessibility interface now always returns null, so pages never build or maintain an accessibility tree.

Trade-offs:
- No Google account sync, page translation, or other features that depend on Google services;
- The version comes from an official Chromium snapshot (currently 156.0.8078.0), not release Chrome.

## Usage

```bash
./build.sh                                   # origin/ChromePublic.apk -> out/ChromePublic_patched.apk
python3 -u build.py <input apk> <output apk> # explicit input and output
```

The input is `ChromePublic.apk` from an official Chromium snapshot (download `chrome-android.zip` from https://commondatastorage.googleapis.com/chromium-browser-snapshots/index.html?prefix=Android_Arm64/ and take `apks/ChromePublic.apk`). Requires root (chroot is used) and Linux x86_64.

## Implementation Overview

- `build.py` unpacks the build environment image (`image/chrome_edit_env.tar.7z`: Rocky 9 + JRE + smali + zipalign/apksigner) to a temporary directory and runs `patch/patch_apk.py` inside a chroot: it decompiles the dex, injects the switches where the command line is initialized, cuts off the accessibility entry point, then repacks and signs. The image contains only the environment; the conversion code and config live outside it. `image/make_image.py` rebuilds the image from official packages with pinned versions and checksums.
- Config lives in `config/`: switches and experiment config (`config.json`, `variations_xiaomi18_chrome155.json`), plus the signing key `keystore.jks`. Future versions must be signed with the same key to install over an existing one.
- The injection points depend on the exact Chromium version; if they no longer match after switching to a new version, the script exits with an error and they need to be located again.
