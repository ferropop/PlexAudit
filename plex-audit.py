#!/usr/bin/env python3
"""
Plex Library Audit Tool
Compares what Plex has scanned vs what files actually exist on disk.
Outputs a self-contained interactive HTML report.
ＦＥＲＲＯ  
"""

import sqlite3
import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

DEFAULT_DB_PATHS = [
    r"C:\Users\{user}\AppData\Local\Plex Media Server\Plug-in Support\Databases\com.plexapp.plugins.library.db",
    r"C:\Users\{user}\AppData\Roaming\Plex Media Server\Plug-in Support\Databases\com.plexapp.plugins.library.db",
    r"C:\ProgramData\Plex Media Server\Plug-in Support\Databases\com.plexapp.plugins.library.db",
]

MEDIA_EXTENSIONS = {
    '.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v', '.mpg', '.mpeg',
    '.ts', '.m2ts', '.flv', '.webm', '.divx', '.xvid', '.h264', '.hevc',
    '.mp3', '.flac', '.aac', '.m4a', '.ogg', '.wav', '.wma', '.opus',
    '.jpg', '.jpeg', '.png', '.gif', '.tiff', '.bmp',
}

PLEX_TYPE_MOVIE   = 1
PLEX_TYPE_SHOW    = 2
PLEX_TYPE_SEASON  = 3
PLEX_TYPE_EPISODE = 4
PLEX_TYPE_ARTIST  = 8
PLEX_TYPE_ALBUM   = 9
PLEX_TYPE_TRACK   = 10

def find_plex_db():
    username = os.environ.get('USERNAME', os.environ.get('USER', 'user'))
    for template in DEFAULT_DB_PATHS:
        path = template.replace('{user}', username)
        if os.path.exists(path):
            return path
    return None

