"""Replace remaster English dialogue with the French dub from the 2021 patches."""
import ctypes
import difflib
import os
import re
import shutil
import struct
import subprocess
import unicodedata
import wave
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GAME = None
ARCHIVES = None
BACKUP = None
FFMPEG = None
INNO = ROOT / "tools" / "innoextract.exe"
TTARCH = ROOT / "tools" / "publish" / "TtarchList.exe"
PATCHED = ROOT / "patched"
ARCHIVE_NAME = "SM1_pc_SamMax{episode}_uncompressed.ttarch2"
# Les archives anglaises d'origine font moins de 90 Mo. La version française, en PCM, dépasse 200 Mo.
ENGLISH_MAX_BYTES = 120 * 1024 * 1024

GENERIC_KEY = "34246C3343726C7564326553576945324F6163396C7574786C3732522D2A384931714F346F616A6C5F24652369616370342A75466C6530"
EPISODES = {
    101: ["92CA9A8185E46473A3BFD6D17FC6CB88995B80D8AAC297E79651A0A89AD9AE95D7766280B4C4A6B9D6ECA99C6885B3DC92C49E64A0A392"],
    102: [
        "92CA9A8185E46473A4BFD6D17FC6CB88990180D8AAC297E79651A1A89AD9AE95D7766281B4C4A6B9D6ECA99C6985B3DC92C49E64A0A492",
        "82A3898889D89FB7D3D8DAC082D7C2C1CE8DA1EA99C897DCB282E786699EBD7799A186B4CDCFADCDE1D9D4BCA9A1BCD4A8D7998FC3D3AC",
    ],
    103: [
        "92CA9A8185E46473A5BFD6D17FC6CB88995D80D8AAC297E79651A2A89AD9AE95D7766282B4C4A6B9D6ECA99C6A85B3DC92C49E64A0A592",
        "82A3898889D89FB7D3D8DAC082D7C2C1CE8DA1EA99C897DCB282E786699FBD7799A186B4CDCFADCDE1D9D4BCA9A1BCD4A8D7998FC3D3AC",
    ],
    104: [
        "92CA9A8185E46473A6BFD6D17FC6CB88995E80D8AAC297E79651A3A89AD9AE95D7766283B4C4A6B9D6ECA99C6B85B3DC92C49E64A0A692",
        "82A3898889D89FB7D3D8DAC082D7C2C1CE8DA1EA99C897DCB282E78669A0BD7799A186B4CDCFADCDE1D9D4BCA9A1BCD4A8D7998FC3D3AC",
    ],
    105: [
        "92CA9A8185E46473A7BFD6D17FC6CB88995F80D8AAC297E79651A4A89AD9AE95D7766284B4C4A6B9D6ECA99C6C85B3DC92C49E64A0A792",
        "82A3898889D89FB7D3D8DAC082D7C2C1CE8DA1EA99C897DCB282E78669A1A46EBB9997BBCDD79AD8DAD0C8DEA69CB7D2B9D68286D1DF8C",
    ],
    106: [
        "92CA9A8185E46473A8BFD6D17FC6CB88996080D8AAC297E79651A5A89AD9AE95D7766285B4C4A6B9D6ECA99C6D85B3DC92C49E64A0A892",
        "82A3898889D89FB7D3D8DAC082D7C2C1CE8DA1EA99C897DCB282E78669A2A46EBB9997BBCDD79AD8DAD0C8DEA69CB7D2B9D68286D1DF8C",
    ],
}
SPEAKERS = {
    "SAM", "MAX", "TEMP", "NARR", "SYS", "BOSCO", "FLINT", "MOM", "DAD",
    "GIRL", "BOY", "COP", "AGENT", "VOICE", "RADIO",
}
RATE_INDEX = {
    4000: 0, 8000: 1, 11000: 2, 11025: 3, 16000: 4, 22050: 5,
    24000: 6, 32000: 7, 44100: 8, 48000: 9, 96000: 10,
}


