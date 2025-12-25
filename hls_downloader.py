import argparse
import logging
import os
import re
import subprocess
import base64
from dataclasses import dataclass
from functools import lru_cache
from time import sleep
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ================= CONFIG =================

_ENCODED_BASE_URL = "aHR0cHM6Ly92aXhzcmMudG8="  # base64("https://iykyn.something")

def _decode_url(value: str) -> str:
    return base64.b64decode(value).decode("utf-8")


@dataclass
class Config:
    base_url: str = _decode_url(_ENCODED_BASE_URL)
    api_base: str = _decode_url(_ENCODED_BASE_URL) + "/api/list"
    ffmpeg_path: str = "ffmpeg"
    output_base: str = "."
    debug: bool = False
    resume: bool = False


HEADERS = {"User-Agent": "Mozilla/5.0"}


# ================= LOGGING =================

def setup_logger(debug: bool):
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S"
    )


# ================= HTTP =================

def make_session():
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504]
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    session.headers.update(HEADERS)
    return session


# ================= UTIL =================

def sanitize_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\n]', "_", name.strip())


def vix_url(config: Config, *parts) -> str:
    return "/".join([config.base_url.rstrip("/"), *map(str, parts)])


# ================= FFMPEG =================

def run_ffmpeg(stream_url: str, output_file: str, config: Config):
    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    temp_file = output_file + ".part"

    cmd = [
        config.ffmpeg_path,
        "-hide_banner",
        "-loglevel", "error" if not config.debug else "info",

        # HLS reliability
        "-user_agent", HEADERS["User-Agent"],
        "-protocol_whitelist", "file,http,https,tcp,tls,crypto",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",

        "-y",
        "-i", stream_url,

        # streams
        "-map", "0:v:0",
        "-map", "0:a?",
        "-map", "0:s?",
        "-c", "copy",

        "-f", "matroska",

        "-movflags", "+faststart",
        "-progress", "pipe:1",
        "-nostats",

        temp_file
    ]

    logging.debug("FFmpeg command: %s", " ".join(cmd))

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )

        last_seconds = -1

        for line in process.stdout:
            if line.startswith("out_time_ms"):
                seconds = int(line.split("=")[1]) // 1_000_000
                if seconds != last_seconds:
                    last_seconds = seconds
                    print(f"\r⏳ Downloaded: {seconds:>6}s", end="", flush=True)

        process.wait()
        print()

    except KeyboardInterrupt:
        logging.warning("Download interrupted by user")
        process.kill()
        if os.path.exists(temp_file):
            os.remove(temp_file)
        raise

    if process.returncode != 0:
        stderr = process.stderr.read().strip()
        if os.path.exists(temp_file):
            os.remove(temp_file)

        logging.error("FFmpeg failed")
        if stderr:
            logging.error("FFmpeg error output:\n%s", stderr)

        raise subprocess.CalledProcessError(process.returncode, cmd)

    # success
    os.replace(temp_file, output_file)

# ================= SCRAPING =================

def extract_playlist_data(html: str):
    url_match = re.search(r"url:\s*'([^']+/playlist/\d+[^']*)'", html)
    token_match = re.search(r"'token'\s*:\s*'([^']+)'", html)
    expires_match = re.search(r"'expires'\s*:\s*'([^']+)'", html)

    if not (url_match and token_match and expires_match):
        return None

    return {
        "playlist_url": url_match.group(1),
        "token": token_match.group(1),
        "expires": expires_match.group(1)
    }


def build_playlist_url(data: dict) -> str:
    parts = list(urlparse(data["playlist_url"]))
    query = parse_qs(parts[4])
    query.update({
        "token": data["token"],
        "expires": data["expires"],
        "h": "1",
        "lang": "en"
    })
    parts[4] = urlencode(query, doseq=True)
    return urlunparse(parts)


# ================= API =================

def check_tmdb_exists(session, config: Config, media_type: str, tmdb_id: int) -> bool:
    r = session.get(f"{config.api_base}/{media_type}?lang=it", timeout=15)
    r.raise_for_status()
    return any(item.get("tmdb_id") == tmdb_id for item in r.json())


def get_available_episodes(session, config: Config, tmdb_id: int):
    r = session.get(f"{config.api_base}/episode?lang=it", timeout=15)
    r.raise_for_status()

    episodes = {}
    for item in r.json():
        if item.get("tmdb_id") == tmdb_id:
            episodes.setdefault(item["s"], []).append(item["e"])

    return episodes


# ================= TMDB =================

