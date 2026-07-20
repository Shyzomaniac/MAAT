import subprocess
import json
from pathlib import Path
import acoustid

WAV_FOLDER = "./splitted_tracks"
SIMILARITY_THRESHOLD = 80.0
CACHE_FILE = "fingerprints_cache.json"

def get_clean_fingerprint(file_path):
    """
    Использует FFmpeg для удаления тишины в начале/конце трека
    и генерирует отпечаток Chromaprint из очищенного аудиопотока.
    """
    # Вырезаем тишину (-af silenceremove)
    # Принудительно конвертируем в моно (-ac 1) и частоту 11025 Гц (-ar 11025)
    ffmpeg_cmd = [
        'ffmpeg', '-v', 'error', '-i', str(file_path),
        '-af', 'silenceremove=start_periods=1:start_threshold=-50dB:stop_periods=-1:stop_threshold=-50dB',
        '-ac', '1', '-ar', '11025', '-f', 's16le', 'pipe:1'
    ]

    try:
        # Запускаем FFmpeg и забираем PCM-поток из памяти
        process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout_data, stderr_data = process.communicate()

        if process.returncode != 0:
            print(f"❌ Ошибка FFmpeg для {file_path.name}: {stderr_data.decode().strip()}")
            return None

        if not stdout_data:
            print(f"⚠️ Файл {file_path.name} после удаления тишины оказался пустым.")
            return None

        # Оборачиваем данные в итератор
        pcm_iterator = iter([stdout_data])

        # В новых версиях pyacoustid функция возвращает ТОЛЬКО fingerprint,
        # а не кортеж (duration, fingerprint). Убираем лишнюю переменную.
        fingerprint = acoustid.fingerprint(11025, 1, pcm_iterator, maxlength=60)

        return fingerprint.decode('utf-8') if isinstance(fingerprint, bytes) else fingerprint

    except Exception as e:
        print(f"❌ Системная ошибка обработки {file_path.name}: {e}")
        return None


def compare_fingerprints(fp1_str, fp2_str):
    """
    Декодирует две строки отпечатков и вычисляет процент их схожести.
    """
    try:
        # Декодируем base64-строки в массивы 32-битных интов
        fp1_data, _ = acoustid.chromaprint.decode_fingerprint(fp1_str.encode('utf-8'))
        fp2_data, _ = acoustid.chromaprint.decode_fingerprint(fp2_str.encode('utf-8'))
    except Exception:
        return 0.0

    if not fp1_data or not fp2_data:
        return 0.0

    # Находим наименьшую длину для посимвольного сравнения
    min_len = min(len(fp1_data), len(fp2_data))
    if min_len == 0:
        # Если один из массивов пустой, совпадения нет
        return 0.0

    matching_bits = 0
    total_bits = min_len * 32

    for i in range(min_len):
        # XOR показывает различающиеся биты. Инвертируем, чтобы получить совпадающие.
        xor_result = fp1_data[i] ^ fp2_data[i]
        # Считаем количество единиц (совпавших битов)
        matching_bits += 32 - bin(xor_result).count('1')

    similarity = (matching_bits / total_bits) * 100
    return round(similarity, 2)


def load_cache():
    """Загружает существующий кэш из JSON."""
    cache_path = Path(CACHE_FILE)
    if cache_path.exists():
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                print(f"📦 Найдена база данных. Загрузка кэшированных альбомов.")
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Ошибка чтения кэша, создаем новый: {e}")
    return {}


def save_cache(cache_data):
    """Сохраняет базу данных в JSON."""
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
        print(f"💾 База данных успешно обновлена и сохранена в {CACHE_FILE}")
    except Exception as e:
        print(f"❌ Не удалось сохранить кэш: {e}")


def cleanup_cache_duplicates(cache, duplicates_set):
    """
    Удаляет из кэша все треки, которые были признаны дубликатами,
    и сохраняет обновленную чистую базу.
    """
    if not duplicates_set:
        print("\n🧹 Очистка кэша не требуется: дубликатов не найдено.")
        return

    print(f"\n🧹 Начало очистки кэша. Найдено дубликатов для удаления: {len(duplicates_set)}")

    for duplicate_path in duplicates_set:
        if "/" in duplicate_path:
            album_name, track_name = duplicate_path.split("/", 1)

            if album_name in cache and track_name in cache[album_name]:
                del cache[album_name][track_name]

                if not cache[album_name]:
                    del cache[album_name]

    save_cache(cache)
    print("💾 Кэш успешно очищен от дубликатов и сохранен.")


