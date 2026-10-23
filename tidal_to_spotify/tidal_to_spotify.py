import argparse
import json
import re
import sys
import unicodedata
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional

import spotipy
import tidalapi
import yaml
from tidalapi.types import ItemOrder, OrderDirection
from tqdm import tqdm

SCRIPT_DIR = Path(__file__).parent
SESSION_FILE = SCRIPT_DIR / ".session.yml"
SPOTIFY_CACHE_FILE = SCRIPT_DIR / ".cache-tidal_to_spotify"
MATCH_CACHE_FILE = SCRIPT_DIR / ".match_cache.json"
NOT_FOUND_FILE = SCRIPT_DIR / "songs not found.txt"
DEFAULT_CONFIG_FILE = SCRIPT_DIR / "config.yml"

SPOTIFY_SCOPES = "playlist-read-private playlist-modify-private playlist-modify-public user-library-read user-library-modify"
SPOTIFY_SEARCH_LIMIT = 10  # max allowed since the Feb 2026 Web API changes
DURATION_TOLERANCE = 3  # seconds

def open_tidal_session() -> tidalapi.Session:
    session = tidalapi.Session()
    if SESSION_FILE.exists():
        try:
            with open(SESSION_FILE, "r") as f:
                prev = yaml.safe_load(f)
            if session.load_oauth_session(token_type=prev["token_type"], access_token=prev["access_token"], refresh_token=prev["refresh_token"]):
                print("Loaded existing Tidal session.")
                return session
        except Exception as e:
            print(f"Could not restore previous session ({e}), re-authenticating…")
    login, future = session.login_oauth()
    url = login.verification_uri_complete
    if not url.startswith("https://"):
        url = "https://" + url
    print(f"\nOpen this URL in your browser to log in to Tidal:\n  {url}\n")
    webbrowser.open(url)
    future.result()
    with open(SESSION_FILE, "w") as f:
        yaml.dump(
            {
                "token_type": session.token_type,
                "access_token": session.access_token,
                "refresh_token": session.refresh_token,
            },
            f,
        )
    print("Logged in to Tidal and session saved.")
    return session

def open_spotify_session(config_path: Path) -> spotipy.Spotify:
    config = {}
    if config_path.exists():
        with open(config_path, "r") as f:
            config = (yaml.safe_load(f) or {}).get("spotify", {})
    # Anything missing from config.yml falls back to SPOTIPY_CLIENT_ID / SPOTIPY_CLIENT_SECRET / SPOTIPY_REDIRECT_URI
    auth_manager = spotipy.SpotifyOAuth(
        client_id=config.get("client_id"),
        client_secret=config.get("client_secret"),
        redirect_uri=config.get("redirect_uri", "http://127.0.0.1:8888/callback"),
        scope=SPOTIFY_SCOPES,
        cache_path=str(SPOTIFY_CACHE_FILE),
        open_browser=config.get("open_browser", True),
    )
    try:
        auth_manager.get_access_token(as_dict=False)
    except spotipy.SpotifyOauthError as e:
        sys.exit(f"Could not log in to Spotify: {e}\nCheck the client_id / client_secret / redirect_uri in {config_path}.")
    print("Logged in to Spotify.")
    return spotipy.Spotify(auth_manager=auth_manager, requests_timeout=15, retries=10, status_retries=10, backoff_factor=1)

def get_all_tidal_playlists(session: tidalapi.Session) -> List[tidalapi.Playlist]:
    playlists, seen, offset = [], set(), 0
    while True:
        page = session.user.playlist_and_favorite_playlists(offset=offset, limit=50)
        for pl in page:
            if pl.id not in seen:
                seen.add(pl.id)
                playlists.append(pl)
        if len(page) < 50:
            return playlists
        offset += 50

def find_tidal_playlist(session: tidalapi.Session, name_or_id: str, all_playlists: List[tidalapi.Playlist]) -> Optional[tidalapi.Playlist]:
    for pl in all_playlists:
        if pl.id == name_or_id or pl.name == name_or_id:
            return pl
    try:
        return session.playlist(name_or_id)
    except Exception:
        return None

def get_tidal_playlist_tracks(playlist: tidalapi.Playlist) -> List[tidalapi.Track]:
    tracks, offset = [], 0
    while offset < playlist.num_tracks:
        page = playlist.tracks(limit=100, offset=offset)
        if not page:
            break
        tracks.extend(page)
        offset += 100
    return tracks

def get_tidal_favorite_tracks(session: tidalapi.Session) -> List[tidalapi.Track]:
    favorites = session.user.favorites
    tracks, offset = [], 0
    while True:
        page = favorites.tracks(limit=100, offset=offset, order=ItemOrder.Date, order_direction=OrderDirection.Ascending)
        tracks.extend(page)
        if len(page) < 100:
            return tracks
        offset += 100

def describe(track: tidalapi.Track) -> str:
    artists = ", ".join(a.name for a in track.artists or [])
    return f"{artists} - {track_title(track)}"

