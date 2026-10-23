# tidal_to_spotify

Copies your **Tidal playlists** and **Tidal favorite tracks** to Spotify.

---

## Requirements

```bash
pip install tidalapi spotipy pyyaml tqdm
```

You also need a Spotify developer app:

1. Go to https://developer.spotify.com/dashboard and create an app (choose **Web API**).
2. Add `http://127.0.0.1:8888/callback` as a Redirect URI.
3. Copy `example_config.yml` to `config.yml` and fill in the app's Client ID and Client Secret.

> Since Feb 2026 Spotify requires the owner of a Development Mode app to have Spotify Premium.

---

## Usage

```bash
# Everything - all your Tidal playlists + favorites -> Liked Songs
python tidal_to_spotify.py

# List your Tidal playlists (name / UUID)
python tidal_to_spotify.py --list

# Specific playlists, by name or UUID
python tidal_to_spotify.py --playlist "Road Trip" --playlist 0a1b2c3d-...

# Only favorites -> Spotify Liked Songs
python tidal_to_spotify.py --favorites

# Full rebuild - clear each Spotify playlist first, then add everything fresh
python tidal_to_spotify.py --all-playlists --clear
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--playlist NAME_OR_ID` | - | Tidal playlist to sync (repeatable) |
| `--all-playlists` | off | Sync every Tidal playlist you own or have favorited |
| `--favorites` | off | Sync Tidal favorite tracks to Spotify Liked Songs |
| `--list` | off | List your Tidal playlists and exit |
| `--clear` | off | Wipe each Spotify playlist before adding, instead of incremental sync |
| `--workers` | `4` | Concurrent Spotify searches (lower it if you hit rate limits) |
| `--config` | `config.yml` | Path to the config file with Spotify credentials |

With no selection flags, it runs as `--all-playlists --favorites`.

---

## How it works

1. **Auth** - Tidal OAuth opens in the browser on first run and is saved to `.session.yml`. Spotify OAuth is saved to `.cache-tidal_to_spotify`.
2. **Fetch from Tidal** - Loads the tracks from the selected playlists and/or your favorites.
3. **Match** - Each track is looked up on Spotify by **ISRC** first. If that fails, it falls back to a title + artist search and accepts a result only when the artist matches, the duration is within 3 seconds and the title is close enough.
4. **Sync** - Creates a private Spotify playlist with the same name (or reuses one you own with that name) and adds the tracks that aren't already in it. Favorites are saved to Liked Songs.

---

## Notes

- Matches are cached in `.match_cache.json`, so later runs only search for new tracks.
- Tracks that couldn't be found are appended to `songs not found.txt`.
- Uses the post-Feb 2026 Spotify Web API endpoints (`/me/playlists`, `/playlists/{id}/items`, `/me/library`).
- `config.yml`, `.session.yml`, the Spotify token cache and the match cache are all in `.gitignore`.
