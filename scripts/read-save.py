#!/usr/bin/env python
"""
Skyrim Save File Reader (.ess)
Decompresses and scans Skyrim save files for FormIDs, strings, and byte patterns.

Usage:
    python scripts/read-save.py info <save.ess>
    python scripts/read-save.py search <save.ess> --string "ScriptName"
    python scripts/read-save.py search <save.ess> --formid 0x0001ABCD
    python scripts/read-save.py search <save.ess> --hex "DEADBEEF"
    python scripts/read-save.py plugins <save.ess>

Requires: lz4, pinned and hash-locked (run from the toolkit root):
    python -m pip install --only-binary=:all: --require-hashes -r tools/requirements-tools.lock
"""

import argparse
import json
import struct
import sys
import os

try:
    import lz4.block
except ImportError:
    print("Error: lz4 package required. Install the tested version from the toolkit root with: "
          "python -m pip install --only-binary=:all: --require-hashes -r tools/requirements-tools.lock", file=sys.stderr)
    sys.exit(1)


def read_save(path):
    """Read and return raw save file bytes."""
    with open(path, 'rb') as f:
        data = f.read()
    if not data.startswith(b'TESV_SAVEGAME'):
        raise ValueError('not a TESV save header')
    return data


class Matches(list):
    """Count all raw hits while retaining at most the displayed 50 contexts."""
    def __init__(self):
        super().__init__()
        self.total = 0

    def append(self, match):
        self.total += 1
        if len(self) < 50:
            super().append(match)


def decompress_save(data):
    """Find and decompress the LZ4 block in a .ess save file.

    SSE/VR saves use LZ4 compression. The compressed block is preceded by
    a (decompressedSize, compressedSize) uint32 pair where compressedSize
    matches the remaining bytes in the file.
    """
    for i in range(0, min(len(data) - 8, 200000)):
        decomp_size = struct.unpack_from('<I', data, i)[0]
        comp_size = struct.unpack_from('<I', data, i + 4)[0]
        remaining = len(data) - (i + 8)

        if 1_000_000 < decomp_size < 200_000_000 and abs(remaining - comp_size) < 1000:
            compressed = data[i + 8:i + 8 + comp_size]
            try:
                decompressed = lz4.block.decompress(compressed, uncompressed_size=decomp_size)
                return data[:i], decompressed, i
            except Exception:
                continue

    return data, None, -1


def read_header(data):
    """Parse the save file header (pre-compression)."""
    info = {}

    # Magic number check
    magic = data[:13]
    if magic[:12] == b'TESV_SAVEGAM':
        info['format'] = 'SSE/VR'
    else:
        info['format'] = 'unknown'

    # Header size at offset 13
    if len(data) > 17:
        header_size = struct.unpack_from('<I', data, 13)[0]
        info['headerSize'] = header_size

    # Version at offset 17
    if len(data) > 21:
        version = struct.unpack_from('<I', data, 17)[0]
        info['saveVersion'] = version

    # Player name - scan for it after the version fields
    # The format has: version(4) + saveNumber(4) + playerName(wstring)
    try:
        offset = 21
        save_num = struct.unpack_from('<I', data, offset)[0]
        info['saveNumber'] = save_num
        offset += 4

        # wstring: uint16 length followed by UTF-8 bytes
        name_len = struct.unpack_from('<H', data, offset)[0]
        offset += 2
        if 0 < name_len < 200:
            if offset + name_len > len(data):
                raise ValueError('truncated player name')
            player_name = data[offset:offset + name_len].decode('utf-8')
            info['playerName'] = player_name
            offset += name_len

            # Player level
            player_level = struct.unpack_from('<I', data, offset)[0]
            info['playerLevel'] = player_level
            offset += 4

            # Player location (wstring)
            loc_len = struct.unpack_from('<H', data, offset)[0]
            offset += 2
            if 0 < loc_len < 200:
                if offset + loc_len > len(data):
                    raise ValueError('truncated player location')
                location = data[offset:offset + loc_len].decode('utf-8')
                info['playerLocation'] = location
    except (struct.error, UnicodeDecodeError) as exc:
        raise ValueError('incomplete or malformed header fields') from exc

    return info


