# brotato-tools

Tools for inspecting and editing Brotato save files.

## bin/brotato_switch_save.py

Exports and imports the JSON save files stored inside a Switch binary save
container used by the modded Nintendo Switch version of Brotato.

Requires Python 3 (standard library only).

### Usage

```
python bin/brotato_switch_save.py <command> [options] binary ...
```

```
usage: brotato_switch_save.py [-h] {export,import} ...

Brotato Switch binary save editor

positional arguments:
  {export,import}

options:
  -h, --help       show this help message and exit
```

### export

```
usage: brotato_switch_save.py export [-h] [--backups BACKUPS] binary output_dir
```

Reads the Switch binary save `binary` and writes the JSON save files to
`output_dir` (created if it does not exist). Compressed entries are
decompressed automatically; entries missing from the container are skipped
with a warning.

| Argument | Description |
| --- | --- |
| `binary` | Path to the Switch binary save file. |
| `output_dir` | Directory the JSON files are written to. |

| Container path | Output file |
| --- | --- |
| `./user/save_v3_0.json` | `save_v3_0.json` |
| `./user/settings.json` | `settings.json` |
| `./user/run_v3_0.json` | `run_v3_0.json` |

Example:

```
python bin/brotato_switch_save.py export save.dat exported
```

```
  ./user/save_v3_0.json -> exported/save_v3_0.json (68066 bytes)
  ./user/settings.json -> exported/settings.json (1979 bytes)
  ./user/run_v3_0.json -> exported/run_v3_0.json (45 bytes)
Exported to exported
```

### import

```
usage: brotato_switch_save.py import [-h] [--backups BACKUPS] binary json_dir
```

Reads `save_v3_0.json`, `settings.json`, and `run_v3_0.json` from `json_dir`,
compresses them with the same compression style as the original entries,
rebuilds the container, and writes it back to `binary`. The rebuilt container
is zero-padded to 1,024,000 bytes.

| Argument | Description |
| --- | --- |
| `binary` | Path to the Switch binary save file. |
| `json_dir` | Directory containing the three JSON files. |

All three files must be present. A backup of `binary` is created before it is
overwritten:

- `--backups DIR` - timestamped copy `DIR/YYYYMMDD_HHMMSS_<basename>` (the
  directory is created if it does not exist)
- default - `<binary>.bak` next to the save file

`--backups` is accepted by `export` as well, but only used by `import`.

If the rebuilt container would exceed 1,024,000 bytes, the command exits with
`ERROR: rebuilt container too large (<size> > 1024000)` and the save file is
left untouched.

Example:

```
python bin/brotato_switch_save.py import save.dat exported --backups backups
```

```
Backup: backups
Updated save.dat
```

### Typical workflow

```
python bin/brotato_switch_save.py export save.dat exported
# edit exported/save_v3_0.json (or settings.json / run_v3_0.json)
python bin/brotato_switch_save.py import save.dat exported --backups backups
```

### Container format

The Switch save container is a fixed-size file of 1,024,000 bytes, zero-padded
at the end. All integers are little-endian.

32-byte header:

| Offset | Size | Field |
| --- | --- | --- |
| 0x00 | u32 | `data_size` - offset of the directory table's `content_len` field |
| 0x04 | u32 | `version` |
| 0x08 | u32 * 3 | `reserved` |
| 0x14 | u32 | `unknown` - opaque, carried over unchanged |
| 0x18 | u32 | `body_size` - size of the file entries + directory table |
| 0x1C | u32 | `entry_count` - number of file entries |

After the header come the file entries, then the directory table:

- File entry: `u32 name_len` + name + `u32 content_len` + content
- Directory table: `u32 count` + `count * (u32 name_len + name + u32 content_len=0)`

Compressed content carries an 8-byte sub-header: `u32 0x05FE04FB`
(compression magic), `u32 uncompressed_size`, then zlib-compressed data.

### Files inside the container

| Container path | Description |
| --- | --- |
| `./save.json` | 12-line format (for brotato_save_editor.py) |
| `./user/save_v2.json` | Windows v2 format (zlib compressed) |
| `./user/save_v3_0.json` | Windows v3 format (zlib compressed) |
| `./user/settings.json` | Game settings (zlib compressed) |
| `./user/run_v3_0.json` | Current run state (zlib compressed) |