def read_plex_library(db_path):
    if not os.path.exists(db_path):
        print(f"ERROR: Database not found at:\n  {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    items = []

    # `index` is a reserved word in SQLite — must be quoted with backticks
    try:
        cur.execute("""
            SELECT
                mp.file                      AS file_path,
                mp.size                      AS file_size,
                meta.id                      AS meta_id,
                meta.title                   AS title,
                meta.`index`                 AS ep_index,
                meta.parent_id               AS parent_id,
                meta.metadata_type           AS media_type,
                meta.added_at                AS added_at,
                ls.name                      AS library_name,
                ls.section_type              AS library_type,
                mi.width                     AS width,
                mi.height                    AS height,
                mi.bitrate                   AS bitrate,
                mi.duration                  AS duration_ms
            FROM media_parts mp
            JOIN media_items mi        ON mp.media_item_id      = mi.id
            JOIN metadata_items meta   ON mi.metadata_item_id   = meta.id
            LEFT JOIN library_sections ls ON meta.library_section_id = ls.id
            WHERE mp.file IS NOT NULL
            ORDER BY ls.name, meta.title
        """)
        rows = cur.fetchall()

        # Build hierarchy lookup — also quote `index` here
        cur.execute("""
            SELECT id, title, `index` AS idx, parent_id, metadata_type
            FROM metadata_items
        """)
        meta_map = {}
        for row in cur.fetchall():
            d = dict(row)
            # normalise key name so we always use 'idx'
            meta_map[d['id']] = d

        for row in rows:
            item = dict(row)
            mt = item.get('media_type')

            show_title = season_number = ep_number = ep_title = None

            if mt == PLEX_TYPE_EPISODE:
                # TV: episode → season → show
                ep_title  = item.get('title')
                ep_number = item.get('ep_index')
                season_row = meta_map.get(item.get('parent_id'))
                if season_row:
                    season_number = season_row.get('idx')
                    show_row = meta_map.get(season_row.get('parent_id'))
                    if show_row:
                        show_title = show_row.get('title')

            elif mt == PLEX_TYPE_TRACK:
                # Music: track → album → artist
                # reuse show_title=artist, season_number=None, ep_number=track#, ep_title=track title
                ep_title  = item.get('title')           # track title
                ep_number = item.get('ep_index')        # track number
                album_row = meta_map.get(item.get('parent_id'))
                if album_row:
                    season_number = None                # no season equivalent for music
                    # show_title = "Artist — Album"
                    artist_row = meta_map.get(album_row.get('parent_id'))
                    artist_name = artist_row.get('title') if artist_row else None
                    album_name  = album_row.get('title')
                    if artist_name and album_name:
                        show_title = f"{artist_name} — {album_name}"
                    elif album_name:
                        show_title = album_name
                    elif artist_name:
                        show_title = artist_name

            item['show_title']    = show_title
            item['season_number'] = season_number
            item['ep_number']     = ep_number
            item['ep_title']      = ep_title
            items.append(item)

    except sqlite3.OperationalError as e:
        print(f"ERROR in DB query: {e}", file=sys.stderr)
        print("Trying bracket-quoted fallback...", file=sys.stderr)
        items.clear()
        # Some SQLite builds prefer [square brackets] for reserved words
        try:
            cur.execute("""
                SELECT
                    mp.file                      AS file_path,
                    mp.size                      AS file_size,
                    meta.id                      AS meta_id,
                    meta.title                   AS title,
                    meta.[index]                 AS ep_index,
                    meta.parent_id               AS parent_id,
                    meta.metadata_type           AS media_type,
                    meta.added_at                AS added_at,
                    ls.name                      AS library_name,
                    ls.section_type              AS library_type,
                    mi.width                     AS width,
                    mi.height                    AS height,
                    mi.bitrate                   AS bitrate,
                    mi.duration                  AS duration_ms
                FROM media_parts mp
                JOIN media_items mi        ON mp.media_item_id      = mi.id
                JOIN metadata_items meta   ON mi.metadata_item_id   = meta.id
                LEFT JOIN library_sections ls ON meta.library_section_id = ls.id
                WHERE mp.file IS NOT NULL
                ORDER BY ls.name, meta.title
            """)
            rows = cur.fetchall()
            cur.execute("SELECT id, title, [index] AS idx, parent_id, metadata_type FROM metadata_items")
            meta_map = {r['id']: dict(r) for r in cur.fetchall()}

            for row in rows:
                item = dict(row)
                mt = item.get('media_type')
                show_title = season_number = ep_number = ep_title = None
                if mt == PLEX_TYPE_EPISODE:
                    ep_title  = item.get('title')
                    ep_number = item.get('ep_index')
                    season_row = meta_map.get(item.get('parent_id'))
                    if season_row:
                        season_number = season_row.get('idx')
                        show_row = meta_map.get(season_row.get('parent_id'))
                        if show_row:
                            show_title = show_row.get('title')
                elif mt == PLEX_TYPE_TRACK:
                    ep_title  = item.get('title')
                    ep_number = item.get('ep_index')
                    album_row = meta_map.get(item.get('parent_id'))
                    if album_row:
                        artist_row = meta_map.get(album_row.get('parent_id'))
                        artist_name = artist_row.get('title') if artist_row else None
                        album_name  = album_row.get('title')
                        if artist_name and album_name:
                            show_title = f"{artist_name} — {album_name}"
                        elif album_name:
                            show_title = album_name
                        elif artist_name:
                            show_title = artist_name
                item['show_title']    = show_title
                item['season_number'] = season_number
                item['ep_number']     = ep_number
                item['ep_title']      = ep_title
                items.append(item)
            print(f"Bracket fallback succeeded: {len(items)} rows", file=sys.stderr)

        except sqlite3.OperationalError as e2:
            print(f"Bracket fallback also failed ({e2}), using file-paths only.", file=sys.stderr)
            items.clear()
            cur.execute("SELECT file FROM media_parts WHERE file IS NOT NULL")
            for row in cur.fetchall():
                items.append({
                    'file_path': row['file'], 'title': None, 'media_type': None,
                    'added_at': None, 'library_name': 'Unknown', 'library_type': None,
                    'show_title': None, 'season_number': None,
                    'ep_number': None, 'ep_title': None,
                })
    finally:
        conn.close()

    return items

def walk_scan_dirs(scan_dirs):
    found_files = {}
    for scan_dir in scan_dirs:
        scan_dir = Path(scan_dir)
        if not scan_dir.exists():
            print(f"WARNING: Scan directory not found: {scan_dir}", file=sys.stderr)
            continue
        for root, dirs, files in os.walk(scan_dir):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for fname in files:
                ext = Path(fname).suffix.lower()
                if ext in MEDIA_EXTENSIONS:
                    full_path = Path(root) / fname
                    key = str(full_path).replace('\\', '/').lower()
                    found_files[key] = {
                        'path': str(full_path),
                        'size_bytes': full_path.stat().st_size,
                        'extension': ext,
                    }
    return found_files

def normalize_path(p):
    """Normalize a path for comparison: lowercase, forward slashes, strip trailing whitespace."""
    if not p:
        return ''
    # Strip leading/trailing whitespace (Plex DB sometimes has these)
    p = p.strip()
    # Normalize to forward slashes and lowercase
    return p.replace('\\', '/').lower()

def cross_reference(plex_items, disk_files, debug=False):
    matched   = []
    unmatched = []
    db_missing = []
    disk_only  = []

    # Build normalized plex path map
    plex_path_map = {}
    for item in plex_items:
        raw = item.get('file_path', '') or ''
        key = normalize_path(raw)
        if key:
            plex_path_map[key] = item

    if debug:
        print("\n── DEBUG: Path samples ──────────────────────────────────", file=sys.stderr)
        plex_samples = list(plex_path_map.keys())[:5]
        disk_samples = list(disk_files.keys())[:5]
        print("Plex DB paths (normalized):", file=sys.stderr)
        for s in plex_samples:
            print(f"  {s!r}", file=sys.stderr)
        print("Disk paths (normalized):", file=sys.stderr)
        for s in disk_samples:
            print(f"  {s!r}", file=sys.stderr)
        # Check if first plex path exists in disk_files
        if plex_samples and disk_samples:
            test = plex_samples[0]
            print(f"\nTest lookup of first plex path in disk dict: {'HIT' if test in disk_files else 'MISS'}", file=sys.stderr)
            # Show character-level diff if miss
            if test not in disk_files:
                best = min(disk_samples, key=lambda d: sum(a!=b for a,b in zip(d,test)))
                print(f"Closest disk path: {best!r}", file=sys.stderr)
                for i,(a,b) in enumerate(zip(test,best)):
                    if a!=b:
                        print(f"  First diff at char {i}: plex={a!r} disk={b!r}", file=sys.stderr)
                        break
        print("─────────────────────────────────────────────────────────\n", file=sys.stderr)

    disk_keys_seen = set()

    for plex_key, plex_item in plex_path_map.items():
        disk_info = disk_files.get(plex_key)
        has_meta = bool(
            plex_item.get('title') or
            plex_item.get('show_title') or
            plex_item.get('ep_title')
        )
        if disk_info:
            disk_keys_seen.add(plex_key)
            (matched if has_meta else unmatched).append(
                {'plex': plex_item, 'disk': disk_info,
                 'status': 'matched' if has_meta else 'unmatched'})
        else:
            # Fallback: check if file literally exists on disk (handles case mismatches
            # on case-sensitive filesystems, or path recorded differently in DB)
            actual = plex_item.get('file_path', '') or ''
            actual = actual.strip()
            if actual and os.path.exists(actual):
                disk_keys_seen.add(plex_key)
                stub = {'path': actual, 'size_bytes': None,
                        'extension': Path(actual).suffix.lower()}
                (matched if has_meta else unmatched).append(
                    {'plex': plex_item, 'disk': stub,
                     'status': 'matched' if has_meta else 'unmatched'})
            else:
                db_missing.append({'plex': plex_item, 'disk': None, 'status': 'db_missing'})

    for disk_key, disk_info in disk_files.items():
        if disk_key not in disk_keys_seen:
            disk_only.append({'plex': None, 'disk': disk_info, 'status': 'disk_only'})

    if debug:
        print(f"Cross-ref result: matched={len(matched)} unmatched={len(unmatched)} "
              f"db_missing={len(db_missing)} disk_only={len(disk_only)}", file=sys.stderr)

    return matched, unmatched, db_missing, disk_only

def fmt_date(ts):
    if not ts:
        return ''
    try:
        return datetime.fromtimestamp(int(ts)).strftime('%Y-%m-%d')
    except:
        return str(ts)

def fmt_resolution(w, h):
    if not w or not h:
        return ''
    # Label by height
    labels = {2160:'4K', 1080:'1080p', 720:'720p', 576:'576p', 480:'480p'}
    label = labels.get(h) or f'{h}p'
    return f'{label} ({w}×{h})'

def fmt_bitrate(bps):
    """Plex stores bitrate in bits-per-second in media_items.bitrate."""
    if not bps:
        return ''
    mbps = bps / 1_000_000
    if mbps >= 1:
        return f'{mbps:.1f} Mbps'
    kbps = bps / 1_000
    return f'{kbps:.0f} kbps'

def fmt_size(b):
    if not b:
        return ''
    for unit in ['B','KB','MB','GB','TB']:
        if b < 1024:
            return f'{b:.1f} {unit}'
        b /= 1024
    return f'{b:.1f} PB'

def fmt_duration(ms):
    if not ms:
        return ''
    s = int(ms) // 1000
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f'{h}h {m:02d}m'
    return f'{m}m {sec:02d}s'

def build_html_report(matched, unmatched, db_missing, disk_only, db_path, scan_dirs, generated_at):

    def make_row(entry):
        p = entry.get('plex') or {}
        d = entry.get('disk') or {}
        status = entry['status']
        mt = p.get('media_type')

        file_path = p.get('file_path') or d.get('path') or ''
        file_path = file_path.replace('\\', '/')   # normalise to forward slashes for JS
        filename  = file_path.split('/')[-1]
        folder    = '/'.join(file_path.split('/')[:-1])
        ext       = (d.get('extension') or os.path.splitext(file_path)[1]).lower()

        lib = (p.get('library_name') or '').lower()
        path_lower = file_path.lower().replace('\\', '/')

        if mt == PLEX_TYPE_EPISODE:
            content_type = 'tv'
        elif mt == PLEX_TYPE_MOVIE:
            content_type = 'movie'
        elif mt == PLEX_TYPE_TRACK:
            content_type = 'music'
        elif 'tv' in lib or 'show' in lib or 'episode' in lib or 'anime' in lib:
            content_type = 'tv'
        elif 'music' in lib or 'audio' in lib:
            content_type = 'music'
        elif 'movie' in lib or 'film' in lib or 'cinema' in lib:
            content_type = 'movie'
        else:
            # No library info (disk_only) — guess from path segments
            parts = set(path_lower.replace('\\','/').split('/'))
            tv_hints    = {'tv', 'tv shows', 'television', 'series', 'episodes',
                           'anime', 'shows', 'season', 'seasons'}
            music_hints = {'music', 'audio', 'albums', 'artists', 'flac', 'mp3'}
            movie_hints = {'movies', 'movie', 'films', 'film', 'cinema', 'featurettes'}
            if parts & tv_hints:
                content_type = 'tv'
            elif parts & music_hints:
                content_type = 'music'
            elif parts & movie_hints:
                content_type = 'movie'
            else:
                content_type = 'unknown'

        return {
            'status':       status,
            'content_type': content_type,
            'filename':     filename,
            'folder':       folder,
            'full_path':    file_path,
            'ext':          ext or '—',
            'library':      p.get('library_name') or ('Extras/Featurettes' if p else '—'),
            'movie_title':  p.get('title') or '',
            'show_title':   p.get('show_title') or '',
            'season':       str(p.get('season_number')) if p.get('season_number') is not None else '',
            'ep_number':    str(p.get('ep_number')) if p.get('ep_number') is not None else '',
            'ep_title':     p.get('ep_title') or p.get('title') or '',
            'added':        fmt_date(p.get('added_at')),
            # Quality fields (from media_items + media_parts)
            'width':        p.get('width') or 0,
            'height':       p.get('height') or 0,
            'bitrate':      p.get('bitrate') or 0,
            'duration_ms':  p.get('duration_ms') or 0,
            'file_size':    d.get('size_bytes') or p.get('file_size') or 0,
            # Derived display strings (computed once here, not in JS)
            'q_res':        fmt_resolution(p.get('width'), p.get('height')),
            'q_bitrate':    fmt_bitrate(p.get('bitrate')),
            'q_size':       fmt_size(d.get('size_bytes') or p.get('file_size')),
            'q_duration':   fmt_duration(p.get('duration_ms')),
        }

    all_rows = (
        [make_row(e) for e in matched] +
        [make_row(e) for e in unmatched] +
        [make_row(e) for e in db_missing] +
        [make_row(e) for e in disk_only]
    )

    libraries = sorted(set(r['library'] for r in all_rows if r['library'] not in ('—', '')))
    stats = {
        'matched':    len(matched),
        'unmatched':  len(unmatched),
        'db_missing': len(db_missing),
        'disk_only':  len(disk_only),
        'total':      len(all_rows),
    }
    # Collect all extensions present in the data, grouped by category
    VIDEO_EXTS  = {'.mkv','.mp4','.avi','.mov','.wmv','.m4v','.mpg','.mpeg',
                   '.ts','.m2ts','.flv','.webm','.divx','.xvid','.h264','.hevc'}
    AUDIO_EXTS  = {'.mp3','.flac','.aac','.m4a','.ogg','.wav','.wma','.opus','.alac'}
    IMAGE_EXTS  = {'.jpg','.jpeg','.png','.gif','.tiff','.tif','.bmp','.webp'}

    all_exts = sorted(set(r['ext'] for r in all_rows if r['ext'] and r['ext'] != '—'))

    def ext_cat(e):
        if e in VIDEO_EXTS:  return 'video'
        if e in AUDIO_EXTS:  return 'audio'
        if e in IMAGE_EXTS:  return 'image'
        return 'other'

    ext_groups_js = json.dumps([{'ext': e, 'cat': ext_cat(e)} for e in all_exts])

    scan_dirs_html = ' | '.join(str(d) for d in scan_dirs)

    # ── HTML ────────────────────────────────────────────────────────────────
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Plex Audit — {generated_at}</title>
<style>
:root {{
  --bg:    #0d0f13; --bg2: #13151b; --bg3: #1a1d26; --bg4: #20232e;
  --bdr:   #252836; --bdr2:#30344a;
  --tx:    #e2e4ee; --tx2: #7c82a0; --tx3: #454960;
  --green: #3ecf8e; --yel: #f5c842; --red: #e5534b; --blue: #4c8bf5;
  --font-m:'JetBrains Mono','Cascadia Code','Fira Code',Consolas,monospace;
  --font-s:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  --r:7px;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--tx);font-family:var(--font-m);font-size:12.5px}}