def read_plugins(data, decompressed):
    """Extract the plugin list from the save.

    The decompressed data starts with 5 bytes of preamble, then:
    - uint8 regular plugin count
    - N x (uint16 length + UTF-8 string) regular plugin names
    - uint16 light plugin count
    - N x (uint16 length + UTF-8 string) light plugin names
    """
    regular = []
    light = []
    if decompressed is None:
        raise ValueError('plugin table unavailable: decompression failed')

    try:
        # Regular plugins: count at byte 5, names start at byte 6
        offset = 5
        reg_count = decompressed[offset]
        offset += 1

        for _ in range(reg_count):
            name_len = struct.unpack_from('<H', decompressed, offset)[0]
            offset += 2
            if name_len > 500 or offset + name_len > len(decompressed):
                raise ValueError('regular plugin name is truncated or outside the supported table format')
            name = decompressed[offset:offset + name_len].decode('utf-8')
            regular.append(name)
            offset += name_len

        # Light plugins: uint16 count immediately after regular list
        light_count = struct.unpack_from('<H', decompressed, offset)[0]
        offset += 2

        for _ in range(light_count):
            name_len = struct.unpack_from('<H', decompressed, offset)[0]
            offset += 2
            if name_len > 500 or offset + name_len > len(decompressed):
                raise ValueError('light plugin name is truncated or outside the supported table format')
            name = decompressed[offset:offset + name_len].decode('utf-8')
            light.append(name)
            offset += name_len

    except (struct.error, IndexError, UnicodeDecodeError) as exc:
        raise ValueError('plugin table is incomplete or malformed') from exc

    return regular, light


def search_string(decompressed, query):
    """Search for a string in the decompressed save data."""
    results = Matches()
    query_bytes = query.encode('utf-8')
    start = 0

    while True:
        pos = decompressed.find(query_bytes, start)
        if pos == -1:
            break

        # Get context around the match
        ctx_start = max(0, pos - 32)
        ctx_end = min(len(decompressed), pos + len(query_bytes) + 32)
        context = decompressed[ctx_start:ctx_end]

        # Show printable ASCII context
        ascii_ctx = ''.join(chr(b) if 32 <= b < 127 else '.' for b in context)

        results.append({
            'offset': pos,
            'context': ascii_ctx,
            'hex': context.hex()
        })
        start = pos + 1

    return results


def search_formid(decompressed, formid):
    """Search for a FormID (as little-endian uint32) in the decompressed save."""
    results = Matches()
    needle = struct.pack('<I', formid)
    start = 0

    while True:
        pos = decompressed.find(needle, start)
        if pos == -1:
            break

        ctx_start = max(0, pos - 16)
        ctx_end = min(len(decompressed), pos + 20)
        context = decompressed[ctx_start:ctx_end]

        results.append({
            'offset': pos,
            'hex': context.hex()
        })
        start = pos + 1

    return results


def search_hex(decompressed, hex_string):
    """Search for a hex byte pattern in the decompressed save."""
    results = Matches()
    needle = bytes.fromhex(hex_string)
    if not needle:
        raise ValueError('hex search pattern cannot be empty')
    start = 0

    while True:
        pos = decompressed.find(needle, start)
        if pos == -1:
            break

        ctx_start = max(0, pos - 16)
        ctx_end = min(len(decompressed), pos + len(needle) + 16)
        context = decompressed[ctx_start:ctx_end]

        results.append({
            'offset': pos,
            'hex': context.hex()
        })
        start = pos + 1

    return results


