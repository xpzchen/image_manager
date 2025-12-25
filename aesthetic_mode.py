import os
import random
from pathlib import Path
from datetime import datetime
from typing import Iterable, List, Dict, Optional


def _is_image(ext: str, image_exts: Iterable[str]) -> bool:
    return ext.lower() in {e.lower() for e in image_exts}


def _is_raw(ext: str, raw_exts: Iterable[str]) -> bool:
    return ext.lower() in {e.lower() for e in raw_exts}


def _rel_parts(root: Path, p: Path) -> List[str]:
    try:
        rp = p.relative_to(root)
        return list(rp.parts)
    except Exception:
        return []


def scan_items(
    root_folder: str,
    image_exts: Iterable[str],
    raw_exts: Iterable[str],
    shuffle: bool = True,
    author: Optional[str] = None,
    work: Optional[str] = None,
) -> List[Dict]:
    """
    递归扫描作品用于"审美提升模式"。

    - 识别目录结构: .\\作者\\作品\\文件
    - 其它文件归为 loose
    - 仅收集图片扩展(含 RAW)
    - 可按 author/work 过滤
    """
    root = Path(root_folder).resolve()
    if not root.exists():
        return []

    items: List[Dict] = []
    image_set = {e.lower() for e in image_exts}
    raw_set = {e.lower() for e in raw_exts}

    for dirpath, dirnames, filenames in os.walk(root):
        # 忽略特殊目录，防止扫描到已标记或回收站的图片
        dirnames[:] = [d for d in dirnames if d not in {'_marked_images', '_trash'}]

        cur = Path(dirpath)
        for fname in filenames:
            ext = Path(fname).suffix.lower()
            if ext not in image_set:
                continue

            fpath = cur / fname
            try:
                stat = fpath.stat()
            except OSError:
                continue

            rel_parts = _rel_parts(root, fpath)

            author_name = ''
            work_name = ''
            category = 'loose'
            if len(rel_parts) >= 3:
                # 认为: 0=作者, 1=作品, 2+=文件
                author_name = rel_parts[0]
                work_name = rel_parts[1]
                category = 'archived'

            item_type = 'RAW' if ext in raw_set else 'JPG'

            # 过滤 author/work (如提供)
            if author and author_name != author:
                continue
            if work and work_name != work:
                continue

            items.append({
                'path': str(fpath),
                'rel_path': str(fpath.relative_to(root)),
                'ext': ext,
                'type': item_type,
                'size': stat.st_size,
                'mtime': stat.st_mtime,
                'date': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                'author': author_name,
                'work': work_name,
                'category': category,
                # 可由前端决定如何显示
                'name': fpath.name,
            })

    if shuffle:
        random.shuffle(items)
    else:
        items.sort(key=lambda x: x['mtime'], reverse=True)

    return items