def ogg_crc_table():
    table = []
    for i in range(256):
        r = (i << 24) & 0xFFFFFFFF
        for _ in range(8):
            r = ((r << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if r & 0x80000000 else (r << 1) & 0xFFFFFFFF
        table.append(r)
    return table


CRC = ogg_crc_table()


def ogg_crc(data: bytes) -> int:
    c = 0
    for b in data:
        c = ((c << 8) ^ CRC[((c >> 24) & 0xFF) ^ b]) & 0xFFFFFFFF
    return c


def ogg_page(packets, serial, seq, granule, bos=False, eos=False):
    segs = []
    body = bytearray()
    for pkt in packets:
        n = len(pkt)
        while n >= 255:
            segs.append(255)
            n -= 255
        segs.append(n)
        body += pkt
    header_type = (0x02 if bos else 0) | (0x04 if eos else 0)
    head = bytearray()
    head += b"OggS"
    head += struct.pack("<BBQIIIB", 0, header_type, granule, serial, seq, 0, len(segs))
    head += bytes(segs)
    page = bytes(head) + bytes(body)
    crc = ogg_crc(page)
    return page[:22] + struct.pack("<I", crc) + page[26:]


def speex_header(rate, mode, frame_size, channels, frames_per_packet):
    hdr = bytearray(80)
    hdr[0:8] = b"Speex   "
    ver = b"speex-1.0.4"
    hdr[8:8 + len(ver)] = ver
    struct.pack_into(
        "<13i", hdr, 28,
        1, 80, rate, mode, 4, channels, -1, frame_size, 0, frames_per_packet, 0, 0, 0,
    )
    return bytes(hdr)


def parse_vox(data: bytes):
    if data[:4] != b"NIBM":
        raise ValueError("not a v1 vox")
    name_len = struct.unpack_from("<I", data, 8)[0]
    pos = 12 + name_len + 4
    encrypted = data[pos:pos + 1] == b"1"
    pos += 5
    data_block = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    frame_size, rate, mode = struct.unpack_from("<III", data, pos)
    pos += 16
    count = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    offsets = list(struct.unpack_from("<" + "I" * count, data, pos))
    start = pos + count * 4
    if start != len(data) - data_block:
        start = pos + count * 4
    packets = []
    for i, off in enumerate(offsets):
        size = (len(data) - (off + start)) if i == count - 1 else offsets[i + 1] - off
        packets.append(data[start + off:start + off + size])
    return {
        "encrypted": encrypted,
        "frame_size": frame_size,
        "rate": rate,
        "mode": mode,
        "packets": packets,
    }


def vox_to_ogg(info, frames_per_packet):
    audio = info["packets"][1:]
    stereo = bool(audio and len(audio[0]) >= 2 and audio[0][0] == 0x74 and audio[0][1] & 0x80)
    channels = 2 if stereo else 1
    serial = 0x53414D31
    pages = [
        ogg_page([speex_header(info["rate"], info["mode"], info["frame_size"], channels, frames_per_packet)], serial, 0, 0, bos=True),
        ogg_page([struct.pack("<I", 7) + b"sammaxfr" + struct.pack("<I", 0)], serial, 1, 0),
    ]
    granule = 0
    for i, pkt in enumerate(audio):
        granule += info["frame_size"] * frames_per_packet
        pages.append(ogg_page([pkt], serial, 2 + i, granule, eos=(i == len(audio) - 1)))
    return b"".join(pages)


def decode_vox_bytes(data: bytes, frames_per_packet: int, ffmpeg=None):
    info = parse_vox(data)
    ogg = vox_to_ogg(info, frames_per_packet)
    proc = subprocess.run(
        [str(ffmpeg or FFMPEG), "-hide_banner", "-loglevel", "error", "-c:a", "libspeex", "-f", "ogg", "-i", "pipe:0",
         "-acodec", "pcm_s16le", "-f", "wav", "pipe:1"],
        input=ogg, capture_output=True, creationflags=0x08000000,
    )
    if proc.returncode != 0 or not proc.stdout:
        err = proc.stderr.decode("utf-8", "replace")[-300:]
        raise RuntimeError(err or "ffmpeg failed")
    with wave.open(BytesIO(proc.stdout)) as w:
        channels = w.getnchannels()
        rate = w.getframerate()
        width = w.getsampwidth()
        pcm = w.readframes(w.getnframes())
        frames = len(pcm) // (channels * width)
    if frames <= 0 or rate not in RATE_INDEX:
        raise RuntimeError(f"bad wav rate={rate} frames={frames}")
    return pcm, channels, rate, frames, info


def sample_mode(num_samples, rate_index, channels_index):
    mode = (num_samples & 0x3FFFFFFF) << 34
    mode |= (channels_index & 0x03) << 5
    mode |= (rate_index & 0x0F) << 1
    return mode


def build_fsb(pcm: bytes, num_samples: int, channels: int, rate: int) -> bytes:
    name = b"voice\x00"
    name_table = struct.pack("<I", 4) + name
    header_bits = sample_mode(num_samples, RATE_INDEX[rate], 0 if channels == 1 else 1)
    sample_header = struct.pack("<Q", header_bits)
    header = bytearray(0x3C)
    header[0:4] = b"FSB5"
    struct.pack_into("<IIIIII", header, 4, 1, 1, len(sample_header), len(name_table), len(pcm), 2)
    return bytes(header) + sample_header + name_table + pcm


def decode_task(task):
    wav_name, vox_path, dest, frames_per_packet, ffmpeg = task
    try:
        pcm, channels, rate, frames, _info = decode_vox_bytes(
            Path(vox_path).read_bytes(), frames_per_packet, ffmpeg)
        Path(dest).write_bytes(build_fsb(pcm, frames, channels, rate))
        return wav_name, frames, None
    except Exception as ex:
        return wav_name, 0, str(ex)


def norm(text: str) -> str:
    text = text.replace("\r", " ").replace("\n", " ").replace("\u00a0", " ").replace("\u200b", "")
    text = re.sub(r"\{[^}]*\}", "", text)
    text = re.sub(r"\[[^\]]*\]", "", text)
    text = unicodedata.normalize("NFKC", text)
    text = (text.replace("’", "'").replace("‘", "'").replace("´", "'")
            .replace("…", "...").replace("–", "-").replace("—", "-"))
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def loose(text: str) -> str:
    text = norm(text).replace("'", "")
    text = re.sub(r"[^0-9a-zàâäçéèêëîïôùûüÿœæ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def folded(text: str) -> str:
    text = loose(text)
    text = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def is_speaker(text: str) -> bool:
    token = text.strip()
    # Speaker tags are stored in capitals. A spoken line can be the name itself ("Max").
    if not token.isupper():
        return False
    if token in SPEAKERS:
        return True
    return len(token) <= 16 and " " not in token and not any(ch in token for ch in ".?!,;:")


def old_pairs(path: Path):
    data = path.read_bytes()
    marks = []
    i = 0
    while i < len(data) - 8:
        v, v2 = struct.unpack_from("<II", data, i)
        if v == v2 and 20_000_000 <= v <= 60_000_000 and (v & 0x7F) == 0:
            marks.append((i, v))
            i += 8
        else:
            i += 1
    pairs = []
    for n, (off, vid) in enumerate(marks):
        end = marks[n + 1][0] if n + 1 < len(marks) else min(len(data), off + 900)
        chunk = data[off:end]
        strings = []
        j = 0
        while j < len(chunk) - 8:
            ln = struct.unpack_from("<I", chunk, j)[0]
            if 1 <= ln <= 500 and j + 4 + ln <= len(chunk):
                raw = chunk[j + 4:j + 4 + ln]
                if raw and all(c in (9, 10, 13) or c >= 32 for c in raw):
                    text = raw.decode("cp1252", "replace")
                    letters = sum(1 for ch in text if ch.isalpha())
                    if letters >= 1 and "\ufffd" not in text:
                        strings.append(text)
                        j += 4 + ln
                        continue
            j += 1
        spoken = [s for s in strings if not is_speaker(s)]
        if spoken:
            pairs.append((vid, spoken[0]))
    return pairs


def landb_headers(data: bytes):
    # Each record starts with: id, 8-byte hash, id again. The text follows the header.
    heads = []
    for i in range(0, len(data) - 16):
        v = struct.unpack_from("<I", data, i)[0]
        if 300_000_000 <= v <= 500_000_000 and struct.unpack_from("<I", data, i + 12)[0] == v:
            heads.append((i, v))
    return heads


def new_pairs(folder: Path):
    import bisect
    pairs = []
    for path in sorted(folder.glob("*.landb")):
        data = path.read_bytes()
        heads = landb_headers(data)
        positions = [pos for pos, _ in heads]
        seen = set()
        i = 0
        while i < len(data) - 24:
            ln = struct.unpack_from("<I", data, i)[0]
            if 2 <= ln <= 500 and i + 4 + ln + 24 <= len(data):
                raw = data[i + 4:i + 4 + ln]
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    i += 1
                    continue
                if text and not any(ord(ch) < 32 and ch not in "\n\r\t" for ch in text):
                    end = i + 4 + ln
                    if data[end:end + 8] == b"\x08\x00\x00\x00\x03\x00\x00\x00":
                        k = bisect.bisect_right(positions, i) - 1
                        if k >= 0 and heads[k][1] not in seen:
                            lid = heads[k][1]
                            seen.add(lid)
                            pairs.append((lid, text, path.name))
                        i = end + 8
                        continue
            i += 1
    return pairs


def assign_lines(old, new):
    exact = defaultdict(list)
    for vid, text in old:
        key = norm(text)
        if key:
            exact[key].append(vid)
    for vids in exact.values():
        vids.sort()

    mapping = {}
    how = {}
    used_counts = defaultdict(int)

    def take(bucket, key, lid, label):
        vids = bucket.get(key)
        if not vids:
            return False
        index = used_counts[(label, key)]
        mapping[lid] = vids[min(index, len(vids) - 1)]
        used_counts[(label, key)] = index + 1
        how[lid] = label
        return True

    pending = []
    for lid, text, name in sorted(new, key=lambda row: row[0]):
        if not take(exact, norm(text), lid, "exact"):
            pending.append((lid, text, name))

    used = set(mapping.values())
    loose_map = defaultdict(list)
    fold_map = defaultdict(list)
    for vid, text in old:
        if vid in used:
            continue
        lk, fk = loose(text), folded(text)
        if lk:
            loose_map[lk].append(vid)
        if fk:
            fold_map[fk].append(vid)
    for bucket in (loose_map, fold_map):
        for vids in bucket.values():
            vids.sort()

    still = []
    for lid, text, name in pending:
        if take(loose_map, loose(text), lid, "loose"):
            continue
        if take(fold_map, folded(text), lid, "fold"):
            continue
        still.append((lid, text, name))

    old_norm = [(vid, norm(text)) for vid, text in old]
    missing = []
    for lid, text, name in still:
        key = norm(text)
        if len(key) < 16:
            missing.append((lid, text, name))
            continue
        scored = sorted(
            ((difflib.SequenceMatcher(None, key, other).ratio(), vid) for vid, other in old_norm if other),
            reverse=True,
        )
        best = scored[0][0] if scored else 0
        second = scored[1][0] if len(scored) > 1 else 0
        if best >= 0.96 and best - second >= 0.05:
            mapping[lid] = scored[0][1]
            how[lid] = "fuzzy"
        else:
            missing.append((lid, text, name))
    return mapping, how, missing


def find_patch(episode: int) -> Path:
    hits = []
    for folder in (ROOT / "patches", ROOT):
        if folder.exists():
            hits.extend(folder.glob(f"SaM_{episode}_*.exe"))
    hits = sorted(set(hits))
    if len(hits) != 1:
        raise SystemExit(
            f"patch de l'épisode {episode} introuvable.\n"
            f"Place les 6 installeurs français (SaM_101_....exe à SaM_106_....exe) dans le dossier :\n"
            f"  {ROOT / 'patches'}"
        )
    return hits[0]


def find_ffmpeg() -> Path:
    found = shutil.which("ffmpeg")
    if found:
        return Path(found)
    for candidate in (
        Path(r"C:\Program Files\ffmpeg\ffmpeg.exe"),
        Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
    ):
        if candidate.exists():
            return candidate
    raise SystemExit("ffmpeg est introuvable. Installe-le et ajoute-le au PATH : https://ffmpeg.org/download.html")


def find_steam_game() -> Path:
    default = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Sam & Max Save the World")
    if (default / "SamMax100.exe").exists():
        return default
    libraries = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            libraries.append(Path(winreg.QueryValueEx(key, "SteamPath")[0]) / "steamapps")
    except OSError:
        pass
    for library in list(libraries):
        vdf = library / "libraryfolders.vdf"
        if not vdf.exists():
            continue
        text = vdf.read_text(encoding="utf-8", errors="replace")
        for match in re.findall(r'"path"\s+"([^"]+)"', text):
            libraries.append(Path(match.replace("\\\\", "\\")) / "steamapps")
    for library in libraries:
        game = library / "common" / "Sam & Max Save the World"
        if (game / "SamMax100.exe").exists():
            return game
    raise SystemExit(
        "jeu introuvable. Indique son dossier avec :\n"
        '  python apply_vf.py all --game "C:\\...\\Sam & Max Save the World"'
    )


def configure(game=None):
    global GAME, ARCHIVES, BACKUP, FFMPEG
    GAME = Path(game) if game else find_steam_game()
    if not (GAME / "SamMax100.exe").exists():
        raise SystemExit(f"SamMax100.exe est absent de {GAME}")
    ARCHIVES = GAME / "Archives"
    BACKUP = ARCHIVES / "EnglishVoiceBackup"
    FFMPEG = find_ffmpeg()
    if not INNO.exists():
        raise SystemExit(f"outil manquant : {INNO}")
    if not TTARCH.exists():
        raise SystemExit(f"outil manquant : {TTARCH}")
    print(f"jeu : {GAME}")


def archive_path(episode: int, folder: Path) -> Path:
    return folder / ARCHIVE_NAME.format(episode=episode)


def episode_root(episode: int) -> Path:
    return ROOT / "extracted" / "vox" / str(episode)


def ensure_patch_files(episode: int):
    root = episode_root(episode)
    if any(root.rglob("*.vox")):
        return
    root.mkdir(parents=True, exist_ok=True)
    print(f"extraction du patch {episode}...")
    subprocess.check_call([str(INNO), "-e", "-m", "-s", "-d", str(root), str(find_patch(episode))])


def langdb_path(episode: int) -> Path:
    dest = ROOT / "extracted" / "lang" / str(episode) / "french.langdb"
    if dest.exists():
        return dest
    if episode == 101:
        legacy = ROOT / "extracted" / "old101" / "french.langdb"
        if legacy.exists():
            return legacy
    ensure_patch_files(episode)
    ttarch = next(episode_root(episode).rglob("*_french.ttarch"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"lecture de {ttarch.name}...")
    subprocess.check_call([str(TTARCH), "--langdb", str(ttarch), EPISODES[episode][0], str(dest.parent)])
    if not dest.exists():
        raise SystemExit(f"french.langdb absent pour {episode}")
    return dest


def landb_dir(episode: int) -> Path:
    dest = ROOT / "extracted" / "landb" / str(episode)
    if any(dest.glob("*.landb")):
        return dest
    if episode == 101:
        legacy = ROOT / "extracted" / "remaster101" / "landb"
        if any(legacy.glob("*.landb")):
            return legacy
    dest.mkdir(parents=True, exist_ok=True)
    archive = ARCHIVES / f"SM1_pc_SamMax{episode}_compressed.ttarch2"
    print(f"extraction des sous-titres {episode}...")
    subprocess.check_call([str(TTARCH), str(archive), "name:french.landb", str(dest)])
    return dest


def vox_index(episode: int):
    ensure_patch_files(episode)
    root = episode_root(episode)
    plain = root / "plain"
    if not plain.exists():
        print(f"déchiffrement des voix longues {episode}...")
        subprocess.check_call([
            str(TTARCH), "--plainvox", str(root), str(plain), *EPISODES[episode], GENERIC_KEY,
        ])
    found = {}
    for path in root.rglob("*.vox"):
        if "plain" in path.parts:
            continue
        found[path.stem.lower()] = path
    if plain.exists():
        for path in plain.glob("*.vox"):
            found[path.stem.lower()] = path
    return found


def read_entries(archive: Path):
    with archive.open("rb") as handle:
        magic = handle.read(4)
        if magic != b"NCTT":
            raise SystemExit(f"{archive.name} n'est pas une archive non compressée")
        size, tag, name_size, count = struct.unpack("<Q4sII", handle.read(20))
        if tag != b"4ATT":
            raise SystemExit(f"en-tête inattendu {tag}")
        records = handle.read(count * 28)
        names = handle.read(name_size)
        files_offset = 24 + count * 28 + name_size
    entries = []
    blob = records
    prefix = magic + struct.pack("<Q4sII", size, tag, name_size, count) + records + names
    for i in range(count):
        crc, offset, fsize, ignore, chunk, name_off = struct.unpack_from("<QQIIHH", blob, i * 28)
        start = chunk * 65536 + name_off
        end = names.index(b"\x00", start)
        name = names[start:end].decode("ascii")
        entries.append({
            "i": i, "crc": crc, "offset": offset, "size": fsize,
            "ignore": ignore, "name": name,
        })
    return prefix, files_offset, entries


def choose_frames(sample: Path) -> int:
    _pcm, _ch, _rate, one, info = decode_vox_bytes(sample.read_bytes(), 1)
    expected = (len(info["packets"]) - 1) * info["frame_size"]
    _pcm8, _ch8, _rate8, eight, _info8 = decode_vox_bytes(sample.read_bytes(), 8)
    print(f"selftest {sample.name}: 1-frame={one} 8-frame={eight} expected={expected}")
    if one != expected:
        raise SystemExit("le décodage Speex ne donne pas la durée attendue")
    return 8 if eight == one else 1


def selftest():
    ensure_patch_files(101)
    sample = next(episode_root(101).rglob("27614720.vox"), None)
    if sample is None:
        sample = next(p for p in episode_root(101).rglob("*.vox") if "plain" not in p.parts)
    frames = choose_frames(sample)
    plain_dir = episode_root(101) / "plain"
    if not any(plain_dir.glob("*.vox")):
        return frames
    plain = max(plain_dir.glob("*.vox"), key=lambda path: path.stat().st_size)
    pcm, channels, rate, count, info = decode_vox_bytes(plain.read_bytes(), frames)
    frame = info["frame_size"]
    audio_packets = len(info["packets"]) - 1
    print(f"long {plain.name}: packets={audio_packets} samples={count} rate={rate} ch={channels}")
    if audio_packets >= 64:
        import array
        samples = array.array("h", pcm)
        def rms(index):
            start = index * frame
            chunk = samples[start:start + frame]
            return int((sum(v * v for v in chunk) / len(chunk)) ** 0.5)
        print(f"  rms frame62={rms(62)} frame63={rms(63)} frame64={rms(64)}")
    return frames


def report(episode: int):
    old = old_pairs(langdb_path(episode))
    new = new_pairs(landb_dir(episode))
    mapping, how, missing = assign_lines(old, new)
    counts = defaultdict(int)
    for label in how.values():
        counts[label] += 1
    print(f"ep{episode}: old={len(old)} new={len(new)} mapped={len(mapping)} {dict(counts)} missing={len(missing)}")
    out = ROOT / "extracted" / f"miss_{episode}.txt"
    out.write_text("\n".join(f"{lid}\t{name}\t{text}" for lid, text, name in missing[:80]), encoding="utf-8")
    print("miss file", out)
    return mapping


def build(episode: int, frames_per_packet: int):
    archive = archive_path(episode, ARCHIVES)
    backup = archive_path(episode, BACKUP)
    if backup.exists():
        archive = backup
    prefix, files_offset, entries = read_entries(archive)
    by_name = {entry["name"].lower(): entry for entry in entries}
    old = old_pairs(langdb_path(episode))
    new = new_pairs(landb_dir(episode))
    mapping, how, missing = assign_lines(old, new)
    voices = vox_index(episode)
    wanted = {}
    for lid, vid in mapping.items():
        name = f"{lid}.wav"
        vox = voices.get(str(vid))
        if name.lower() in by_name and vox is not None:
            wanted[name.lower()] = vox
    for stem, vox in voices.items():
        if stem.isdigit() or not stem.startswith("vox"):
            continue
        name = stem + ".wav"
        if name in by_name:
            wanted[name] = vox
    print(f"ep{episode}: {len(wanted)} fichiers voix à remplacer, {len(missing)} répliques sans correspondance")

    cache = ROOT / "extracted" / "fsbvox" / str(episode)
    cache.mkdir(parents=True, exist_ok=True)
    tasks = []
    for name, vox in wanted.items():
        dest = cache / (Path(vox).stem + ".fsb")
        if dest.exists() and dest.stat().st_size > 64 and dest.read_bytes()[:4] == b"FSB5":
            continue
        tasks.append((name, str(vox), str(dest), frames_per_packet, str(FFMPEG)))
    print(f"décodage de {len(tasks)} voix...")
    failures = []
    if tasks:
        workers = min(4, os.cpu_count() or 2)
        with ProcessPoolExecutor(max_workers=workers) as pool:
            done = 0
            for wav_name, _frames, err in pool.map(decode_task, tasks, chunksize=4):
                done += 1
                if err:
                    failures.append((wav_name, err))
                if done % 200 == 0:
                    print(f"  {done}/{len(tasks)}")
    if failures:
        print(f"échecs de décodage: {len(failures)}")
        for name, err in failures[:8]:
            print(" ", name, err.splitlines()[-1] if err else "")

    replacements = {}
    for name, vox in wanted.items():
        dest = cache / (Path(vox).stem + ".fsb")
        if dest.exists() and dest.read_bytes()[:4] == b"FSB5":
            replacements[name] = dest
    print(f"fsb prêts: {len(replacements)}")

    cursor = 0
    plan = []
    for entry in entries:
        payload = replacements.get(entry["name"].lower())
        size = payload.stat().st_size if payload else entry["size"]
        plan.append((entry, payload, cursor, size))
        cursor += size
        cursor = (cursor + 15) & ~15

    header = bytearray(prefix)
    total = len(header) + cursor
    struct.pack_into("<Q", header, 4, total - 12)
    for entry, _payload, offset, size in plan:
        at = 24 + entry["i"] * 28
        struct.pack_into("<Q", header, at + 8, offset)
        struct.pack_into("<i", header, at + 16, size)

    PATCHED.mkdir(exist_ok=True)
    dest = archive_path(episode, PATCHED)
    print(f"écriture de {dest.name} ({total / 1048576:.1f} Mo)...")
    with archive.open("rb") as src, dest.open("wb") as out:
        out.write(header)
        written = 0
        for entry, payload, offset, size in plan:
            pad = offset - written
            if pad:
                out.write(b"\x00" * pad)
            if payload:
                out.write(payload.read_bytes())
            else:
                src.seek(files_offset + entry["offset"])
                out.write(src.read(entry["size"]))
            written = offset + size
        tail = cursor - written
        if tail:
            out.write(b"\x00" * tail)
    verify_archive(archive, dest, replacements)
    print(f"ep{episode} prêt: {dest}")
    return dest


def verify_archive(original: Path, rebuilt: Path, replacements: dict):
    _op, ofiles, oentries = read_entries(original)
    _np, nfiles, nentries = read_entries(rebuilt)
    if len(oentries) != len(nentries):
        raise SystemExit("le nombre de fichiers a changé")
    if [e["name"] for e in oentries] != [e["name"] for e in nentries]:
        raise SystemExit("les noms de fichiers ont changé")
    replaced = kept = 0
    with original.open("rb") as old, rebuilt.open("rb") as new:
        for left, right in zip(oentries, nentries):
            new.seek(nfiles + right["offset"])
            blob = new.read(right["size"])
            if left["name"].lower() in replacements:
                if blob[:4] != b"FSB5":
                    raise SystemExit(f"{left['name']} n'est pas un FSB")
                replaced += 1
            else:
                old.seek(ofiles + left["offset"])
                if blob != old.read(left["size"]):
                    raise SystemExit(f"{left['name']} a été modifié par erreur")
                kept += 1
    print(f"vérification archive: {replaced} voix françaises, {kept} fichiers inchangés")
    samples = [name for name in list(replacements)[:5]]
    fmod_check(rebuilt, samples)


def fmod_check(archive: Path, names):
    _prefix, files_offset, entries = read_entries(archive)
    wanted = {name.lower() for name in names}
    os.add_dll_directory(str(GAME))
    dll = ctypes.WinDLL(str(GAME / "fmod64.dll"))
    system = ctypes.c_void_p()
    create = dll.FMOD_System_Create
    create.restype = ctypes.c_int
    create.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint]
    if create(ctypes.byref(system), 0) != 0:
        raise SystemExit("FMOD_System_Create a échoué")
    init = dll.FMOD_System_Init
    init.restype = ctypes.c_int
    init.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p]
    if init(system, 32, 0, None) != 0:
        raise SystemExit("FMOD_System_Init a échoué")
    createsound = dll.FMOD_System_CreateSound
    createsound.restype = ctypes.c_int
    createsound.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    getnum = dll.FMOD_Sound_GetNumSubSounds
    getnum.restype = ctypes.c_int
    getnum.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
    getsub = dll.FMOD_Sound_GetSubSound
    getsub.restype = ctypes.c_int
    getsub.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    getlen = dll.FMOD_Sound_GetLength
    getlen.restype = ctypes.c_int
    getlen.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint), ctypes.c_uint]
    with archive.open("rb") as handle:
        checked = 0
        for entry in entries:
            if entry["name"].lower() not in wanted:
                continue
            handle.seek(files_offset + entry["offset"])
            blob = handle.read(entry["size"])
            tmp = ROOT / "extracted" / "_fmod_probe.fsb"
            tmp.write_bytes(blob)
            sound = ctypes.c_void_p()
            rc = createsound(system, str(tmp).encode("utf-8"), 0, None, ctypes.byref(sound))
            num = ctypes.c_int()
            getnum(sound, ctypes.byref(num))
            target = sound
            if num.value > 0:
                sub = ctypes.c_void_p()
                getsub(sound, 0, ctypes.byref(sub))
                target = sub
            length = ctypes.c_uint()
            getlen(target, ctypes.byref(length), 2)
            print(f"  fmod {entry['name']}: rc={rc} samples={length.value}")
            if rc != 0 or length.value <= 0:
                raise SystemExit(f"FMOD refuse {entry['name']}")
            checked += 1
    if checked == 0:
        raise SystemExit("aucun échantillon FMOD vérifié")


