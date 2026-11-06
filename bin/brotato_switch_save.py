#!/usr/bin/env python3
"""Brotato Switch binary save editor.

Exports and imports save files inside a Switch binary save container
used by the modded Nintendo Switch version of Brotato.

The container format:
    - 32-byte header:
        u32 data_size     - offset of the directory table's content_len field
        u32 version
        u32 reserved[3]
        u32 unknown       - opaque, carried over unchanged
        u32 body_size     - size of the file entries + directory table
        u32 entry_count   - number of file entries
    - File entries: u32 name_len + name + u32 content_len + content
    - Directory table: u32 count + count * (u32 name_len + name + u32 content_len=0)
    - Compressed content has an 8-byte sub-header:
        u32 0x05FE04FB (compression magic)
        u32 uncompressed_size
        zlib-compressed data
    - Zero-padded to 1024000 bytes

Files inside the container:
    ./save.json              - 12-line format (for brotato_save_editor.py)
    ./user/save_v2.json      - Windows v2 format (zlib compressed)
    ./user/save_v3_0.json    - Windows v3 format (zlib compressed)
    ./user/settings.json     - Game settings (zlib compressed)
    ./user/run_v3_0.json     - Current run state (zlib compressed)
"""

import argparse
import json
import os
import shutil
import struct
import sys
import zlib
from datetime import datetime

COMPRESSION_MAGIC = 0x05FE04FB
CONTAINER_SIZE = 1024000
HEADER_SIZE = 32

# Files we care about, mapped to their container paths
SAVE_FILE = "./user/save_v3_0.json"
SETTINGS_FILE = "./user/settings.json"
RUN_FILE = "./user/run_v3_0.json"

BACKUPS_DIR = None


# --------------------------------------------------------------------------- #
# container parsing
# --------------------------------------------------------------------------- #
class ContainerEntry:
    def __init__(self, name, content, raw_offset):
        self.name = name
        self.content = content
        self.raw_offset = raw_offset  # offset of content within the file


class Container:
    def __init__(self, path):
        with open(path, "rb") as f:
            self.data = bytearray(f.read())
        self.header = bytes(self.data[:HEADER_SIZE])
        self.entries = []
        self.dir_table = b""
        self._parse()

    def _parse(self):
        if len(self.data) < HEADER_SIZE:
            sys.exit("ERROR: file is too small to be a save container")

        self.data_size = struct.unpack_from("<I", self.data, 0)[0]
        self.version = struct.unpack_from("<I", self.data, 4)[0]
        self.reserved = struct.unpack_from("<III", self.data, 8)
        self.unknown = struct.unpack_from("<I", self.data, 0x14)[0]
        self.body_size = struct.unpack_from("<I", self.data, 0x18)[0]
        self.entry_count = struct.unpack_from("<I", self.data, 0x1C)[0]

        if not (HEADER_SIZE <= self.data_size <= len(self.data)):
            sys.exit(f"ERROR: invalid container data size {self.data_size}")

        offset = HEADER_SIZE
        while offset < self.data_size:
            if self._try_parse_dir_table(offset):
                self.dir_table = bytes(self.data[offset : self.data_size + 4])
                break

            name_len = struct.unpack_from("<I", self.data, offset)[0]
            if name_len == 0 or name_len > 256:
                break
            offset += 4
            name = self.data[offset : offset + name_len].decode("ascii", errors="replace")
            offset += name_len
            content_len = struct.unpack_from("<I", self.data, offset)[0]
            if content_len > self.data_size - offset:
                break
            offset += 4
            content = bytes(self.data[offset : offset + content_len])
            raw_offset = offset
            offset += content_len
            self.entries.append(ContainerEntry(name, content, raw_offset))

        if len(self.entries) != self.entry_count:
            print(f"  WARNING: header says {self.entry_count} entries, parsed {len(self.entries)}")

    def _try_parse_dir_table(self, offset):
        """Detect a directory table at offset. Returns True when one is found."""
        if offset + 4 > len(self.data):
            return False
        count = struct.unpack_from("<I", self.data, offset)[0]
        if count == 0 or count > 64:
            return False
        p = offset + 4
        names = []
        for _ in range(count):
            if p + 4 > len(self.data):
                return False
            name_len = struct.unpack_from("<I", self.data, p)[0]
            if name_len == 0 or name_len > 256:
                return False
            p += 4
            name = self.data[p : p + name_len].decode("ascii", errors="replace")
            p += name_len
            if p + 4 > len(self.data):
                return False
            content_len = struct.unpack_from("<I", self.data, p)[0]
            p += 4
            if content_len != 0:
                return False
            if not name.startswith("./"):
                return False
            names.append(name)
        return p == self.data_size + 4

    def find_entry(self, name):
        for entry in self.entries:
            if entry.name == name:
                return entry
        return None

    def get_decompressed(self, name):
        """Get decompressed content for a given file entry."""
        entry = self.find_entry(name)
        if not entry:
            return None
        content = entry.content
        if len(content) >= 8 and content[:4] == struct.pack("<I", COMPRESSION_MAGIC):
            return zlib.decompress(content[8:])
        if content[:2] == b"\x78\x9c":
            return zlib.decompress(content)
        return content

    def rebuild(self, updates):
        """Rebuild the container with updated file contents.

        updates: dict of {container_path: new_bytes}
        Returns a new bytearray with the rebuilt container.
        """
        body = bytearray()
        for entry in self.entries:
            if entry.name in updates:
                content = updates[entry.name]
            else:
                content = entry.content
            name_bytes = entry.name.encode("ascii")
            body += struct.pack("<I", len(name_bytes))
            body += name_bytes
            body += struct.pack("<I", len(content))
            body += content
        body += self.dir_table

        header = bytearray(self.header)
        struct.pack_into("<I", header, 0, len(body) + 28)
        struct.pack_into("<I", header, 0x18, len(body))
        struct.pack_into("<I", header, 0x1C, len(self.entries))

        result = header + body
        if len(result) < CONTAINER_SIZE:
            result += b"\x00" * (CONTAINER_SIZE - len(result))
        elif len(result) > CONTAINER_SIZE:
            sys.exit(f"ERROR: rebuilt container too large ({len(result)} > {CONTAINER_SIZE})")
        return result


