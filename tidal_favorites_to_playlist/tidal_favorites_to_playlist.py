import argparse
import asyncio
import math
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import List

import tidalapi
import yaml
from tqdm import tqdm
from tqdm.asyncio import tqdm as atqdm

SESSION_FILE = Path(__file__).parent / ".session.yml"

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
    print(f"\nOpen this URL in your browser to log in:\n  {url}\n")
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
    print("Logged in and session saved.")
    return session

async def _get_all_chunks(url: str, session: tidalapi.Session, parser, params: dict = {}) -> list:
    def _make_request(offset: int = 0):
        p = dict(params)
        p["offset"] = offset
        return session.request.map_request(url, params=p)
    first_raw = _make_request()
    limit = first_raw["limit"]
    total = first_raw["totalNumberOfItems"]
    items = session.request.map_json(first_raw, parse=parser)
    if len(items) < total:
        offsets = [limit * n for n in range(1, math.ceil(total / limit))]
        extra_results = await atqdm.gather(
            *[
                asyncio.to_thread(
                    lambda offset=offset: session.request.map_json(
                        _make_request(offset), parse=parser
                    )
                )
                for offset in offsets
            ],
            desc="  Fetching page",
        )
        for extra in extra_results:
            items.extend(extra)
    return items

async def get_all_favorite_tracks(session: tidalapi.Session) -> List[tidalapi.Track]:
    favorites = session.user.favorites
    params = {"limit": 100, "order": "DATE", "orderDirection": "ASC"}
    url = f"{favorites.base_url}/tracks"
    tracks = await _get_all_chunks(url, session, session.parse_track, params=params)
    return tracks

def add_tracks_to_playlist(playlist: tidalapi.UserPlaylist, track_ids: List[int], chunk_size: int = 20):
    with tqdm(total=len(track_ids), desc="  Adding tracks to playlist") as progress:
        offset = 0
        while offset < len(track_ids):
            chunk = track_ids[offset : offset + chunk_size]
            playlist.add(chunk)
            progress.update(len(chunk))
            offset += len(chunk)

def get_or_create_playlist(session: tidalapi.Session, playlist_name: str, description: str) -> tidalapi.UserPlaylist:
    existing = session.user.playlists()
    for pl in existing:
        if pl.name == playlist_name:
            print(f'Found existing playlist "{playlist_name}" ({pl.num_tracks} tracks).')
            return pl
    print(f'Creating new playlist "{playlist_name}"…')
    return session.user.create_playlist(playlist_name, description)

async def run(playlist_name: str, clear_existing: bool):
    session = open_tidal_session()
    print("\nLoading your Tidal favorite tracks…")
    fav_tracks = await get_all_favorite_tracks(session)
    print(f"Found {len(fav_tracks)} favorite track(s).")
    if not fav_tracks:
        print("Nothing to do - your favorites library is empty.")
        return
    print(f'\nPreparing playlist "{playlist_name}"…')
    today = datetime.today().strftime("%Y-%m-%d")
    description = f"All favorited tracks - synced on {today}"
    playlist = get_or_create_playlist(session, playlist_name, description)
    existing_ids: set[int] = set()
    if playlist.num_tracks > 0:
        if clear_existing:
            print(f"Clearing {playlist.num_tracks} existing track(s) (--clear flag set)…")
            from tqdm import tqdm as _tqdm
            with _tqdm(total=playlist.num_tracks, desc="Clearing playlist") as progress:
                while playlist.num_tracks:
                    chunk_size = min(20, playlist.num_tracks)
                    indices = list(range(chunk_size))
                    index_string = ",".join(map(str, indices))
                    headers = {"If-None-Match": playlist._etag}
                    playlist.request.request("DELETE", (playlist._base_url + "/items/%s") % (playlist.id, index_string), headers=headers)
                    playlist._reparse()
                    progress.update(chunk_size)
        else:
            print(f"Playlist already has {playlist.num_tracks} track(s); fetching to skip duplicates…")
            existing_raw = playlist.tracks(limit=9999)
            existing_ids = {t.id for t in existing_raw}
            print(f"  {len(existing_ids)} track(s) already present - they will be skipped.")
    print("\nAdding favorite tracks to playlist…")
    new_ids = [t.id for t in fav_tracks if t.id not in existing_ids]
    if not new_ids:
        print("No new tracks to add - playlist is already up to date.")
    else:
        print(f"Adding {len(new_ids)} track(s)…")
        add_tracks_to_playlist(playlist, new_ids)
    print(f'\nPlaylist "{playlist_name}" now contains {playlist.num_tracks} track(s).')

def main():
    parser = argparse.ArgumentParser(description="Copy all Tidal favorite tracks into a Tidal playlist.")
    parser.add_argument("--playlist-name", default="My Tidal Favorites", help='Name of the target playlist (default: "My Tidal Favorites")')
    parser.add_argument("--clear", action="store_true", help="Clear the playlist before adding tracks (full rebuild instead of incremental sync)")
    args = parser.parse_args()
    asyncio.run(run(playlist_name=args.playlist_name, clear_existing=args.clear))

if __name__ == "__main__":
    main()
