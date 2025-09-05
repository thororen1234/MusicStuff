import os
import shutil
import argparse
import subprocess
import yt_dlp

pid = os.getpid()
tmp_dir = f"tmp_{pid}"

def run_ffmpeg(input_path, output_path, ffmpeg_filter):
    cmd = [
        "ffmpeg",
        "-i", input_path,
        "-filter:a", ffmpeg_filter,
        "-b:a", "192k",
        output_path,
        "-y"
    ]
    return subprocess.call(cmd)

def youtube_download(terms):
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': tmp_dir + '/yt_dlp'
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([terms])

def main(args):
    if os.path.isdir(tmp_dir):
        shutil.rmtree(tmp_dir)
    os.mkdir(tmp_dir)

    path = ''
    ext = 'mp3'

    if args.ytdl:
        youtube_download(args.ytdl)
        path = tmp_dir + '/yt_dlp.mp3'
    elif args.search:
        youtube_download('ytsearch:' + args.search)
        path = tmp_dir + '/yt_dlp.mp3'
    elif args.file:
        path = args.file
        ext = os.path.splitext(path)[1].lstrip('.') or 'mp3'

    intermediate_path = path

    filters = []

    if args.nightcore:
        filters.append(f"asetrate=44100*{args.speed},aresample=44100")

    if args.eightd:
        filters.append("pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1,apulsator=hz=0.125")

    if args.volume != 1.0:
        filters.append(f"volume={args.volume}")

    if args.bass != 0.0:
        filters.append(f"bass=g={args.bass}")

    ffmpeg_filter = ",".join(filters)

    output = args.output if args.output else f"processed_{pid}.{ext}"
    run_ffmpeg(intermediate_path, output, ffmpeg_filter)

    shutil.rmtree(tmp_dir)
    print(f'File generated: {output}')

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Change song using ffmpeg."
    )
    group_input = parser.add_mutually_exclusive_group(required=True)
    group_input.add_argument("-y", "--ytdl", help="Download a specific video URL using yt-dlp")
    group_input.add_argument("-s", "--search", help="Search for a specific song on YouTube")
    group_input.add_argument("-f", "--file", help="File path to the song to convert")

    parser.add_argument("--eightd", action="store_true", help="Apply 8D effect")
    parser.add_argument("--nightcore", action="store_true", help="Apply Nightcore effect")
    parser.add_argument("--speed", type=float, default=1.25, help="Speed multiplier for Nightcore effect")
    parser.add_argument("--volume", type=float, default=1.0, help="Audio volume multiplier (default=1.0)")
    parser.add_argument("--bass", type=float, default=0.0, help="Bass gain in dB (default=0)")
    parser.add_argument("-o", "--output", help="Name of the output file")

    main(parser.parse_args())