.header{{position:sticky;top:0;z-index:200;background:var(--bg2);border-bottom:1px solid var(--bdr);padding:14px 22px 12px}}
.h1{{display:flex;align-items:center;gap:12px;margin-bottom:12px}}
.logo{{font-size:17px;font-weight:700;letter-spacing:1px}}.logo span{{color:#e5a020}}
.dbinfo{{font-size:10px;color:var(--tx3);margin-left:auto;font-family:var(--font-s);max-width:500px;text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}

.view-tabs{{display:flex;gap:5px;margin-left:auto}}
.vtab{{background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--r);color:var(--tx3);cursor:pointer;font-family:var(--font-s);font-size:11px;font-weight:600;letter-spacing:.4px;padding:4px 11px;transition:all .15s;text-transform:uppercase}}
.vtab.active{{background:var(--bg4);border-color:var(--bdr2);color:var(--tx)}}
.vtab:hover{{color:var(--tx2)}}

.stats{{display:flex;gap:9px;margin-bottom:12px;flex-wrap:wrap}}
.stat{{background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--r);padding:9px 14px;cursor:pointer;transition:border-color .15s,background .15s;min-width:100px}}
.stat:hover,.stat.active{{background:var(--bg4);border-color:var(--bdr2)}}
.stat.active{{border-color:var(--c,var(--bdr2))}}
.stat-n{{font-size:20px;font-weight:700;line-height:1;color:var(--c,var(--tx))}}
.stat-label{{font-size:10px;color:var(--tx2);margin-top:3px;font-family:var(--font-s);text-transform:uppercase;letter-spacing:.4px}}
.s-all{{--c:var(--tx)}}.s-ok{{--c:var(--green)}}.s-unm{{--c:var(--yel)}}.s-miss{{--c:var(--red)}}.s-new{{--c:var(--blue)}}

.filters{{display:flex;gap:9px;flex-wrap:wrap;align-items:center}}
.flabel{{font-size:10px;color:var(--tx3);font-family:var(--font-s);text-transform:uppercase;letter-spacing:.4px;white-space:nowrap}}
select,input[type=text]{{background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--r);color:var(--tx);font-family:var(--font-m);font-size:12px;padding:5px 9px;outline:none;transition:border-color .15s}}
select:focus,input[type=text]:focus{{border-color:var(--bdr2)}}
input[type=text]{{width:190px}}
.btn{{background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--r);color:var(--tx2);cursor:pointer;font-family:var(--font-m);font-size:12px;padding:5px 11px;transition:all .15s}}
.btn:hover{{border-color:var(--bdr2);color:var(--tx)}}

/* Extension pills */
.ext-row{{display:flex;flex-wrap:wrap;gap:5px;align-items:center;padding:8px 0 2px}}
.ext-group{{display:flex;flex-wrap:wrap;gap:4px;align-items:center}}
.ext-sep{{width:1px;height:16px;background:var(--bdr2);margin:0 4px;flex-shrink:0}}
.ext-pill{{
  cursor:pointer;border-radius:4px;padding:2px 7px;
  font-family:var(--font-m);font-size:11px;font-weight:500;
  border:1px solid transparent;transition:all .12s;
  user-select:none;white-space:nowrap;
}}
/* ON state per category */
.ext-pill.on.video  {{background:rgba(76,139,245,.15); border-color:rgba(76,139,245,.4);  color:#6ea8f7}}
.ext-pill.on.audio  {{background:rgba(62,207,142,.15); border-color:rgba(62,207,142,.4);  color:#5dd9a4}}
.ext-pill.on.image  {{background:rgba(245,200,66,.15); border-color:rgba(245,200,66,.4);  color:#f5c842}}
.ext-pill.on.other  {{background:rgba(160,120,220,.15);border-color:rgba(160,120,220,.4); color:#b07fe0}}
/* OFF state — all categories same muted look */
.ext-pill.off{{background:transparent;border-color:var(--bdr);color:var(--tx3)}}
.ext-pill.off:hover{{border-color:var(--bdr2);color:var(--tx2)}}
.ext-cat-btn{{
  cursor:pointer;font-size:10px;color:var(--tx2);font-family:var(--font-s);
  text-transform:uppercase;letter-spacing:.4px;white-space:nowrap;
  padding:2px 6px;border-radius:4px;border:1px solid var(--bdr);
  background:var(--bg3);transition:all .12s;user-select:none;
  display:inline-flex;align-items:center;gap:4px;
}}
.ext-cat-btn:hover{{border-color:var(--bdr2);color:var(--tx)}}
.ext-toggle-all{{font-size:10px;color:var(--tx3);font-family:var(--font-s);cursor:pointer;text-decoration:underline;white-space:nowrap;margin-left:4px}}
.ext-toggle-all:hover{{color:var(--tx2)}}

.main{{padding:14px 22px 48px}}
.result-bar{{display:flex;justify-content:space-between;align-items:center;margin-bottom:9px;font-size:11px;color:var(--tx3);font-family:var(--font-s)}}
.legend{{display:flex;gap:14px}}
.leg{{display:flex;align-items:center;gap:5px;font-size:11px;color:var(--tx2)}}

/* ── Two-table sticky header layout ──
   #tbl-wrap: outer horizontal scroll container (clips left/right)
   #thead-scroll: header table, no overflow, scrolls horizontally in sync
   #tbody-scroll: body scroll container, overflow-x synced, overflow-y auto
   Both tables share identical colgroup widths kept in sync by JS.           */
#tbl-wrap{{
  border:1px solid var(--bdr);
  border-radius:var(--r);
  overflow:hidden;         /* clips the rounded corners */
}}
#thead-scroll{{
  overflow:hidden;         /* header never shows its own scrollbar */
  background:var(--bg2);
  border-bottom:1px solid var(--bdr);
}}
#tbody-scroll{{
  overflow-x:auto;
  overflow-y:auto;
  max-height:calc(100vh - 280px);
  min-height:120px;
}}
/* Both tables identical layout */
.tbl{{
  width:max-content;min-width:100%;
  border-collapse:collapse;
  table-layout:fixed;
}}
col{{overflow:hidden}}
th{{
  background:var(--bg2);
  color:var(--tx3);font-family:var(--font-s);font-size:10px;font-weight:600;
  letter-spacing:.5px;padding:8px 10px 7px;text-align:left;
  text-transform:uppercase;user-select:none;white-space:nowrap;
  position:relative;overflow:hidden;
}}
th.sortable{{cursor:pointer}}
th.sortable:hover{{color:var(--tx2)}}
th.sorted{{color:var(--yel)}}
th.sorted::after{{content:' ↕'}}
th.drag-over{{background:var(--bg4);outline:2px solid var(--yel);outline-offset:-2px}}

.rh{{position:absolute;right:0;top:0;bottom:0;width:6px;cursor:col-resize;z-index:10;background:transparent}}
.rh:hover,.rh.drag{{background:var(--bdr2)}}

#col-ghost{{
  position:fixed;pointer-events:none;z-index:9999;
  background:var(--bg4);border:1px solid var(--yel);border-radius:var(--r);
  padding:5px 12px;font-family:var(--font-s);font-size:11px;font-weight:600;
  color:var(--yel);letter-spacing:.5px;text-transform:uppercase;
  opacity:.92;white-space:nowrap;
}}

