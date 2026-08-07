# tidal_favorites_to_playlist

Copies **all of your Tidal favorited tracks** into a Tidal playlist.

---

## Requirements

```bash
pip install tidalapi pyyaml tqdm
```

---

## Usage

```bash
# Basic - creates / updates a playlist called "My Tidal Favorites"
python tidal_favorites_to_playlist.py

# Custom playlist name
python tidal_favorites_to_playlist.py --playlist-name "All My Likes"

# Full rebuild - clear the playlist first, then add everything fresh
python tidal_favorites_to_playlist.py --clear
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--playlist-name` | `My Tidal Favorites` | Name of the target playlist |
| `--clear` | off | Wipe the playlist before adding, instead of incremental sync |

---

## How it works

1. **Auth** - Opens a browser for Tidal OAuth on first run. Saves session to `.session.yml` so subsequent runs skip the login step.
2. **Fetch favorites** - Loads all tracks from your Tidal favorites library (paginated, any size).
3. **Create/find playlist** - Looks for an existing playlist by name; creates one if not found.
4. **Sync** - Adds any favorites not already in the playlist (incremental by default, or full rebuild with `--clear`).

---

## Notes

- Uses the same `tidalapi` unofficial library as `spotify_to_tidal` in this repo.
- The `.session.yml` file stores your Tidal tokens - keep it out of version control (already in `.gitignore`).