@lru_cache
def fetch_metadata_from_tmdb(tmdb_id: int, media_type: str):
    url = f"https://www.themoviedb.org/{media_type}/{tmdb_id}?language=en-US"
    r = requests.get(url, headers=HEADERS, timeout=10)

    if r.status_code != 200:
        return f"{media_type}_{tmdb_id}", "Unknown"

    soup = BeautifulSoup(r.text, "html.parser")
    h2 = soup.select_one(".header_poster_wrapper h2")

    if not h2:
        return f"{media_type}_{tmdb_id}", "Unknown"

    title = h2.find("a").get_text(strip=True)
    year = h2.find("span", class_="release_date")
    year = year.get_text(strip=True).strip("()") if year else "Unknown"

    return title, year


# ================= HANDLERS =================

def handle_movie(session, config: Config, tmdb_id: int):
    r = session.get(vix_url(config, "movie", tmdb_id), timeout=15)
    if r.status_code != 200:
        logging.error("Movie not found")
        return

    data = extract_playlist_data(r.text)
    if not data:
        logging.error("Playlist not found")
        return

    playlist = build_playlist_url(data)
    title, year = fetch_metadata_from_tmdb(tmdb_id, "movie")

    folder = os.path.join(config.output_base, sanitize_filename(f"{title} ({year})"))
    file = os.path.join(folder, f"{sanitize_filename(title)} ({year}).mkv")

    if config.resume and os.path.exists(file) and os.path.getsize(file) > 0:
        logging.info("Skipping existing movie")
        return

    logging.info("Downloading movie: %s (%s)", title, year)
    run_ffmpeg(playlist, file, config)
    logging.info("Saved: %s", file)


def handle_tv(session, config: Config, tmdb_id: int, season_range, episode_range):
    episodes = get_available_episodes(session, config, tmdb_id)
    if not episodes:
        logging.error("No episodes found")
        return

    show, year = fetch_metadata_from_tmdb(tmdb_id, "tv")
    base = os.path.join(config.output_base, sanitize_filename(f"{show} ({year})"))

    total = sum(len(v) for v in episodes.values())
    done = 0

    for season in sorted(episodes):
        if season_range and not season_range[0] <= season <= season_range[1]:
            continue

        season_dir = os.path.join(base, f"Season {season:02d}")
        os.makedirs(season_dir, exist_ok=True)

        for episode in sorted(set(episodes[season])):
            if episode_range and not episode_range[0] <= episode <= episode_range[1]:
                continue

            done += 1
            filename = os.path.join(
                season_dir,
                f"{sanitize_filename(show)} ({year}) - S{season:02d}E{episode:02d}.mkv"
            )

            logging.info("Episode %d/%d — S%02dE%02d", done, total, season, episode)

            if config.resume and os.path.exists(filename) and os.path.getsize(filename) > 0:
                logging.info("Skipping existing file")
                continue

            r = session.get(vix_url(config, "tv", tmdb_id, f"{season:02d}", f"{episode:02d}"), timeout=15)
            if r.status_code != 200:
                logging.error("Episode not found")
                continue

            data = extract_playlist_data(r.text)
            if not data:
                logging.error("Playlist missing")
                continue

            try:
                run_ffmpeg(build_playlist_url(data), filename, config)
            except subprocess.CalledProcessError:
                logging.error("FFmpeg failed")
                continue

            sleep(0.4)


# ================= MAIN =================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", choices=["movie", "tv"])
    parser.add_argument("--tmdb-id", type=int)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--ffmpeg-path")
    parser.add_argument("--output")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--season-from", type=int)
    parser.add_argument("--season-to", type=int)
    parser.add_argument("--episode-from", type=int)
    parser.add_argument("--episode-to", type=int)

    args = parser.parse_args()

    config = Config(
        debug=args.debug,
        ffmpeg_path=args.ffmpeg_path or "ffmpeg",
        output_base=args.output or ".",
        resume=args.resume
    )

    setup_logger(config.debug)
    session = make_session()

    media_type = args.type or input("Type (movie/tv): ").strip()
    tmdb_id = args.tmdb_id or int(input("TMDB ID: "))

    if not check_tmdb_exists(session, config, media_type, tmdb_id):
        logging.error("TMDB ID not available")
        return

    if media_type == "movie":
        handle_movie(session, config, tmdb_id)
    else:
        season_range = (args.season_from, args.season_to) if args.season_from and args.season_to else None
        episode_range = (args.episode_from, args.episode_to) if args.episode_from and args.episode_to else None
        handle_tv(session, config, tmdb_id, season_range, episode_range)


if __name__ == "__main__":
    main()