tbody tr{{border-bottom:1px solid var(--bdr);transition:background .08s}}
tbody tr:last-child{{border-bottom:none}}
tbody tr:hover{{background:var(--bg3)}}
tr.unmatched {{background:rgba(245,200,66,.03)}}
tr.db_missing{{background:rgba(229,83,75,.03)}}
tr.disk_only {{background:rgba(76,139,245,.03)}}

/* max-width:0 forces content to respect col width — no cell can push wider */
td{{padding:7px 10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--tx2);vertical-align:middle;max-width:0}}
td.p{{color:var(--tx);font-weight:500}}
td.g{{color:var(--green)}}
td.y{{color:var(--yel);font-style:italic}}
td.f{{color:var(--tx3);font-size:11px}}

.dot{{display:inline-block;width:8px;height:8px;border-radius:50%}}
.dot.matched   {{background:var(--green);box-shadow:0 0 5px var(--green)}}
.dot.unmatched {{background:var(--yel);box-shadow:0 0 5px var(--yel)}}
.dot.db_missing{{background:var(--red);box-shadow:0 0 5px var(--red)}}
.dot.disk_only {{background:var(--blue);box-shadow:0 0 5px var(--blue)}}

.empty{{text-align:center;padding:50px;color:var(--tx3);font-family:var(--font-s)}}
::-webkit-scrollbar{{width:6px;height:6px}}
::-webkit-scrollbar-track{{background:var(--bg)}}
::-webkit-scrollbar-thumb{{background:var(--bdr2);border-radius:3px}}

/* ── Loading overlay ── */
#loading-overlay{{
  position:fixed;inset:0;z-index:9999;
  background:var(--bg);
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  gap:18px;transition:opacity .35s;
}}
#loading-overlay.fade{{opacity:0;pointer-events:none}}
.ld-logo{{font-size:22px;font-weight:700;letter-spacing:1px;color:var(--tx2)}}
.ld-logo span{{color:#e5a020}}
.ld-msg{{font-size:13px;color:var(--tx2);font-family:var(--font-s);min-height:20px}}
.ld-bar-wrap{{width:280px;height:4px;background:var(--bdr);border-radius:2px;overflow:hidden}}
.ld-bar{{height:100%;width:0%;background:var(--green);border-radius:2px;transition:width .15s}}

/* ── Filter status bar ── */
#filter-bar{{
  height:3px;background:transparent;
  position:sticky;top:0;z-index:199;
  transition:background .1s;
  overflow:hidden;
}}
#filter-bar-fill{{
  height:100%;width:0%;
  background:linear-gradient(90deg,var(--green),#3ecf8e88);
  transition:width .12s;
  border-radius:0 2px 2px 0;
}}
#filter-bar.busy #filter-bar-fill{{
  animation:filterPulse 0.6s ease-in-out infinite alternate;
}}
@keyframes filterPulse{{
  from{{width:30%;opacity:1}}
  to{{width:85%;opacity:.6}}
}}
#ctx-menu{{
  position:fixed;z-index:9000;display:none;
  background:var(--bg3);border:1px solid var(--bdr2);border-radius:var(--r);
  padding:4px 0;min-width:220px;
  box-shadow:0 8px 24px rgba(0,0,0,.5);
  font-family:var(--font-s);font-size:13px;
}}
.ctx-item{{
  padding:7px 14px;cursor:pointer;color:var(--tx2);
  display:flex;align-items:center;gap:9px;
  transition:background .1s,color .1s;
}}
.ctx-item:hover{{background:var(--bg4);color:var(--tx)}}
.ctx-sep{{height:1px;background:var(--bdr);margin:3px 0}}
.ctx-path{{
  padding:6px 14px 8px;font-size:10px;color:var(--tx3);
  font-family:var(--font-m);overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap;max-width:340px;
}}

/* ── Toast ── */
#toast{{
  position:fixed;bottom:28px;left:50%;transform:translateX(-50%) translateY(20px);
  background:var(--bg4);border:1px solid var(--bdr2);border-radius:var(--r);
  padding:8px 18px;font-family:var(--font-s);font-size:13px;color:var(--tx);
  pointer-events:none;opacity:0;transition:opacity .2s,transform .2s;z-index:9999;
  white-space:nowrap;
}}
#toast.show{{opacity:1;transform:translateX(-50%) translateY(0)}}
</style>
</head>
<body>
<div id="loading-overlay">
  <div class="ld-logo">PLEX<span>AUDIT</span></div>
  <div class="ld-bar-wrap"><div class="ld-bar" id="ld-bar"></div></div>
  <div class="ld-msg" id="ld-msg">Parsing {stats['total']:,} entries…</div>
</div>
<div id="filter-bar"><div id="filter-bar-fill"></div></div>
<div class="header">
  <div class="h1">
    <div class="logo">PLEX<span>AUDIT</span></div>
    <div class="view-tabs">
      <div class="vtab"        onclick="setView('tv')"    id="vtab-tv">TV Shows</div>
      <div class="vtab"        onclick="setView('movie')" id="vtab-movie">Movies</div>
      <div class="vtab"        onclick="setView('music')" id="vtab-music">Music</div>
      <div class="vtab active" onclick="setView('all')"   id="vtab-all">All</div>
    </div>
    <div class="dbinfo">{generated_at} &nbsp;·&nbsp; {scan_dirs_html}</div>
  </div>
  <div class="stats">
    <div class="stat s-all active" onclick="filterStatus('all')"        id="btn-all">
      <div class="stat-n" id="cnt-all">{stats['total']}</div><div class="stat-label">Total</div></div>
    <div class="stat s-ok"  onclick="filterStatus('matched')"    id="btn-matched">
      <div class="stat-n" id="cnt-matched">{stats['matched']}</div><div class="stat-label">Matched</div></div>
    <div class="stat s-unm" onclick="filterStatus('unmatched')"  id="btn-unmatched">
      <div class="stat-n" id="cnt-unmatched">{stats['unmatched']}</div><div class="stat-label">No metadata</div></div>
    <div class="stat s-miss" onclick="filterStatus('db_missing')" id="btn-db_missing">
      <div class="stat-n" id="cnt-db_missing">{stats['db_missing']}</div><div class="stat-label">File missing</div></div>
    <div class="stat s-new" onclick="filterStatus('disk_only')"  id="btn-disk_only">
      <div class="stat-n" id="cnt-disk_only">{stats['disk_only']}</div><div class="stat-label">Not in Plex</div></div>
  </div>
  <div class="filters">
    <span class="flabel">Library</span>
    <select id="lib-filter" onchange="applyFilters()">
      <option value="">All libraries</option>
      {"".join(f'<option value="{l}">{l}</option>' for l in libraries)}
    </select>
    <span class="flabel">Search</span>
    <input type="text" id="search" placeholder="filename, show, episode…" oninput="applyFilters()">
    <button class="btn" onclick="resetFilters()">Reset</button>
    <button class="btn" onclick="exportCSV()">Export CSV</button>
    <button class="btn" id="btn-quality" onclick="toggleQuality()" style="margin-left:auto">Quality columns: OFF</button>
  </div>
  <div class="ext-row" id="ext-row"></div>