def cmd_info(args):
    data = read_save(args.save)
    header_data, decompressed, comp_offset = decompress_save(data)

    info = read_header(data)
    info['filePath'] = os.path.abspath(args.save)
    info['fileSize'] = len(data)
    info['compressionOffset'] = comp_offset
    info['scope'] = 'selected header fields and plugin table; heuristic raw scanner'
    info['complete'] = decompressed is not None

    if decompressed is not None:
        info['decompressedSize'] = len(decompressed)
        regular, light = read_plugins(data, decompressed)
        info['regularPlugins'] = len(regular)
        info['lightPlugins'] = len(light)
        info['totalPlugins'] = len(regular) + len(light)
    else:
        info['decompressedSize'] = None
        info['totalPlugins'] = None
        info['error'] = 'Failed to decompress save'

    print(json.dumps(info, indent=2))
    return 2 if decompressed is None else 0


def cmd_plugins(args):
    data = read_save(args.save)
    _, decompressed, _ = decompress_save(data)

    if decompressed is None:
        print(json.dumps({'error': 'Failed to decompress save', 'complete': False}))
        return 2

    regular, light = read_plugins(data, decompressed)
    result = {
        'complete': True,
        'scope': 'plugin table in supported decompressed layout',
        'regularPlugins': len(regular),
        'lightPlugins': len(light),
        'totalPlugins': len(regular) + len(light),
        'regular': regular,
        'light': light
    }
    print(json.dumps(result, indent=2))
    return 0


def cmd_search(args):
    data = read_save(args.save)
    _, decompressed, _ = decompress_save(data)

    if decompressed is None:
        print(json.dumps({'error': 'Failed to decompress save', 'complete': False}))
        return 2

    if args.string:
        results = search_string(decompressed, args.string)
        print(json.dumps({
            'query': args.string,
            'type': 'string',
            'matchCount': results.total,
            'shown': len(results), 'truncated': results.total > len(results),
            'scope': 'raw bytes; hits are investigation leads',
            'matches': results[:50]  # cap output
        }, indent=2))

    elif args.formid:
        fid = int(args.formid, 16) if args.formid.startswith('0x') else int(args.formid, 16)
        results = search_formid(decompressed, fid)
        print(json.dumps({
            'query': hex(fid),
            'type': 'formid',
            'matchCount': results.total,
            'shown': len(results), 'truncated': results.total > len(results),
            'scope': 'raw bytes; hits are investigation leads',
            'matches': results[:50]
        }, indent=2))

    elif args.hex:
        results = search_hex(decompressed, args.hex)
        print(json.dumps({
            'query': args.hex,
            'type': 'hex',
            'matchCount': results.total,
            'shown': len(results), 'truncated': results.total > len(results),
            'scope': 'raw bytes; hits are investigation leads',
            'matches': results[:50]
        }, indent=2))

    else:
        print('Error: specify --string, --formid, or --hex', file=sys.stderr)
        return 2
    return 0


def main():
    parser = argparse.ArgumentParser(description='Skyrim Save File Reader (.ess)')
    subparsers = parser.add_subparsers(dest='command', required=True)

    # info
    p_info = subparsers.add_parser('info', help='Show save file info (player, level, file size)')
    p_info.add_argument('save', help='Path to .ess save file')

    # plugins
    p_plugins = subparsers.add_parser('plugins', help='List all plugins in the save')
    p_plugins.add_argument('save', help='Path to .ess save file')

    # search
    p_search = subparsers.add_parser('search', help='Search for strings, FormIDs, or hex patterns')
    p_search.add_argument('save', help='Path to .ess save file')
    p_search.add_argument('--string', '-s', help='Search for a UTF-8 string')
    p_search.add_argument('--formid', '-f', help='Search for a FormID (hex, e.g. 0x0001ABCD)')
    p_search.add_argument('--hex', '-x', help='Search for a hex byte pattern (e.g. DEADBEEF)')

    args = parser.parse_args()

    try:
        if args.command == 'info':
            return cmd_info(args)
        elif args.command == 'plugins':
            return cmd_plugins(args)
        elif args.command == 'search':
            return cmd_search(args)
    except (OSError, ValueError, struct.error) as exc:
        print(json.dumps({'error': str(exc), 'complete': False}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
