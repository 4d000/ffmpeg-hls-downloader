# HLS Downloader

Python tool to download HLS streams with TMDB metadata support and FFmpeg.  
Supports both movies and TV shows with optional season/episode selection, progress display, and resumable downloads.

> ⚠️ The tool is provider-specific.

---

## Features

- Download movies or TV shows
- TMDB metadata scraping for titles and release years
- FFmpeg-based HLS stream recording
- Progress display in console
- Resume partially downloaded files
- Season and episode selection for TV shows
- Robust HTTP session with retries

---

## Installation

1. Clone the repository:

`
git clone https://github.com/4d000/ffmpeg-hls-downloader.git
cd ffmpeg-hls-downloader
`

2. Install dependencies:

`
pip install -r requirements.txt
`

3. Ensure FFmpeg is installed and available in PATH or provide a custom path with `--ffmpeg-path`.

---

## Usage

`
python hls_downloader.py --type TYPE --tmdb-id TMDB_ID [OPTIONS]
`

### Required

- `--type` : `movie` or `tv`
- `--tmdb-id` : TMDB ID of the media

### Optional

| Option | Description |
|--------|-------------|
| `--debug` | Enable debug logging |
| `--ffmpeg-path PATH` | Custom FFmpeg executable path |
| `--output PATH` | Base output folder (default: `.`) |
| `--resume` | Skip already downloaded files |
| `--season-from N` | Start season (TV shows) |
| `--season-to N` | End season (TV shows) |
| `--episode-from N` | Start episode (TV shows) |
| `--episode-to N` | End episode (TV shows) |

---

## Examples

Download a movie:

`
python hls_downloader.py --type movie --tmdb-id 12345
`

Download a TV show, seasons 1–2, all episodes:

`
python hls_downloader.py --type tv --tmdb-id 67890 --season-from 1 --season-to 2
`

Download a TV show, specific episodes:

`
python hls_downloader.py --type tv --tmdb-id 67890 --season-from 1 --season-to 1 --episode-from 3 --episode-to 5
`

---

## Notes

- The default provider URL is obfuscated in the code using Base64.
- Works only with providers that follow the same HLS playlist structure.
- FFmpeg must support HLS (`.m3u8`) playback.

---

## License

MIT License