</div>

<div class="main">
  <div class="result-bar">
    <span id="result-count"></span>
    <div class="legend">
      <div class="leg"><span class="dot matched"></span>Matched</div>
      <div class="leg"><span class="dot unmatched"></span>Scanned, no metadata</div>
      <div class="leg"><span class="dot db_missing"></span>File missing</div>
      <div class="leg"><span class="dot disk_only"></span>Not in Plex</div>
    </div>
  </div>
  <div id="tbl-wrap">
    <div id="thead-scroll">
      <table class="tbl" id="tbl-head">
        <colgroup id="col-group-head"></colgroup>
        <thead><tr id="col-headers"></tr></thead>
      </table>
    </div>
    <div id="tbody-scroll">
      <table class="tbl" id="tbl-body">
        <colgroup id="col-group-body"></colgroup>
        <tbody id="table-body"></tbody>
      </table>
    </div>
  </div>
  <div id="col-ghost" style="display:none"></div>
</div>

<div id="ctx-menu">
  <div class="ctx-path" id="ctx-path"></div>
  <div class="ctx-sep"></div>
  <div class="ctx-item" onclick="ctxCopyPath()">⎘&nbsp; Copy full path</div>
  <div class="ctx-item" onclick="ctxCopyFolder()">📁&nbsp; Copy folder path</div>
  <div class="ctx-item" onclick="ctxOpenExplorer()">🗂&nbsp; Copy "Open in Explorer" command <span style="font-size:10px;color:var(--tx3);margin-left:auto">Win+R</span></div>
</div>
<div id="toast"></div>

<script>
const RAW={json.dumps(all_rows, ensure_ascii=False)};
const EXT_GROUPS={ext_groups_js};

// ── Extension filter state ────────────────────────────────────────────────────
// null = all on (default). Set = only these extensions visible.
let extFilter = null; // null means "show all"

function buildExtPills() {{
  const row = document.getElementById('ext-row');
  if (!EXT_GROUPS.length) {{ row.style.display='none'; return; }}

  const cats = ['video','audio','image','other'];
  const catLabel = {{video:'Video',audio:'Audio',image:'Image',other:'Other'}};
  const bycat = {{}};
  cats.forEach(c=>bycat[c]=[]);
  EXT_GROUPS.forEach(e=>bycat[e.cat].push(e.ext));

  let html = '<span class="ext-cat-label" style="margin-right:6px">Ext</span>';

  cats.forEach((cat, ci) => {{
    const exts = bycat[cat];
    if (!exts.length) return;
    if (ci > 0) html += '<div class="ext-sep"></div>';
    // Category heading is itself a toggle button
    html += `<span class="ext-cat-btn" data-cat="${{cat}}" onclick="toggleCat('${{cat}}')"><span class="ext-cat-indicator" id="cat-ind-${{cat}}">▼</span> ${{catLabel[cat]}}</span>`;
    html += '<div class="ext-group">';
    exts.forEach(ext => {{
      html += `<span class="ext-pill on ${{cat}}" data-ext="${{ext}}" onclick="toggleExt('${{ext}}')">${{ext}}</span>`;
    }});
    html += '</div>';
  }});

  html += '<span class="ext-toggle-all" onclick="setAllExts(true)">all on</span>';
  html += '<span class="ext-toggle-all" onclick="setAllExts(false)">all off</span>';

  row.innerHTML = html;
  syncPillUI();
}}

function getCatExts(cat) {{
  return EXT_GROUPS.filter(e=>e.cat===cat).map(e=>e.ext);
}}

function toggleCat(cat) {{
  const exts = getCatExts(cat);
  // Clearance logic: if ANY in this cat are on → turn ALL off. Only if ALL are off → turn all on.
  const anyOn = exts.some(e => extFilter===null || extFilter.has(e));
  if (extFilter === null) {{
    // All globally on — switch to explicit set with this category removed
    extFilter = new Set(EXT_GROUPS.map(e=>e.ext));
    exts.forEach(e => extFilter.delete(e));
  }} else if (anyOn) {{
    // Some/all on → clear them all (clearance)
    exts.forEach(e => extFilter.delete(e));
  }} else {{
    // All off → turn on
    exts.forEach(e => extFilter.add(e));
    if (extFilter.size === EXT_GROUPS.length) extFilter = null;
  }}
  syncPillUI();
  applyFilters();
}}

function syncPillUI() {{
  document.querySelectorAll('.ext-pill').forEach(pill => {{
    const ext = pill.dataset.ext;
    const on  = extFilter === null || extFilter.has(ext);
    pill.classList.toggle('on',  on);
    pill.classList.toggle('off', !on);
  }});
  // Update category indicator: ▼ = all on, ▶ = mixed, ✕ = all off
  const cats = ['video','audio','image','other'];
  cats.forEach(cat => {{
    const el = document.getElementById('cat-ind-'+cat);
    if (!el) return;
    const exts = getCatExts(cat);
    if (!exts.length) return;
    const onCount = exts.filter(e => extFilter===null||extFilter.has(e)).length;
    if (onCount === exts.length) {{ el.textContent='▼'; el.style.color=''; }}
    else if (onCount === 0)      {{ el.textContent='✕'; el.style.color='var(--red)'; }}
    else                         {{ el.textContent='◈'; el.style.color='var(--yel)'; }}
  }});
}}

// #4: When switching views, auto-set extension filters to the relevant category
const VIEW_EXT_CATS = {{
  tv:    ['video'],
  movie: ['video'],
  music: ['audio'],
  all:   null,   // null = all on
}};

function setViewExts(v) {{
  const cats = VIEW_EXT_CATS[v];
  if (cats === null) {{
    extFilter = null;
  }} else {{
    extFilter = new Set();
    cats.forEach(cat => getCatExts(cat).forEach(e => extFilter.add(e)));
  }}
  syncPillUI();
}}

const SO={{matched:0,unmatched:1,db_missing:2,disk_only:3}};
let view='all', statusFilter='all', sortCol='status', sortAsc=true, filtered=[];

// meta cell renderer: green if has value, yellow-italic if unmatched placeholder, faint dash otherwise
function mCell(r, field) {{
  const v = r[field];
  const hasV = v !== null && v !== undefined && v !== '';
  if (hasV) return `<td class="g" title="${{xe(v)}}">${{xe(v)}}</td>`;
  if (r.status==='unmatched') return `<td class="y">—</td>`;
  return `<td class="f">—</td>`;
}}

// Column definitions
// fnCell: filename cell with right-click context menu
const fnCell = (r,w) => `<td class="p" title="${{xe(r.full_path)}}" oncontextmenu="showCtx(event,'${{xe(r.full_path)}}')" style="cursor:context-menu">${{xe(r.filename)}}</td>`;