def ensure_english_backup(episode: int):
    BACKUP.mkdir(exist_ok=True)
    dst = archive_path(episode, ARCHIVES)
    bak = archive_path(episode, BACKUP)
    if not dst.exists():
        raise SystemExit(f"archive absente : {dst}")
    if bak.exists():
        if bak.stat().st_size > ENGLISH_MAX_BYTES:
            raise SystemExit(
                f"la sauvegarde {bak.name} n'est pas la version anglaise d'origine.\n"
                "Ne continue pas : restaure d'abord les fichiers du jeu avec Steam."
            )
        print(f"sauvegarde déjà présente : {bak.name}")
        return
    if dst.stat().st_size > ENGLISH_MAX_BYTES:
        raise SystemExit(
            f"{dst.name} est déjà modifié et aucune sauvegarde anglaise n'existe.\n"
            "Dans Steam : propriétés du jeu, fichiers installés, vérifier l'intégrité.\n"
            "Relance ensuite l'installation."
        )
    print(f"sauvegarde des voix anglaises : {bak.name}")
    shutil.copy2(dst, bak)


def install(episodes):
    for episode in episodes:
        ensure_english_backup(episode)
        src = archive_path(episode, PATCHED)
        dst = archive_path(episode, ARCHIVES)
        if not src.exists():
            raise SystemExit(f"archive française absente, lance d'abord la construction : {src}")
        print(f"installation {dst.name}")
        shutil.copy2(src, dst)
    print("voix françaises installées")
    print(f"pour revenir aux voix anglaises : python apply_vf.py restore")
    print(f"les originaux sont dans {BACKUP}")