# --------------------------------------------------------------------------- #
# backup
# --------------------------------------------------------------------------- #
def backup_file(path, backups_dir=None):
    if backups_dir:
        os.makedirs(backups_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = os.path.basename(path)
        shutil.copy2(path, os.path.join(backups_dir, f"{stamp}_{name}"))
    else:
        shutil.copy2(path, path + ".bak")


# --------------------------------------------------------------------------- #
# export
# --------------------------------------------------------------------------- #
def cmd_export(binary_path, output_dir):
    if not os.path.isfile(binary_path):
        sys.exit(f"ERROR: no such file: {binary_path}")
    os.makedirs(output_dir, exist_ok=True)

    container = Container(binary_path)

    files = [
        (SAVE_FILE, "save_v3_0.json"),
        (SETTINGS_FILE, "settings.json"),
        (RUN_FILE, "run_v3_0.json"),
    ]

    for container_path, output_name in files:
        data = container.get_decompressed(container_path)
        if data is None:
            print(f"  WARNING: {container_path} not found in container")
            continue
        out_path = os.path.join(output_dir, output_name)
        with open(out_path, "wb") as f:
            f.write(data)
        print(f"  {container_path} -> {out_path} ({len(data)} bytes)")

    print(f"Exported to {output_dir}")


# --------------------------------------------------------------------------- #
# import
# --------------------------------------------------------------------------- #
def cmd_import(binary_path, json_dir, backups_dir=None):
    if not os.path.isfile(binary_path):
        sys.exit(f"ERROR: no such file: {binary_path}")

    container = Container(binary_path)

    # Read JSON files
    save_path = os.path.join(json_dir, "save_v3_0.json")
    settings_path = os.path.join(json_dir, "settings.json")
    run_path = os.path.join(json_dir, "run_v3_0.json")

    for p in [save_path, settings_path, run_path]:
        if not os.path.isfile(p):
            sys.exit(f"ERROR: no such file: {p}")

    with open(save_path, "rb") as f:
        save_json = f.read()
    with open(settings_path, "rb") as f:
        settings_json = f.read()
    with open(run_path, "rb") as f:
        run_json = f.read()

    # Preserve each entry's original compression style
    updates = {}
    for name, data in ((SAVE_FILE, save_json), (SETTINGS_FILE, settings_json), (RUN_FILE, run_json)):
        entry = container.find_entry(name)
        if entry is None:
            sys.exit(f"ERROR: {name} not found in container")
        if is_compressed(entry.content):
            data = compress_content(data)
        updates[name] = data

    # Rebuild container
    new_data = container.rebuild(updates)

    # Backup and write
    backup_file(binary_path, backups_dir)
    if backups_dir:
        print(f"Backup: {backups_dir}")

    with open(binary_path, "wb") as f:
        f.write(new_data)
    print(f"Updated {binary_path}")


def is_compressed(content):
    """True when content carries the container's compression sub-header."""
    return len(content) >= 8 and content[:4] == struct.pack("<I", COMPRESSION_MAGIC)


def compress_content(data):
    """Compress data with zlib and prepend the 8-byte sub-header."""
    compressed = zlib.compress(data)
    header = struct.pack("<II", COMPRESSION_MAGIC, len(data))
    return header + compressed


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("binary", help="path to Switch binary save file")
    common.add_argument("--backups", default=None, help="directory for timestamped backups")

    p = argparse.ArgumentParser(description="Brotato Switch binary save editor")
    sub = p.add_subparsers(dest="command", required=True)

    e = sub.add_parser("export", parents=[common])
    e.add_argument("output_dir", help="directory for exported JSON files")

    i = sub.add_parser("import", parents=[common])
    i.add_argument("json_dir", help="directory containing save_v3_0.json, settings.json, run_v3_0.json")

    args = p.parse_args()

    global BACKUPS_DIR
    BACKUPS_DIR = args.backups

    if args.command == "export":
        cmd_export(args.binary, args.output_dir)
    elif args.command == "import":
        cmd_import(args.binary, args.json_dir, args.backups)


if __name__ == "__main__":
    main()