def delete_duplicate_files(wav_folder_path, duplicates_set, dry_run=True):
    """
    Удаляет физические файлы-дубликаты с диска на основе множества duplicates_set.
    dry_run=True — безопасный режим (только показывает, что удалит).
    dry_run=False — реальное удаление файлов.
    """
    if not duplicates_set:
        print("\n🚫 Нет файлов для удаления: список дубликатов пуст.")
        return

    base_path = Path(wav_folder_path)
    if not base_path.exists():
        print(f"\n❌ Ошибка: Базовая папка {wav_folder_path} не найдена на диске!")
        return

    print(f"\n{ '♻️ СИМУЛЯЦИЯ' if dry_run else '⚠️ ВНИМАНИЕ' }: Начинается удаление файлов с диска...")
    deleted_count = 0
    failed_count = 0

    for rel_path_str in duplicates_set:
        file_path = base_path / rel_path_str

        if file_path.exists():
            if dry_run:
                print(f"   [Будет удален]: {file_path.resolve()}")
                deleted_count += 1
            else:
                try:
                    file_path.unlink() # Физическое удаление файла
                    print(f"   [Удален]: {file_path.name} из {file_path.parent.name}")
                    deleted_count += 1
                except Exception as e:
                    print(f"   ❌ Не удалось удалить {file_path.name}: {e}")
                    failed_count += 1
        else:
            print(f"   ❓ Файл не найден на диске (возможно, уже удален): {rel_path_str}")

    status = "Условно удалено" if dry_run else "Успешно удалено"
    print(f"\n📊 Итог удаления: {status}: {deleted_count} файлов. Ошибок: {failed_count}.")
    if dry_run:
        print("💡 Чтобы запустить реальное удаление, передайте параметр dry_run=False.")



def scan_and_compare():
    wav_path = Path(WAV_FOLDER)
    if not wav_path.exists():
        print(f"Папка {WAV_FOLDER} не найдена!")
        return

    # 1. Загружаем кэш. Структура: { "Имя_Альбома": { "01.wav": "отпечаток" } }
    cache = load_cache()
    cache_updated = False

    print(f"1. Сканирование папок в {wav_path.resolve()}...")

    # Находим все уникальные подпапки (альбомы), где есть .wav файлы
    albums = set(p.parent for p in wav_path.rglob("*.wav"))

    for album_path in albums:
        album_name = album_path.name  # Уникальное имя папки-альбома

        if album_name not in cache:
            cache[album_name] = {}

        print(f"   💿 Сканирование альбома: '{album_name}'...")

        # Сканируем треки внутри альбома пофайлово
        for file in album_path.glob("*.wav"):
            track_name = file.name

            # Проверяем конкретный файл в кэше, а не весь альбом целиком
            if track_name in cache[album_name]:
                # Важно: старые "короткие" отпечатки (меньше 40 символов) отбрасываем
                if len(cache[album_name][track_name]) > 40:
                    continue

            print(f"      🎵 Анализ нового или измененного трека: {track_name}")
            fp = get_clean_fingerprint(file)
            if fp:
                cache[album_name][track_name] = fp
                cache_updated = True

    # Если появились новые данные — сохраняем их в JSON
    if cache_updated:
        save_cache(cache)

    # 2. Преобразуем структуру кэша в плоский список для попарного сравнения
    flat_tracks = []
    for album_name, tracks in cache.items():
        for track_name, fp in tracks.items():
            flat_tracks.append({
                'display_path': f"{album_name}/{track_name}",
                'fp': fp
            })

    print(f"\n2. Подготовка к анализу завершена. Всего треков в базе: {len(flat_tracks)}")
    print(f"3. Попарное сравнение со схожестью >= {SIMILARITY_THRESHOLD}%...\n")

    found_any = False
    # Множество для отслеживания треков, которые МЫ УЖЕ ВЫВЕЛИ как дубликаты,
    # чтобы не спамить в консоль одними и теми же парами по кругу
    already_printed_as_duplicate = set()

    for i in range(len(flat_tracks)):
        current_track_path = flat_tracks[i]['display_path']

        # Если этот трек уже признан чьим-то дубликатом, не делаем его «Эталоном»
        if current_track_path in already_printed_as_duplicate:
            continue

        current_duplicates = []

        for j in range(i + 1, len(flat_tracks)):
            # Вызываем новую функцию побитового сравнения через Хэмминг
            sim = compare_fingerprints(flat_tracks[i]['fp'], flat_tracks[j]['fp'])

            if sim >= SIMILARITY_THRESHOLD:
                current_duplicates.append((flat_tracks[j]['display_path'], sim))
                already_printed_as_duplicate.add(flat_tracks[j]['display_path'])

        if current_duplicates:
            found_any = True
            print("-" * 70)
            print(f"🔊 Найдена группа похожих треков:")
            print(f"   Эталон: {current_track_path}")
            for dup_path, score in current_duplicates:
                print(f"   -> Дубликат: {dup_path} (Схожесть: {score}%)")

    if not found_any:
        print("Похожих треков с заданным порогом не обнаружено.")

#опциональные вызовы. очистка кеша от дублей. автоматическое удаление файлов
    cleanup_cache_duplicates(cache, already_printed_as_duplicate)
    # Пока dry_run=True, файлы НЕ удаляются, а только выводятся в консоль для проверки!
    delete_duplicate_files(WAV_FOLDER, already_printed_as_duplicate, dry_run=True)


if __name__ == "__main__":
    scan_and_compare()