const COLS = {{
  tv:[
    {{id:'_dot',      w:28,  th:'',             row:r=>`<td style="text-align:center"><span class="dot ${{r.status}}"></span></td>`}},
    {{id:'folder',    w:300, th:'Folder',        row:r=>`<td class="f" title="${{xe(r.folder)}}">${{xe(r.folder)}}</td>`}},
    {{id:'show_title',w:180, th:'Show',          row:r=>mCell(r,'show_title')}},
    {{id:'filename',  w:250, th:'Filename',      row:r=>fnCell(r)}},
    {{id:'season',    w:48,  th:'S',             row:r=>mCell(r,'season')}},
    {{id:'ep_number', w:48,  th:'E',             row:r=>mCell(r,'ep_number')}},
    {{id:'ep_title',  w:220, th:'Episode title', row:r=>mCell(r,'ep_title')}},
    {{id:'library',   w:130, th:'Library',       row:r=>`<td class="f">${{xe(r.library)}}</td>`}},
  ],
  movie:[
    {{id:'_dot',        w:28,  th:'',           row:r=>`<td style="text-align:center"><span class="dot ${{r.status}}"></span></td>`}},
    {{id:'folder',      w:310, th:'Folder',      row:r=>`<td class="f" title="${{xe(r.folder)}}">${{xe(r.folder)}}</td>`}},
    {{id:'filename',    w:280, th:'Filename',    row:r=>fnCell(r)}},
    {{id:'movie_title', w:280, th:'Plex title',  row:r=>mCell(r,'movie_title')}},
    {{id:'library',     w:130, th:'Library',     row:r=>`<td class="f">${{xe(r.library)}}</td>`}},
  ],
  music:[
    {{id:'_dot',       w:28,  th:'',              row:r=>`<td style="text-align:center"><span class="dot ${{r.status}}"></span></td>`}},
    {{id:'folder',     w:260, th:'Folder',        row:r=>`<td class="f" title="${{xe(r.folder)}}">${{xe(r.folder)}}</td>`}},
    {{id:'show_title', w:220, th:'Artist — Album',row:r=>mCell(r,'show_title')}},
    {{id:'filename',   w:240, th:'Filename',      row:r=>fnCell(r)}},
    {{id:'ep_number',  w:48,  th:'#',             row:r=>mCell(r,'ep_number')}},
    {{id:'ep_title',   w:220, th:'Track title',   row:r=>mCell(r,'ep_title')}},
    {{id:'library',    w:130, th:'Library',       row:r=>`<td class="f">${{xe(r.library)}}</td>`}},
  ],
  all:[
    {{id:'_dot',       w:28,  th:'', row:r=>`<td style="text-align:center"><span class="dot ${{r.status}}"></span></td>`}},
    {{id:'content_type',w:62, th:'Type', row:r=>{{
      const b={{tv:'<span style="color:#6ea8f7;font-size:10px;font-weight:600">TV</span>',
               movie:'<span style="color:#b07fe0;font-size:10px;font-weight:600">MOVIE</span>',
               music:'<span style="color:#5dd9a4;font-size:10px;font-weight:600">MUSIC</span>',
               unknown:'<span style="color:var(--tx3);font-size:10px">?</span>'}};
      return `<td style="text-align:center">${{b[r.content_type]||b.unknown}}</td>`;
    }}}},
    {{id:'folder',     w:240, th:'Folder',       row:r=>`<td class="f" title="${{xe(r.folder)}}">${{xe(r.folder)}}</td>`}},
    {{id:'filename',   w:230, th:'Filename',     row:r=>fnCell(r)}},
    {{id:'plex_title', w:200, th:'Show / Movie', row:r=>{{
      if (r.content_type==='movie') return mCell(r,'movie_title');
      if (r.content_type==='music') return mCell(r,'show_title');
      if (r.content_type==='tv')    return mCell(r,'show_title');
      const v=r.movie_title||r.show_title||'';
      return v?`<td class="g" title="${{xe(v)}}">${{xe(v)}}</td>`:`<td class="f">—</td>`;
    }}}},
    {{id:'season',     w:44,  th:'S',  row:r=>r.content_type==='tv'?mCell(r,'season'):`<td class="f">—</td>`}},
    {{id:'ep_number',  w:44,  th:'E/#',row:r=>r.content_type==='movie'?`<td class="f">—</td>`:mCell(r,'ep_number')}},
    {{id:'ep_title',   w:200, th:'Episode / Track', row:r=>r.content_type==='movie'?`<td class="f">—</td>`:mCell(r,'ep_title')}},
    {{id:'library',    w:120, th:'Library',      row:r=>`<td class="f">${{xe(r.library)}}</td>`}},
  ],
}};

// ── Quality columns (appended to any view when toggled on) ────────────────────
const QUALITY_COLS = [
  {{id:'q_res',      w:120, th:'Resolution', row:r=>r.q_res    ?`<td class="f">${{xe(r.q_res)}}</td>`   :`<td class="f">—</td>`}},
  {{id:'q_bitrate',  w:90,  th:'Bitrate',    row:r=>r.q_bitrate?`<td class="f">${{xe(r.q_bitrate)}}</td>`:`<td class="f">—</td>`}},
  {{id:'q_size',     w:80,  th:'File size',  row:r=>r.q_size   ?`<td class="f">${{xe(r.q_size)}}</td>`  :`<td class="f">—</td>`}},
  {{id:'q_duration', w:80,  th:'Duration',   row:r=>r.q_duration?`<td class="f">${{xe(r.q_duration)}}</td>`:`<td class="f">—</td>`}},
];

let qualityOn = false;
function toggleQuality() {{
  qualityOn = !qualityOn;
  const btn = document.getElementById('btn-quality');
  btn.textContent = `Quality columns: ${{qualityOn ? 'ON' : 'OFF'}}`;
  btn.style.borderColor = qualityOn ? 'var(--green)' : '';
  btn.style.color       = qualityOn ? 'var(--green)' : '';
  // Reset column order so quality cols don't get stuck in a stale position
  colOrder[view] = null;
  buildHeaders();
  render();
}}

// ── Column order (indices into COLS[view]) ────────────────────────────────────
const colOrder = {{ tv:null, movie:null, music:null, all:null }};
function getOrder(v) {{
  if (!colOrder[v]) colOrder[v] = COLS[v].map((_,i)=>i);
  return colOrder[v];
}}
function orderedCols() {{
  const base = getOrder(view).map(i=>COLS[view][i]);
  return qualityOn ? [...base, ...QUALITY_COLS] : base;
}}

// ── Resize via <col> elements ─────────────────────────────────────────────────
// We drive width exclusively through <col> elements; td has max-width:0 so it
// can never push the column wider than the col says.
let rsz=null, rsxStart=0, rswStart=0, rszJustFinished=false;

function startRsz(e, colHead, colBody, orderIdx) {{
  e.stopPropagation(); e.preventDefault();
  rsz = {{ colHead, colBody, orderIdx }};
  rsxStart = e.clientX;
  rswStart = parseInt(colHead.style.width) || 100;
  rszJustFinished = false;
  document.body.style.cursor = 'col-resize';
  e.target.classList.add('drag');
  document.addEventListener('mousemove', onRszMove);
  document.addEventListener('mouseup',   onRszEnd);
}}
function onRszMove(e) {{
  if (!rsz) return;
  const nw = Math.max(20, rswStart + (e.clientX - rsxStart));
  rsz.colHead.style.width = nw + 'px';
  rsz.colBody.style.width = nw + 'px';
  const realIdx = getOrder(view)[rsz.orderIdx];
  COLS[view][realIdx].w = nw;
}}
function onRszEnd(e) {{
  if (!rsz) return;
  document.querySelectorAll('.rh.drag').forEach(el=>el.classList.remove('drag'));
  document.body.style.cursor = '';
  rsz = null;
  rszJustFinished = true;          // flag: swallow the next click on this th
  setTimeout(()=>{{ rszJustFinished=false; }}, 50);
  document.removeEventListener('mousemove', onRszMove);
  document.removeEventListener('mouseup',   onRszEnd);
}}

// ── Drag-to-reorder ───────────────────────────────────────────────────────────
let dnd = null; // {{ fromIdx, ghost }}