def track_title(track: tidalapi.Track) -> str:
    if track.version and track.version.lower() not in track.name.lower():
        return f"{track.name} ({track.version})"
    return track.name

def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()

def simplify(title: str) -> str:
    # Drop "(feat. X)", "[Remastered 2011]", "- Radio Edit" etc. for loose comparison
    title = re.split(r" - |\(|\[", title)[0]
    return normalize(title)

def artist_overlap(track: tidalapi.Track, candidate: dict) -> bool:
    tidal_artists = {normalize(a.name) for a in track.artists or []}
    spotify_artists = {normalize(a["name"]) for a in candidate.get("artists", [])}
    if tidal_artists & spotify_artists:
        return True
    # Handle "Artist A & Artist B" credited as a single artist on one side
    tidal_joined = " ".join(tidal_artists)
    return any(a and a in tidal_joined for a in spotify_artists)

def duration_close(track: tidalapi.Track, candidate: dict) -> bool:
    return abs(track.duration - candidate["duration_ms"] / 1000) <= DURATION_TOLERANCE

def score(track: tidalapi.Track, candidate: dict) -> float:
    if not artist_overlap(track, candidate) or not duration_close(track, candidate):
        return 0.0
    full = SequenceMatcher(None, normalize(track_title(track)), normalize(candidate["name"])).ratio()
    loose = SequenceMatcher(None, simplify(track.name), simplify(candidate["name"])).ratio()
    return max(full, loose * 0.95)

def spotify_search(sp: spotipy.Spotify, query: str) -> List[dict]:
    result = sp.search(q=query, type="track", limit=SPOTIFY_SEARCH_LIMIT)
    return [t for t in result["tracks"]["items"] if t]

def find_on_spotify(sp: spotipy.Spotify, track: tidalapi.Track) -> Optional[str]:
    if track.isrc:
        candidates = spotify_search(sp, f"isrc:{track.isrc}")
        exact = [c for c in candidates if c.get("external_ids", {}).get("isrc", "").upper() == track.isrc.upper()]
        if exact:
            return max(exact, key=lambda c: (artist_overlap(track, c), -abs(track.duration - c["duration_ms"] / 1000)))["uri"]
    artist = track.artists[0].name if track.artists else ""
    queries = [
        f'track:"{track.name}" artist:"{artist}"',
        f"{simplify(track.name)} {artist}",
    ]
    for query in queries:
        candidates = spotify_search(sp, query)
        if not candidates:
            continue
        best = max(candidates, key=lambda c: score(track, c))
        if score(track, best) >= 0.6:
            return best["uri"]
    return None