def restore(episodes):
    for episode in episodes:
        bak = archive_path(episode, BACKUP)
        dst = archive_path(episode, ARCHIVES)
        if not bak.exists():
            raise SystemExit(f"aucune sauvegarde pour l'épisode {episode} : {bak}")
        if bak.stat().st_size > ENGLISH_MAX_BYTES:
            raise SystemExit(f"la sauvegarde {bak.name} ne ressemble pas à la version anglaise")
        print(f"restauration {dst.name}")
        shutil.copy2(bak, dst)
    print("voix anglaises d'origine restaurées")


def status():
    print(f"sauvegardes : {BACKUP}")
    for episode in EPISODES:
        dst = archive_path(episode, ARCHIVES)
        bak = archive_path(episode, BACKUP)
        if not dst.exists():
            print(f"  {episode} : archive absente")
            continue
        kind = "français" if dst.stat().st_size > ENGLISH_MAX_BYTES else "anglais"
        saved = "sauvegarde ok" if bak.exists() else "pas de sauvegarde"
        print(f"  {episode} : {kind} ({dst.stat().st_size / 1048576:.0f} Mo), {saved}")


def episodes_from_arg(text: str):
    if text in ("0", "all", "tous"):
        return list(EPISODES)
    episode = int(text)
    if episode not in EPISODES:
        raise SystemExit(f"épisode inconnu : {episode}")
    return [episode]


def main():
    import sys
    argv = sys.argv[1:]
    game = None
    args = []
    i = 0
    while i < len(argv):
        if argv[i] == "--game" and i + 1 < len(argv):
            game = argv[i + 1]
            i += 2
        else:
            args.append(argv[i])
            i += 1
    cmd = args[0] if args else "status"
    configure(game)
    if cmd == "status":
        status()
    elif cmd == "report":
        report(int(args[1]) if len(args) > 1 else 101)
    elif cmd == "selftest":
        selftest()
    elif cmd == "build":
        frames = selftest()
        for episode in episodes_from_arg(args[1] if len(args) > 1 else "all"):
            ensure_english_backup(episode)
            build(episode, frames)
    elif cmd == "install":
        install(episodes_from_arg(args[1] if len(args) > 1 else "all"))
    elif cmd == "restore":
        restore(episodes_from_arg(args[1] if len(args) > 1 else "all"))
    elif cmd == "all":
        frames = selftest()
        targets = list(EPISODES)
        for episode in targets:
            ensure_english_backup(episode)
            build(episode, frames)
        install(targets)
    else:
        raise SystemExit("commandes : all, status, build, install, restore, report, selftest")


if __name__ == "__main__":
    main()
