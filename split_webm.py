import os
import json
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional


def convert_file(
        input_path: str,
        output_path: Optional[str] = None,
        format: str = "wav",
        sample_rate: int = 44100,
        bitrate: Optional[str] = None,
) -> str:
    """
    Конвертирует аудиофайл в нужный формат.

    Параметры:
        input_path   - путь к исходному файлу
        output_path  - путь к выходному файлу (если None, генерируется автоматически)
        format       - целевой формат (wav, mp3, flac и т.п.)
        sample_rate  - частота дискретизации (для PCM/wav)
        bitrate      - битрейт (например, "192k" для MP3). Игнорируется для WAV.

    Возвращает:
        Путь к сконвертированному файлу.
    """
    input_path = Path(input_path).resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    if output_path is None:
        base = input_path.stem
        output_path = input_path.with_suffix(f".{format.lower()}")
    else:
        output_path = Path(output_path).resolve()

    codec_args = []
    if format.lower() == "wav":
        codec_args = ["-c:a", "pcm_s16le", "-ar", str(sample_rate), "-ac", "2"]
    elif format.lower() == "mp3":
        codec_args = ["-c:a", "libmp3lame"]
        if bitrate:
            codec_args.extend(["-b:a", bitrate])
    elif format.lower() == "flac":
        codec_args = ["-c:a", "flac"]
    elif format.lower() == "opus":
        codec_args = ["-c:a", "libopus"]
        if bitrate:
            codec_args.extend(["-b:a", bitrate])
        codec_args.extend(["-ar", "48000"])
    else:
        codec_args = ["-c:a", "aac"]

    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(input_path),
        *codec_args,
        "-vn",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {result.stderr}")

    return str(output_path)


def detect_silence(
        audio_path: str,
        min_silence_duration: float = 1.5,
        noise_threshold: float = -40.0,
) -> List[Dict[str, Any]]:
    """
    Ищет участки тишины в WAV-файле с помощью ffprobe + silencedetect.

    Параметры:
        audio_path          - путь к WAV-файлу
        min_silence_duration- минимальная длительность тишины (d в silencedetect)
        noise_threshold     - порог шума в dB (noise в silencedetect)

    Возвращает:
        Список словарей с данными о найденных паузах (без записи JSON на диск).
    """
    audio_path = Path(audio_path).resolve()
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")
    if audio_path.suffix.lower() != ".wav":
        return {"error": "Input must be a .wav file for silence detection."}

    cmd = [
        "ffprobe",
        "-f", "lavfi",
        "-i", f"amovie={audio_path},silencedetect=noise={noise_threshold}dB:d={min_silence_duration}",
        "-show_frames",
        "-show_entries", "frame=key_frame,pkt_pts_time,tags",
        "-print_format", "json",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe silence detection failed: {result.stderr}")

    data = json.loads(result.stdout)
    frames = data.get("frames", [])

    silences = []
    for f in frames:
        tags = f.get("tags", {})
        if "lavfi.silence_start" in tags:
            silences.append({
                "start": float(tags["lavfi.silence_start"]),
                "duration": float(tags.get("lavfi.silence_duration", 0)),
            })

    return silences


def split_by_silence(
        source_path: str,
        silence_data: List[Dict[str, Any]],
        output_dir: str,
        output_format: str = "wav",
        sample_rate: int = 44100,
        min_segment_duration: float = 10.0,
) -> List[str]:
    """
    Нарезает исходный файл на сегменты по данным о тишине.
    Не делает сложной конвертации: либо копирует поток (если возможно),
    либо приводит к нужному формату (например, WAV для CD).

    Параметры:
        source_path           - путь к оригинальному файлу (WebM, FLAC, MP3 и т.д.)
        silence_data          - результат detect_silence
        output_dir            - папка для треков
        output_format         - формат выхода ('wav', 'flac', 'mp3' и т.п.)
        sample_rate           - частота дискретизации (для WAV/FLAC)
        min_segment_duration  - минимальная длина трека (сек)

    Возвращает:
        Список путей к созданным файлам.
    """
    source_path = Path(source_path).resolve()
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)


    boundaries = [0.0]
    for s in silence_data:
        boundaries.append(s["start"] + s["duration"])

    boundaries = sorted(set(boundaries))

    created_files = []
    fmt = output_format.lower()

    for i in range(len(boundaries) - 1):
        start = boundaries[i]
        end = boundaries[i + 1]
        duration = end - start

        if duration < min_segment_duration:
            continue

        track_num = i + 1
        out_name = f"{track_num:02d}.{fmt}"
        out_path = out_dir / out_name

        cmd = [
            "ffmpeg",
            "-y",
            "-i", str(source_path),
            "-ss", str(start),
            "-t", str(duration),
            "-vn",  # без видео
        ]

        # Если хотим чистый PCM (для CD/дальнейшей работы) — делаем WAV
        if fmt == "wav":
            cmd.extend([
                "-c:a", "pcm_s16le",
                "-ar", str(sample_rate),
                "-ac", "2",
            ])
        elif fmt == "flac":
            cmd.extend(["-c:a", "flac"])
        elif fmt == "mp3":
            # Если всё-таки нужен MP3 прямо здесь — можно оставить,
            # но по твоей идее лучше резать в WAV, а потом отдельно конвертировать.
            cmd.extend(["-c:a", "libmp3lame", "-b:a", "192k"])
        else:
            # fallback
            cmd.extend(["-c:a", "aac"])

        cmd.append(str(out_path))

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            created_files.append(str(out_path))
        else:
            print(f"Warning: failed to create {out_path}: {res.stderr}")

    return created_files