def load_match_cache() -> Dict[str, str]:
    if MATCH_CACHE_FILE.exists():
        try:
            return json.loads(MATCH_CACHE_FILE.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {}

def save_match_cache(cache: Dict[str, str]):
    MATCH_CACHE_FILE.write_text(json.dumps(cache, indent=1), encoding="utf-8")

def match_tracks(sp: spotipy.Spotify, tracks: List[tidalapi.Track], cache: Dict[str, str], workers: int, label: str) -> List[str]:
    """Returns Spotify URIs in the same order as `tracks`, skipping tracks that couldn't be found."""
    todo = list({str(t.id): t for t in tracks if str(t.id) not in cache}.values())
    if todo:
        print(f"  {len(tracks) - len(todo)} track(s) cached, searching Spotify for {len(todo)}…")
        not_found = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(tqdm(pool.map(lambda t: find_on_spotify(sp, t), todo), total=len(todo), desc="  Searching Spotify"))
        for track, uri in zip(todo, results):
            if uri:
                cache[str(track.id)] = uri
            else:
                not_found.append(track)
        save_match_cache(cache)
        if not_found:
            print(f"  Could not find {len(not_found)} track(s) on Spotify (logged to {NOT_FOUND_FILE.name}).")
            with open(NOT_FOUND_FILE, "a", encoding="utf-8") as f:
                f.write(f"\n== {label} ({datetime.today():%Y-%m-%d %H:%M}) ==\n")
                for t in not_found:
                    f.write(f"{t.id}: {describe(t)} [{t.album.name if t.album else ''}]\n")
    uris, seen = [], set()
    for t in tracks:
        uri = cache.get(str(t.id))
        if uri and uri not in seen:
            seen.add(uri)
            uris.append(uri)
    return uris

def get_all_pages(sp: spotipy.Spotify, first_page: dict) -> List[dict]:
    items, page = list(first_page["items"]), first_page
    while page.get("next"):
        page = sp._get(page["next"])
        items.extend(page["items"])
    return items

def get_or_create_spotify_playlist(sp: spotipy.Spotify, my_id: str, name: str, description: str) -> dict:
    playlists = get_all_pages(sp, sp._get("me/playlists", limit=50))
    for pl in playlists:
        if pl and pl["name"] == name and pl["owner"]["id"] == my_id:
            return pl
    print(f'  Creating Spotify playlist "{name}"…')
    return sp._post("me/playlists", payload={"name": name, "public": False, "description": description})

def get_spotify_playlist_uris(sp: spotipy.Spotify, playlist_id: str) -> List[str]:
    items = get_all_pages(sp, sp._get(f"playlists/{playlist_id}/items", limit=50))
    uris = []
    for it in items:
        entry = it.get("item") or it.get("track")
        if entry and entry.get("uri"):
            uris.append(entry["uri"])
    return uris

def add_to_spotify_playlist(sp: spotipy.Spotify, playlist_id: str, uris: List[str]):
    for i in tqdm(range(0, len(uris), 100), desc="  Adding to playlist"):
        sp._post(f"playlists/{playlist_id}/items", payload={"uris": uris[i : i + 100]})

def sync_playlist(sp: spotipy.Spotify, my_id: str, tidal_playlist: tidalapi.Playlist, cache: Dict[str, str], workers: int, clear: bool):
    print(f'\nPlaylist "{tidal_playlist.name}" ({tidal_playlist.num_tracks} track(s) on Tidal)')
    tracks = get_tidal_playlist_tracks(tidal_playlist)
    uris = match_tracks(sp, tracks, cache, workers, tidal_playlist.name)
    description = f"Synced from Tidal on {datetime.today():%Y-%m-%d}"
    spotify_playlist = get_or_create_spotify_playlist(sp, my_id, tidal_playlist.name, description)
    playlist_id = spotify_playlist["id"]
    if clear:
        print("  Clearing existing Spotify playlist (--clear flag set)…")
        sp._put(f"playlists/{playlist_id}/items", payload={"uris": []})
        existing = set()
    else:
        existing = set(get_spotify_playlist_uris(sp, playlist_id))
    new_uris = [u for u in uris if u not in existing]
    if not new_uris:
        print("  Already up to date.")
        return
    print(f"  Adding {len(new_uris)} track(s)…")
    add_to_spotify_playlist(sp, playlist_id, new_uris)

def sync_favorites(sp: spotipy.Spotify, tidal_session: tidalapi.Session, cache: Dict[str, str], workers: int):
    print("\nTidal favorites -> Spotify Liked Songs")
    tracks = get_tidal_favorite_tracks(tidal_session)
    print(f"  Found {len(tracks)} favorite track(s) on Tidal.")
    if not tracks:
        return
    uris = match_tracks(sp, tracks, cache, workers, "Favorites")
    print("  Loading Spotify Liked Songs…")
    liked = {(it.get("item") or it.get("track"))["uri"] for it in get_all_pages(sp, sp._get("me/tracks", limit=50)) if it.get("item") or it.get("track")}
    new_uris = [u for u in uris if u not in liked]
    if not new_uris:
        print("  Already up to date.")
        return
    # Oldest Tidal favorite is saved first so it ends up lowest in Liked Songs
    print(f"  Saving {len(new_uris)} track(s) to Liked Songs…")
    for i in tqdm(range(0, len(new_uris), 40), desc="  Saving"):
        sp._put("me/library", payload={"uris": new_uris[i : i + 40]})

def main():
    parser = argparse.ArgumentParser(description="Copy Tidal playlists and favorites to Spotify.")
    parser.add_argument("--playlist", action="append", metavar="NAME_OR_ID", help="Tidal playlist name or UUID to sync (repeatable)")
    parser.add_argument("--all-playlists", action="store_true", help="Sync every Tidal playlist you own or have favorited")
    parser.add_argument("--favorites", action="store_true", help="Sync Tidal favorite tracks to Spotify Liked Songs")
    parser.add_argument("--list", action="store_true", help="List your Tidal playlists and exit")
    parser.add_argument("--clear", action="store_true", help="Clear each Spotify playlist before adding tracks (full rebuild instead of incremental sync)")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent Spotify searches (default: 4, lower it if you hit rate limits)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_FILE, help="Path to config.yml with Spotify credentials")
    args = parser.parse_args()

    tidal_session = open_tidal_session()
    tidal_playlists = get_all_tidal_playlists(tidal_session)

    if args.list:
        for pl in tidal_playlists:
            print(f"{pl.id}  {pl.num_tracks:>5}  {pl.name}")
        return

    # No selection given -> sync everything
    if not (args.playlist or args.all_playlists or args.favorites):
        args.all_playlists = args.favorites = True

    if args.all_playlists:
        selected = tidal_playlists
    else:
        selected = []
        for name_or_id in args.playlist or []:
            pl = find_tidal_playlist(tidal_session, name_or_id, tidal_playlists)
            if pl is None:
                sys.exit(f'Tidal playlist "{name_or_id}" not found. Run with --list to see your playlists.')
            selected.append(pl)

    sp = open_spotify_session(args.config)
    my_id = sp._get("me")["id"]
    cache = load_match_cache()

    for pl in selected:
        sync_playlist(sp, my_id, pl, cache, args.workers, args.clear)
    if args.favorites:
        sync_favorites(sp, tidal_session, cache, args.workers)
    print("\nDone.")

if __name__ == "__main__":
    main()
