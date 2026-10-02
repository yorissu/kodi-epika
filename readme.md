
# LRT Epika for Kodi

Unofficial Kodi add-on for [epika.lrt.lt](https://epika.lrt.lt), LRT's free film and series platform. Works on Kodi 20 or newer (LibreELEC on a Raspberry Pi included) and is used with a TV remote.

## Installation

Done once, in Kodi, with the remote. Menu names are from Kodi 21 with the default skin.

1. **Allow outside add-ons:** Settings → System → Add-ons → switch on **Unknown sources** → Yes.
2. **Add the source:** Settings → File manager → **Add source** → `<None>` → type `https://yorissu.github.io/kodi-epika/` → OK → name it `epika` → OK.
   (Tip: the Kore or Yatse phone apps let you type this on your phone.)
3. **Install the repository:** Add-ons → add-on browser (box icon, top left) → **Install from zip file** → `epika` → `repository.lrt.epika-….zip`.
4. **Install the add-on:** add-on browser → **Install from repository** → LRT Epika repository → Video add-ons → **LRT Epika** → Install → OK.

If you installed an older LRT Epika zip by hand (add-on id `plugin.video.epika`), uninstall it first.

## Usage

- **Log in:** open LRT Epika → **Log in**. Kodi shows a 6-digit code. On a phone or PC, open epika.lrt.lt/ziureti-tv, log in to your (free) Epika account and enter the code. No password is stored in Kodi.
- **First playback:** Kodi asks to install Widevine (needed for Epika's video protection). Answer Yes; on a Pi it takes a few minutes and about 2 GB of free space, once.
- **Browse:** the menu is the website's (Filmai, Serialai, Vaikams, Kategorijos...), plus Genres, My List, Continue watching and Search. Every title shows its plot, cast, year, rating and artwork.
- **Search:** Search → New search. Recent searches are kept, so you rarely have to type.
- **Context menu** (the remote's menu button on a title): Watch trailer, Add to / Remove from My List.
- **Settings:** catalogue order and page size, subtitles, Widevine install, cache, debug logging.
- **Log out:** the last entry in the LRT Epika menu, or remove the device on the website.
- **Updates:** Kodi installs new releases automatically. To go back to an older version: Add-ons → My add-ons → Video add-ons → LRT Epika → **Update** (or **Versions**), and switch off **Auto-update** there.
- **Something fails:** turn on Settings → Advanced → **Debug logging** in LRT Epika, try again and check the Kodi log.

## Building

Needs only Python 3. In the project folder:

```bash
python build_zip.py
```

This writes `dist/` with two zips and `SHA256SUMS`:

- `plugin.video.lrt.epika-<version>.zip`, the add-on. Install it on a Kodi with **Install from zip file** to try it.
- `repository.lrt.epika-<version>.zip`, the repository add-on that points Kodi at the update site.

**Versions.** The add-on's version comes from the git tag; `addon.xml` only holds a placeholder.

- `python build_zip.py --tag v0.3.0` builds version `0.3.0`, exactly what the release will contain.
- `python build_zip.py --tag v0.3.0-rc.2` builds the pre-release `0.3.0~rc.2`. Kodi needs the `~` to sort it below `0.3.0`; the tag and the zip's file name (`…-0.3.0-rc.2.zip`) never have it.
- Without `--tag` the version comes from git (`0.3.1~dev2` = 2 commits after v0.3.0), or is `0.0.0~dev` without git.

**The two zips have different versions on purpose.** They are separate add-ons, and Kodi updates each by its own version. The repository add-on only points to the update site, so its version (in `repository.lrt.epika/addon.xml`) stays at `1.0.0` and is only raised when that address changes. If it followed the tag, every release would also push a pointless update of the repository add-on to every Kodi.

`icon.png` and `license.txt` exist once, next to this readme; the build copies them into both zips.

## Testing

```bash
python -m unittest discover -s tests
```

The tests run without Kodi and without network: small stand-ins for Kodi's modules (`tests/kodi_stubs/`) and a fake Epika server with made-up data (`tests/helpers.py`).

## CI and releases

GitHub Actions does the rest:

- **CI** (`.github/workflows/ci.yml`), on every push to main and every pull request: runs the tests, builds the zips and runs Kodi's official add-on checker on the built add-on.
- **Release** (`.github/workflows/release.yml`), on pushing a tag:

  ```bash
  git tag v0.3.0
  git push origin v0.3.0
  ```

  It runs the tests, builds the zips with the tag's version and creates the GitHub release with the zips and `SHA256SUMS`. For a normal release (`vX.Y.Z`) it also rebuilds the Kodi repository site on GitHub Pages (`build_repo.py`), so every Kodi gets the update. A pre-release (`vX.Y.Z-<suffix>`) only becomes a GitHub pre-release: install its zip by hand to test it.
- **Pulling a broken release:** delete it on GitHub, then Actions → Release → **Run workflow** to rebuild the site without it.
- **One-time setup** of the GitHub repo: Settings → Pages → Source: **GitHub Actions**.

## License

MIT. Not affiliated with LRT. Needs a free LRT Epika account; some content is only available in Lithuania.