function startDrag(e, orderIdx) {{
  if (e.target.classList.contains('rh')) return; // let resize win
  e.preventDefault();
  const ghost = document.getElementById('col-ghost');
  ghost.textContent = orderedCols()[orderIdx].th || '·';
  ghost.style.display = 'block';
  ghost.style.left = (e.clientX + 14) + 'px';
  ghost.style.top  = (e.clientY - 10) + 'px';
  dnd = {{ fromIdx: orderIdx }};
  document.addEventListener('mousemove', onDragMove);
  document.addEventListener('mouseup',   onDragEnd);
}}
function onDragMove(e) {{
  if (!dnd) return;
  const ghost = document.getElementById('col-ghost');
  ghost.style.left = (e.clientX + 14) + 'px';
  ghost.style.top  = (e.clientY - 10) + 'px';
  // highlight target
  document.querySelectorAll('th.drag-over').forEach(t=>t.classList.remove('drag-over'));
  const el = document.elementFromPoint(e.clientX, e.clientY);
  const th = el && el.closest('th[data-order-idx]');
  if (th && parseInt(th.dataset.orderIdx) !== dnd.fromIdx) {{
    th.classList.add('drag-over');
  }}
}}
function onDragEnd(e) {{
  if (!dnd) return;
  document.getElementById('col-ghost').style.display = 'none';
  document.querySelectorAll('th.drag-over').forEach(t=>t.classList.remove('drag-over'));
  const el = document.elementFromPoint(e.clientX, e.clientY);
  const th = el && el.closest('th[data-order-idx]');
  if (th) {{
    const toIdx = parseInt(th.dataset.orderIdx);
    if (toIdx !== dnd.fromIdx) {{
      const ord = getOrder(view);
      const [moved] = ord.splice(dnd.fromIdx, 1);
      ord.splice(toIdx, 0, moved);
      buildHeaders();
      render();
    }}
  }}
  document.removeEventListener('mousemove', onDragMove);
  document.removeEventListener('mouseup',   onDragEnd);
  dnd = null;
}}

// ── Sync horizontal scroll between header and body ────────────────────────────
function initScrollSync() {{
  const bodyScroll = document.getElementById('tbody-scroll');
  const headScroll = document.getElementById('thead-scroll');
  bodyScroll.addEventListener('scroll', () => {{
    headScroll.scrollLeft = bodyScroll.scrollLeft;
  }});
}}

// ── Build headers + both colgroups ────────────────────────────────────────────
function buildHeaders() {{
  const cols = orderedCols();
  const cgHead = document.getElementById('col-group-head');
  const cgBody = document.getElementById('col-group-body');
  const tr     = document.getElementById('col-headers');
  cgHead.innerHTML = '';
  cgBody.innerHTML = '';
  tr.innerHTML = '';

  cols.forEach((col, orderIdx) => {{
    // One <col> in each colgroup — kept in sync by updateColWidths()
    const colHead = document.createElement('col');
    const colBody = document.createElement('col');
    colHead.style.width = colBody.style.width = col.w + 'px';
    colHead.dataset.orderIdx = colBody.dataset.orderIdx = orderIdx;
    cgHead.appendChild(colHead);
    cgBody.appendChild(colBody);

    // <th>
    const th = document.createElement('th');
    th.dataset.col = col.id;
    th.dataset.orderIdx = orderIdx;

    if (col.id !== '_dot') {{
      th.classList.add('sortable');
      if (sortCol === col.id) th.classList.add('sorted');
      th.addEventListener('click', (e) => {{
        if (dnd || rszJustFinished) return;
        sortBy(col.id);
      }});
      th.addEventListener('mousedown', (e) => {{
        if (!e.target.classList.contains('rh')) startDrag(e, orderIdx);
      }});
    }}

    th.textContent = col.th;

    const rh = document.createElement('div');
    rh.className = 'rh';
    rh.addEventListener('mousedown', e => startRsz(e, colHead, colBody, orderIdx));
    th.appendChild(rh);
    tr.appendChild(th);
  }});
}}

function toggleExt(ext) {{
  if (extFilter === null) {{
    extFilter = new Set(EXT_GROUPS.map(e=>e.ext));
    extFilter.delete(ext);
  }} else {{
    if (extFilter.has(ext)) extFilter.delete(ext);
    else extFilter.add(ext);
    if (extFilter.size === EXT_GROUPS.length) extFilter = null;
  }}
  syncPillUI();
  applyFilters();
}}

function setAllExts(on) {{
  extFilter = on ? null : new Set();
  syncPillUI();
  applyFilters();
}}

// ── View ──────────────────────────────────────────────────────────────────────
function setView(v){{
  view=v;
  document.querySelectorAll('.vtab').forEach(t=>t.classList.remove('active'));
  document.getElementById('vtab-'+v).classList.add('active');
  sortCol='status';sortAsc=true;
  colOrder[view]=null;
  setViewExts(v);
  buildHeaders();applyFilters();
}}

// ── Filter / sort ─────────────────────────────────────────────────────────────
function filterStatus(s){{
  statusFilter=s;
  document.querySelectorAll('.stat').forEach(el=>el.classList.remove('active'));
  document.getElementById('btn-'+(s==='all'?'all':s)).classList.add('active');
  applyFilters();
}}

function applyFilters(){{
  setFilterBar(true);
  const lib=document.getElementById('lib-filter').value;
  const q=document.getElementById('search').value.toLowerCase();
  const viewMatch=r=>view==='all'||r.content_type===view||r.content_type==='unknown';
  const extMatch=r=>extFilter===null||extFilter.has(r.ext);

  const base=RAW.filter(r=>{{
    if(!viewMatch(r))return false;
    if(!extMatch(r))return false;
    if(lib&&r.library!==lib)return false;
    if(q){{const h=(r.filename+r.folder+r.show_title+r.movie_title+r.ep_title).toLowerCase();if(!h.includes(q))return false;}}
    return true;
  }});
  document.getElementById('cnt-all').textContent       =base.length;
  document.getElementById('cnt-matched').textContent   =base.filter(r=>r.status==='matched').length;
  document.getElementById('cnt-unmatched').textContent =base.filter(r=>r.status==='unmatched').length;
  document.getElementById('cnt-db_missing').textContent=base.filter(r=>r.status==='db_missing').length;
  document.getElementById('cnt-disk_only').textContent =base.filter(r=>r.status==='disk_only').length;

  filtered=base.filter(r=>statusFilter==='all'||r.status===statusFilter);
  sortData();render();
}}

function sortBy(col){{
  if(sortCol===col)sortAsc=!sortAsc;else{{sortCol=col;sortAsc=true;}}
  buildHeaders();sortData();render();
}}

function sortData(){{
  filtered.sort((a,b)=>{{
    let va=a[sortCol]??'',vb=b[sortCol]??'';
    if(sortCol==='status'){{va=SO[va]??9;vb=SO[vb]??9;}}
    if(sortCol==='ep_number'||sortCol==='season'){{va=parseInt(va)||0;vb=parseInt(vb)||0;}}
    const primary = va<vb ? (sortAsc?-1:1) : va>vb ? (sortAsc?1:-1) : 0;
    if(primary!==0) return primary;
    // Secondary sort: when sorting by folder, sort filenames within each folder
    if(sortCol==='folder'){{
      const fa=(a.filename||'').toLowerCase(), fb=(b.filename||'').toLowerCase();
      return fa<fb?-1:fa>fb?1:0;
    }}
    return 0;
  }});
}}

// ── Chunked render ────────────────────────────────────────────────────────────
const CHUNK = 200;   // rows per idle-callback batch
let renderGeneration = 0;  // incremented on every new render call to cancel stale ones

function render() {{
  const tbody = document.getElementById('table-body');
  const cols  = orderedCols();
  const total = RAW.filter(r => view==='all' || r.content_type===view || r.content_type==='unknown').length;
  document.getElementById('result-count').textContent =
    filtered.length + ' of ' + total.toLocaleString() + ' entries';

  // Cancel any in-flight render from a previous filter
  renderGeneration++;
  const gen = renderGeneration;

  tbody.innerHTML = '';

  if (!filtered.length) {{
    tbody.innerHTML = `<tr><td colspan="${{cols.length}}"><div class="empty">No entries match.</div></td></tr>`;
    setFilterBar(false);
    return;
  }}

  // Build the first chunk synchronously so the table isn't blank
  const firstChunk = filtered.slice(0, CHUNK);
  tbody.innerHTML = rowsHtml(firstChunk, cols);

  if (filtered.length <= CHUNK) {{
    setFilterBar(false);
    return;
  }}

  // Schedule remaining chunks during idle time
  let offset = CHUNK;
  function appendChunk(deadline) {{
    if (gen !== renderGeneration) return;  // stale — a new render started
    while (offset < filtered.length && (deadline.timeRemaining() > 2 || deadline.didTimeout)) {{
      const chunk = filtered.slice(offset, offset + CHUNK);
      tbody.insertAdjacentHTML('beforeend', rowsHtml(chunk, cols));
      offset += CHUNK;
    }}
    if (offset < filtered.length) {{
      requestIdleCallback(appendChunk, {{timeout: 300}});
    }} else {{
      setFilterBar(false);
    }}
  }}
  requestIdleCallback(appendChunk, {{timeout: 300}});
}}