def process_folder(
    input_folder: str,
    output_base: str,
    target_format: str = "wav",
    min_silence_duration: float = 1.5,
    noise_threshold: float = -40.0,
    sample_rate: int = 44100,
) -> None:
    """
    Обрабатывает все WebM-файлы в папке:
      1) конвертирует в WAV для детекции тишины
      2) ищет тишину
      3) нарезает в target_format (по умолчанию WAV)
    Для каждого входного файла создаётся подпапка.
    """
    input_folder = Path(input_folder).resolve()
    output_base = Path(output_base).resolve()

    webm_files = [f for f in input_folder.iterdir() if f.suffix.lower() == ".webm"]
    if not webm_files:
        print("No .webm files found.")
        return

    for webm_path in webm_files:
        base_name = webm_path.stem
        out_subdir = output_base / base_name
        out_subdir.mkdir(parents=True, exist_ok=True)

        print(f"Processing: {webm_path.name}")

        wav_path = convert_file(
            str(webm_path),
            format="wav",
            sample_rate=sample_rate,
        )
        print(f"  Converted to WAV for silence detection: {wav_path}")

        silences = detect_silence(
            wav_path,
            min_silence_duration=min_silence_duration,
            noise_threshold=noise_threshold,
        )
        if isinstance(silences, dict) and "error" in silences:
            print(f"  Silence detection error: {silences['error']}")
            continue
        print(f"  Detected {len(silences)} silence segments.")

        tracks = split_by_silence(
            source_path=str(webm_path),
            silence_data=silences,
            output_dir=str(out_subdir),
            output_format=target_format,
            sample_rate=sample_rate,
            min_segment_duration=10.0,
        )
        print(f"  Created {len(tracks)} tracks in: {out_subdir}")



def count_silenses(wav_path, min_silence_duration, noise_threshold):
    silences = detect_silence(
        wav_path,
        min_silence_duration=min_silence_duration,
        noise_threshold=noise_threshold,
    )
    if isinstance(silences, dict) and "error" in silences:
        print(f"  Silence detection error: {silences['error']}")
        return
    print(f"  Detected {len(silences)} silence segments.")