function rowsHtml(rows, cols) {{
  return rows.map(r =>
    `<tr class="${{r.status}}">${{cols.map(c => c.row(r)).join('')}}</tr>`
  ).join('');
}}

function setFilterBar(busy) {{
  const bar  = document.getElementById('filter-bar');
  const fill = document.getElementById('filter-bar-fill');
  if (busy) {{
    bar.classList.add('busy');
    fill.style.width = '';
  }} else {{
    bar.classList.remove('busy');
    fill.style.width = '100%';
    setTimeout(() => {{ fill.style.width = '0%'; }}, 400);
  }}
}}

function xe(s){{
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}

function resetFilters(){{
  document.getElementById('lib-filter').value='';
  document.getElementById('search').value='';
  statusFilter='all';
  setViewExts(view);
  document.querySelectorAll('.stat').forEach(el=>el.classList.remove('active'));
  document.getElementById('btn-all').classList.add('active');
  applyFilters();
}}

function exportCSV(){{
  const cols=orderedCols().filter(c=>c.id!=='_dot');
  const fields=['status',...cols.map(c=>c.id),'full_path'];
  const header=fields.join(',');
  const rows=filtered.map(r=>fields.map(f=>'"'+(r[f]||'').toString().replace(/"/g,'""')+'"').join(','));
  const blob=new Blob([header+'\\n'+rows.join('\\n')],{{type:'text/csv'}});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download='plex_audit_{generated_at.replace(" ","_").replace(":","")}.csv';
  a.click();
}}

// ── Context menu ──────────────────────────────────────────────────────────────
let ctxCurrentPath = '';
const ctxMenu = () => document.getElementById('ctx-menu');

function showCtx(e, fullPath) {{
  e.preventDefault();
  e.stopPropagation();
  ctxCurrentPath = fullPath;
  const m = ctxMenu();
  document.getElementById('ctx-path').textContent = fullPath;
  m.style.display = 'block';
  const vw = window.innerWidth, vh = window.innerHeight;
  let x = e.clientX, y = e.clientY;
  if (x + 240 > vw) x = vw - 244;
  if (y + 150 > vh) y = vh - 154;
  m.style.left = x + 'px';
  m.style.top  = y + 'px';
}}
function hideCtx() {{ ctxMenu().style.display = 'none'; }}
document.addEventListener('click', hideCtx);
document.addEventListener('keydown', e => {{ if(e.key==='Escape') hideCtx(); }});

function showToast(msg) {{
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2400);
}}

function ctxCopyPath() {{
  navigator.clipboard.writeText(ctxCurrentPath)
    .then(() => showToast('✓ Full path copied'));
  hideCtx();
}}
function ctxCopyFolder() {{
  const folder = ctxCurrentPath.replace(/[/\\\\][^/\\\\]+$/, '');
  navigator.clipboard.writeText(folder)
    .then(() => showToast('✓ Folder path copied'));
  hideCtx();
}}
function ctxOpenExplorer() {{
  // Convert forward slashes to backslashes for Windows Explorer command
  let p = ctxCurrentPath;
  // Replace each / with a backslash using char-by-char loop
  let winPath = '';
  for (let i = 0; i < p.length; i++) {{
    winPath += (p[i] === '/') ? '\\\\' : p[i];
  }}
  const cmd = 'explorer /select,"' + winPath + '"';
  navigator.clipboard.writeText(cmd)
    .then(() => showToast('\u2713 Command copied \u2014 paste into Win+R or Start search'));
  hideCtx();
}}

// ── Boot with loading overlay ─────────────────────────────────────────────────
function setLoadProgress(pct, msg) {{
  document.getElementById('ld-bar').style.width = pct + '%';
  if (msg) document.getElementById('ld-msg').textContent = msg;
}}
function hideOverlay() {{
  const ov = document.getElementById('loading-overlay');
  ov.classList.add('fade');
  setTimeout(() => ov.remove(), 400);
}}

// Defer heavy work so the overlay paints first
requestAnimationFrame(() => {{
  requestAnimationFrame(() => {{
    setLoadProgress(20, 'Building columns\u2026');
    initScrollSync();

    setLoadProgress(35, 'Building extension filters\u2026');
    buildExtPills();
    setViewExts(view);

    setLoadProgress(55, 'Building headers\u2026');
    buildHeaders();

    setLoadProgress(70, 'Filtering ' + RAW.length.toLocaleString() + ' entries\u2026');

    // Let the browser paint the progress bar before the heavy filter pass
    setTimeout(() => {{
      const lib = document.getElementById('lib-filter').value;
      const q   = document.getElementById('search').value.toLowerCase();
      const viewMatch = r => view==='all' || r.content_type===view || r.content_type==='unknown';
      const extMatch  = r => extFilter===null || extFilter.has(r.ext);
      const base = RAW.filter(r => viewMatch(r) && extMatch(r));
      document.getElementById('cnt-all').textContent        = base.length;
      document.getElementById('cnt-matched').textContent    = base.filter(r=>r.status==='matched').length;
      document.getElementById('cnt-unmatched').textContent  = base.filter(r=>r.status==='unmatched').length;
      document.getElementById('cnt-db_missing').textContent = base.filter(r=>r.status==='db_missing').length;
      document.getElementById('cnt-disk_only').textContent  = base.filter(r=>r.status==='disk_only').length;
      filtered = base.slice();
      sortData();

      setLoadProgress(88, 'Rendering first rows\u2026');

      setTimeout(() => {{
        render();
        setLoadProgress(100, 'Done');
        setTimeout(hideOverlay, 250);
      }}, 16);
    }}, 16);
  }});
}});
</script>
</body>
</html>"""

def main():
    parser = argparse.ArgumentParser(
        description="Plex Library Audit — compare DB vs disk files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python plex_audit.py --scan "D:\\Movies" "E:\\TV Shows"
  python plex_audit.py --db "C:\\path\\to\\library.db" --scan "D:\\Media" --out report.html
        """
    )
    parser.add_argument('--db', help='Path to Plex SQLite database (auto-detected if omitted)')
    parser.add_argument('--scan', nargs='+', required=True, help='Directories to scan for media files')
    parser.add_argument('--out', default='plex_audit_report.html', help='Output HTML file')
    parser.add_argument('--debug', action='store_true', help='Print path samples to diagnose matching issues')
    args = parser.parse_args()

    db_path = args.db
    if not db_path:
        db_path = find_plex_db()
        if db_path:
            print(f"Auto-detected Plex DB: {db_path}")
        else:
            print("ERROR: Could not auto-detect Plex database. Use --db to specify the path.", file=sys.stderr)
            sys.exit(1)

    print("Reading Plex database...")
    plex_items = read_plex_library(db_path)
    print(f"  → {len(plex_items)} media entries in DB")

    print("Scanning disk directories...")
    disk_files = walk_scan_dirs(args.scan)
    print(f"  → {len(disk_files)} media files on disk")
    if args.debug:
        for scan_dir in args.scan:
            count = sum(1 for k in disk_files if normalize_path(k).startswith(normalize_path(str(scan_dir))))
            print(f"     {scan_dir}: {count} files", file=sys.stderr)

    print("Cross-referencing...")
    matched, unmatched, db_missing, disk_only = cross_reference(plex_items, disk_files, debug=args.debug)
    print(f"  Matched:             {len(matched)}")
    print(f"  Scanned, no match:   {len(unmatched)}")
    print(f"  In DB, file missing: {len(db_missing)}")
    print(f"  On disk, not in DB:  {len(disk_only)}")

    generated_at = datetime.now().strftime('%Y-%m-%d %H:%M')
    html = build_html_report(matched, unmatched, db_missing, disk_only, db_path, args.scan, generated_at)

    with open(args.out, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"\n✓ Report saved to: {args.out}")

if __name__ == '__main__':
    main()