def convert_all_to_opus(
    source_base: str,
    dest_base: str,
    bitrate: str = "128k"
) -> List[str]:
    """
    Рекурсивно конвертирует все .wav файлы из source_base в .opus в dest_base,
    сохраняя структуру подпапок.

    Параметры:
        source_base - папка-источник (например, "./splitted_tracks")
        dest_base   - папка назначения (будет создана, например, "./opus_out")
        bitrate     - битрейт Opus (по умолчанию 128k; для хорошего качества можно ставить 160k–192k)

    Возвращает:
        Список путей к созданным OPUS-файлам.
    """
    source_root = Path(source_base).resolve()
    dest_root = Path(dest_base).resolve()

    if not source_root.exists():
        raise FileNotFoundError(f"Source folder not found: {source_root}")

    dest_root.mkdir(parents=True, exist_ok=True)

    created_files = []

    for wav_path in source_root.rglob("*.wav"):
        rel_path = wav_path.relative_to(source_root)
        opus_path = dest_root / rel_path.with_suffix(".opus")
        opus_path.parent.mkdir(parents=True, exist_ok=True)

        print(f"Converting: {rel_path} -> {opus_path}")

        try:
            converted_path = convert_file(
                input_path=str(wav_path),
                output_path=str(opus_path),
                format="opus",
                bitrate=bitrate
            )
            created_files.append(converted_path)
        except Exception as e:
            print(f"Error converting {wav_path}: {e}")

    return created_files


def convert_all_to_mp3(
    source_base: str,
    dest_base: str,
    bitrate: str = "320k"
) -> List[str]:
    """
    Рекурсивно конвертирует все .wav файлы из source_base в .mp3 в dest_base,
    сохраняя структуру подпапок.

    Параметры:
        source_base - папка-источник (например, "./splitted_tracks")
        dest_base   - папка назначения (будет создана, например, "./mp3_out")
        bitrate     - битрейт MP3 (по умолчанию 320k)

    Возвращает:
        Список путей к созданным MP3-файлам.
    """
    source_root = Path(source_base).resolve()
    dest_root = Path(dest_base).resolve()

    if not source_root.exists():
        raise FileNotFoundError(f"Source folder not found: {source_root}")

    dest_root.mkdir(parents=True, exist_ok=True)

    created_files = []

    for wav_path in source_root.rglob("*.wav"):

        rel_path = wav_path.relative_to(source_root)

        mp3_path = dest_root / rel_path.with_suffix(".mp3")

        mp3_path.parent.mkdir(parents=True, exist_ok=True)

        print(f"Converting: {rel_path} -> {mp3_path}")

        try:
            converted_path = convert_file(
                input_path=str(wav_path),
                output_path=str(mp3_path),
                format="mp3",
                bitrate=bitrate
            )
            created_files.append(converted_path)
        except Exception as e:
            print(f"Error converting {wav_path}: {e}")

    return created_files


if __name__ == "__main__":
    INPUT_FOLDER = "./webm_input"
    OUTPUT_BASE = "./splitted_tracks"
    MP3_OUT_BASE = "./mp3_out"

    print("\n--- Starting MP3 conversion ---")
    converted = convert_all_to_mp3(
        source_base=OUTPUT_BASE,
        dest_base=MP3_OUT_BASE,
        bitrate="320k"
    )
    print(f"\nDone! Converted {len(converted)} files to MP3 in {MP3_OUT_BASE}")
'''
    print("\n--- Starting MP3 conversion ---")
    converted = convert_all_to_mp3(
        source_base=OUTPUT_BASE,
        dest_base=MP3_OUT_BASE,
        bitrate="320k"
    )
    print(f"\nDone! Converted {len(converted)} files to MP3 in {MP3_OUT_BASE}")
'''
'''
    process_folder(
        input_folder=INPUT_FOLDER,
        output_base=OUTPUT_BASE,
        target_format="wav",            # для Audio CD лучше WAV
        min_silence_duration=1.5,
        noise_threshold=-40.0,
        sample_rate=44100,
    )
    '''

'''
    count_silenses("webm_input/B.B. King & Santana – Echoes of Soul & Flame  Inspired Tribute.wav",
                   min_silence_duration = 1.1,
                   noise_threshold=-30.0)
    '''

'''
    print("\n--- Starting OPUS conversion ---")
    converted = convert_all_to_opus(
        source_base=OUTPUT_BASE,
        dest_base=OPUS_OUT_BASE,
        bitrate="192k"
    )
    print(f"\nDone! Converted {len(converted)} files to OPUS in {OPUS_OUT_BASE}")

'''

