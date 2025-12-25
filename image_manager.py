#!/usr/bin/env python3
"""
图片管理工具 - 轻量级浏览器端图片管理
支持整理、审阅、标记、删除和恢复功能
"""

import os
import sys
import shutil
import json
import time
import platform
import glob
import random
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_from_directory, send_file
from PIL import Image, ImageOps, ImageDraw
import threading
import webbrowser
try:
    import pillow_avif  # 导入后会自动注册解码器
except ImportError:
    print("Warning: pillow_avif module not found. AVIF images may not load.")

from pillow_heif import register_heif_opener
import mimetypes
from functools import lru_cache
import hashlib
import logging
import urllib.parse

register_heif_opener() # 注册 HEIF/HEIC 支持

# 审美提升模式模块
try:
    from aesthetic_mode import scan_items as aesthetic_scan_items
except Exception:
    aesthetic_scan_items = None

# 尝试导入 rawpy 用于处理 RAW 文件
try:
    import rawpy
except ImportError:
    rawpy = None
    print("Warning: rawpy module not found. RAW image processing will be limited.")

# 尝试导入 pillow_heif 用于处理 HEIF/HIF 文件
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    print("HEIF/HIF support enabled.")
except ImportError:
    print("Warning: pillow_heif module not found. HEIF/HIF images may not load.")

# 初始化应用
if getattr(sys, 'frozen', False):
    template_folder = os.path.join(sys._MEIPASS, 'templates')
    static_folder = os.path.join(sys._MEIPASS, 'static')
    app = Flask(__name__, template_folder=template_folder, static_folder=static_folder)
else:
    app = Flask(__name__)

app.secret_key = 'image-manager-secret-key-2024'

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app.logger.setLevel(logging.INFO)

# 配置
CONFIG = {
    'marked_folder_name': '_marked_images',
    'trash_folder_name': '_trash',
    'max_trash_size': 30,  # 回收站最大容量
    'thumbnail_size': (600, 600),  # 缩略图大小
    'preview_size': (3840, 2160),   # 预览图大小 (4K resolution)
    'image_extensions': {
        '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.hif', '.heic',
        '.cr3', '.cr2', '.nef', '.arw', '.dng',
        '.JPG', '.JPEG', '.PNG', '.GIF', '.BMP', '.HIF', '.HEIC',
        '.CR3', '.CR2', '.NEF', '.ARW', '.DNG'
    },
    'raw_extensions': {
        '.cr3', '.cr2', '.nef', '.arw', '.dng',
        '.CR3', '.CR2', '.NEF', '.ARW', '.DNG'
    }
}

# 扩展名小写集合，方便快速判断
IMAGE_EXT_LOWER = {ext.lower() for ext in CONFIG['image_extensions']}
RAW_EXT_LOWER = {ext.lower() for ext in CONFIG['raw_extensions']}

# 缓存管理
class CacheManager:
    def __init__(self):
        # 使用绝对路径，确保在任何工作目录下都能找到缓存目录
        if getattr(sys, 'frozen', False):
            self.base_dir = Path(sys.executable).parent.resolve()
        else:
            self.base_dir = Path(__file__).parent.resolve()
        self.cache_dir = self.base_dir / '_cache'
        self.cache_dir.mkdir(exist_ok=True)
        self.max_age = 3600  # 1小时缓存
    
    def get_cache_key(self, path, size):
        try:
            mtime = Path(path).stat().st_mtime
        except (FileNotFoundError, OSError):
            mtime = 0
        key = f"{path}_{size}_{mtime}"
        return hashlib.md5(key.encode()).hexdigest()
    
    def get_cached_path(self, path, size):
        if not Path(path).exists():
            return None
        
        cache_key = self.get_cache_key(path, size)
        cache_file = self.cache_dir / f"{cache_key}.jpg"
        
        # 检查缓存是否存在且未过期
        if cache_file.exists():
            try:
                cache_mtime = cache_file.stat().st_mtime
                if time.time() - cache_mtime < self.max_age:
                    return cache_file
            except OSError:
                pass
        
        return None
    
    def save_cache(self, path, size, img_data):
        try:
            cache_key = self.get_cache_key(path, size)
            cache_file = self.cache_dir / f"{cache_key}.jpg"
            # 确保缓存目录存在
            self.cache_dir.mkdir(exist_ok=True)
            img_data.save(cache_file, 'JPEG', quality=95)
            return cache_file
        except Exception as e:
            print(f"保存缓存失败: {e}")
            return None

cache = CacheManager()

# 工具函数
def get_drives():
    """获取系统驱动器列表 (Windows)"""
    drives = []
    if platform.system() == 'Windows':
        import string
        from ctypes import windll
        bitmask = windll.kernel32.GetLogicalDrives()
        for letter in string.ascii_uppercase:
            if bitmask & 1:
                drives.append(f"{letter}:\\")
            bitmask >>= 1
    else:
        drives.append('/')
    return drives

def convert_heic_to_jpg(file_path):
    """将HEIC/HIF文件转换为JPG并替换原文件"""
    path = Path(file_path)
    if path.suffix.lower() not in ['.heic', '.hif']:
        return str(path)
    
    try:
        # 检查文件是否真的存在
        if not path.exists():
            return str(path)

        app.logger.info(f"Converting HEIC to JPG: {file_path}")
        
        # 优先使用 PIL Image.open (会自动使用 pillow_heif)
        # 这样可以更好地处理色彩模式、EXIF 等
        try:
            with Image.open(path) as img:
                img.load()
                
                # 处理 EXIF 旋转
                try:
                    img = ImageOps.exif_transpose(img)
                except Exception:
                    pass
                
                # 转换为 RGB
                if img.mode != 'RGB':
                    if img.mode in ('RGBA', 'LA'):
                        # 处理透明度：白色背景
                        background = Image.new('RGB', img.size, (255, 255, 255))
                        background.paste(img, mask=img.split()[-1])
                        img = background
                    else:
                        img = img.convert('RGB')
                
                new_path = path.with_suffix('.jpg')
                img.save(new_path, "JPEG", quality=95)
                
                path.unlink()
                app.logger.info(f"Converted and replaced: {path} -> {new_path}")
                return str(new_path)
                
        except Exception as e:
            app.logger.warning(f"PIL conversion failed: {e}, trying fallback")
            # Fallback using pillow_heif directly
            if 'pillow_heif' in sys.modules:
                import pillow_heif
                heif_file = pillow_heif.open_heif(path)
                img = Image.frombytes(
                    heif_file.mode,
                    heif_file.size,
                    heif_file.data,
                    "raw",
                )
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                
                new_path = path.with_suffix('.jpg')
                img.save(new_path, "JPEG", quality=95)
                path.unlink()
                app.logger.info(f"Fallback converted: {path} -> {new_path}")
                return str(new_path)
            return str(path)
            
    except Exception as e:
        app.logger.error(f"Failed to convert HEIC {path}: {e}")
        return str(path)

def get_all_image_files(folder='.', show_raw=False):
    """获取所有图片文件，包括已整理的子目录"""
    try:
        folder_path = Path(folder).resolve()
    except Exception:
        return []

    if not folder_path.exists():
        return []
        
    images = []
    
    # 确定需要扫描的文件夹：当前文件夹 + 可能的已整理子文件夹
    folders_to_scan = [folder_path]
    
    # 查找可能的已整理子文件夹
    possible_subfolders = set()
    # 显式添加 RAW 目录，确保能识别
    possible_subfolders.add('RAW')
    
    for ext in CONFIG['image_extensions']:
        # 如果不是 RAW 后缀，添加对应的分类文件夹（如 JPG, PNG）
        # 注意：CONFIG['raw_extensions'] 包含大小写，这里简单判断
        if ext.lower() not in [e.lower() for e in CONFIG['raw_extensions']]:
            possible_subfolders.add(ext[1:].upper())
            
    for sub in possible_subfolders:
        sub_path = folder_path / sub
        if sub_path.exists() and sub_path.is_dir():
            folders_to_scan.append(sub_path)
    
    seen_paths = set()
    images_by_stem = {}
    
    for scan_path in folders_to_scan:
        for ext in CONFIG['image_extensions']:
            for file in scan_path.glob(f'*{ext}'):
                # 自动转换 HEIC/HIF 格式
                if file.suffix.lower() in ['.heic', '.hif']:
                    new_path = convert_heic_to_jpg(file)
                    file = Path(new_path)

                # 解决 Windows 下大小写不敏感导致的重复加载问题
                norm_path = str(file).lower() if platform.system() == 'Windows' else str(file)
                if norm_path in seen_paths:
                    continue
                seen_paths.add(norm_path)
                
                is_raw = file.suffix.lower() in [e.lower() for e in CONFIG['raw_extensions']]
                img_type = 'RAW' if is_raw else 'JPG'
                
                img_data = {
                    'name': file.name,
                    'path': str(file).replace('\\', '/'),
                    'size': file.stat().st_size,
                    'mtime': file.stat().st_mtime,
                    'type': img_type,
                    'ext': file.suffix.lower()
                }
                
                # 按文件名主干分组 (忽略大小写)
                stem = file.stem.lower()
                if stem not in images_by_stem:
                    images_by_stem[stem] = []
                images_by_stem[stem].append(img_data)
    
    # 筛选逻辑：如果同名文件既有 JPG/PNG 又有 RAW，只显示 JPG/PNG，但记录 RAW 的路径
    final_images = []
    for stem, group in images_by_stem.items():
        # 找出非 RAW 文件
        non_raws = [img for img in group if img['type'] != 'RAW']
        raws = [img for img in group if img['type'] == 'RAW']
        
        if non_raws:
            # 如果有非 RAW 文件，优先显示非 RAW 文件
            # 如果有多个非 RAW (如 jpg 和 png)，优先显示 jpg
            non_raws.sort(key=lambda x: 0 if 'jpg' in x['ext'] or 'jpeg' in x['ext'] else 1)
            selected_img = non_raws[0]
            
            # 如果存在 RAW 文件，将 RAW 信息附加到选中的图片对象中
            if raws:
                selected_img['has_raw'] = True
                selected_img['raw_path'] = raws[0]['path']
                selected_img['raw_name'] = raws[0]['name']
            else:
                selected_img['has_raw'] = False
                
            final_images.append(selected_img)
            
            # 如果开启了显示 RAW，则将 RAW 文件也加入列表
            if show_raw and raws:
                for r in raws:
                    final_images.append(r)
        else:
            # 只有 RAW 文件，显示 RAW
            if raws:
                final_images.append(raws[0])
                # 如果有多个 RAW (比如不同后缀)，且开启了 show_raw，显示其他的
                if show_raw and len(raws) > 1:
                    for r in raws[1:]:
                        final_images.append(r)
    
    # 按修改时间倒序排列
    final_images.sort(key=lambda x: x['mtime'], reverse=True)
    return final_images


def scan_aesthetic_items(root_folder='.', shuffle=True):
    """递归扫描作品，用于审美提升模式。

    规则：
    - 作者归档目录：严格遵守 .\\作者\\作品名称\\文件
    - 零散文件：根目录或其他非标准层级的文件
    """
    try:
        root_path = Path(root_folder).resolve()
    except Exception:
        return []

    if not root_path.exists():
        return []

    items = []

    for dirpath, dirnames, filenames in os.walk(root_path):
        # 忽略特殊目录
        dirnames[:] = [d for d in dirnames if d not in {'_marked_images', '_trash'}]
        
        current_dir = Path(dirpath)
        for fname in filenames:
            ext = Path(fname).suffix.lower()
            
            file_path = current_dir / fname
            
            # 自动转换 HEIC/HIF 格式
            if ext in ['.heic', '.hif']:
                new_path = convert_heic_to_jpg(file_path)
                file_path = Path(new_path)
                ext = file_path.suffix.lower()
                fname = file_path.name

            if ext not in IMAGE_EXT_LOWER:
                continue

            try:
                stat = file_path.stat()
            except OSError:
                continue

            try:
                rel_parts = file_path.relative_to(root_path).parts
            except ValueError:
                rel_parts = ()

            author = ''
            work = ''
            category = 'loose'

            if len(rel_parts) >= 3:
                author = rel_parts[0].strip()
                work = rel_parts[1].strip()
                if author and work:
                    category = 'archive'

            item_type = 'RAW' if ext in RAW_EXT_LOWER else 'JPG'

            items.append({
                'name': file_path.name,
                'path': str(file_path).replace('\\', '/'),
                'size': stat.st_size,
                'mtime': stat.st_mtime,
                'type': item_type,
                'ext': ext,
                'author': author,
                'work': work,
                'category': category,
                'has_meta': category == 'archive'
            })

    if shuffle:
        random.shuffle(items)
    else:
        # 默认按时间降序，便于可预测调试
        items.sort(key=lambda x: x['mtime'], reverse=True)

    return items

def get_image_info(file_path):
    """获取图片信息"""
    path = Path(file_path)
    info = {
        'name': path.name,
        'size': path.stat().st_size,
        'mtime': path.stat().st_mtime,
        'date': datetime.fromtimestamp(path.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
    }
    
    try:
        with Image.open(file_path) as img:
            info['width'], info['height'] = img.size
            info['format'] = img.format
    except:
        info['width'], info['height'] = 0, 0
        info['format'] = 'RAW'
    
    return info

def create_thumbnail(image_path, size=CONFIG['thumbnail_size']):
    """创建缩略图"""
    app.logger.info(f"--- create_thumbnail called for path: {image_path}, size: {size} ---")
    
    # 检查缓存
    cached = cache.get_cached_path(image_path, size)
    if cached:
        app.logger.info(f"Cache hit for {image_path}. Returning cached file: {cached}")
        return cached
    
    app.logger.info(f"Cache miss for {image_path}. Proceeding to generate new thumbnail.")
    
    app.logger.info(f"Cache miss for {image_path}. Proceeding to generate new thumbnail.")
    
    try:
        img = None
        # 尝试使用 rawpy 处理 RAW 文件
        ext = Path(image_path).suffix.lower()
        if ext in [e.lower() for e in CONFIG['raw_extensions']]:
            if rawpy:
                try:
                    app.logger.info(f"Attempting to open RAW file with rawpy: {image_path}")
                    with rawpy.imread(image_path) as raw:
                        # postprocess 默认转换为 RGB
                        rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=False, bright=1.0)
                        img = Image.fromarray(rgb)
                        app.logger.info(f"Successfully opened RAW file with rawpy: {image_path}")
                except Exception as e:
                    app.logger.error(f"rawpy failed to open {image_path}: {e}")
            else:
                app.logger.warning(f"rawpy not installed. Skipping RAW processing for {image_path}")

        # 如果 rawpy 没处理（不是RAW或失败），尝试使用 PIL
        if img is None:
            try:
                with Image.open(image_path) as pil_img:
                    app.logger.info(f"Successfully opened image with PIL: {image_path}. Mode: {pil_img.mode}, format: {pil_img.format}")
                    
                    # 复制图像以避免文件关闭问题
                    img = pil_img.copy()
                    
                    # 处理EXIF旋转信息
                    try:
                        img = ImageOps.exif_transpose(img)
                    except Exception as e:
                        app.logger.warning(f"Failed to transpose image {image_path}: {e}")
            except Exception as e:
                app.logger.warning(f"PIL failed to open {image_path}: {e}")
                # 尝试显式使用 pillow_heif 打开 (针对某些扩展名不匹配或 PIL 识别失败的 HEIC 文件)
                if 'pillow_heif' in sys.modules:
                    try:
                        import pillow_heif
                        app.logger.info(f"Attempting fallback with pillow_heif for {image_path}")
                        heif_file = pillow_heif.open_heif(image_path)
                        img = Image.frombytes(
                            heif_file.mode,
                            heif_file.size,
                            heif_file.data,
                            "raw",
                        )
                        app.logger.info(f"Successfully opened with pillow_heif fallback: {image_path}")
                    except Exception as heif_e:
                        app.logger.error(f"pillow_heif fallback failed: {heif_e}")

        if img:
            # 确保在缩放前转换为 RGB，避免某些特殊模式 (如 I;16, CMYK) 导致缩放失败或颜色错误
            if img.mode not in ('RGB', 'RGBA', 'L'):
                app.logger.info(f"Converting image from mode {img.mode} to RGB before resizing.")
                img = img.convert('RGB')

            # 保持宽高比
            img.thumbnail(size, Image.Resampling.LANCZOS)
            app.logger.info(f"Image resized to: {img.size}")
            
            # 如果是PNG或其他透明格式，转换为RGB (保存为JPEG需要)
            if img.mode in ('RGBA', 'LA'):
                app.logger.info(f"Converting transparent image from mode {img.mode} to RGB.")
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'LA':
                    img = img.convert('RGBA')
                background.paste(img, mask=img.split()[-1])
                img = background
            elif img.mode != 'RGB':
                # Should be covered by pre-resize convert, but safe to keep
                img = img.convert('RGB')
            
            cache_path = cache.save_cache(image_path, size, img)
            app.logger.info(f"Saved new thumbnail to cache: {cache_path}")
            return cache_path
            
    except Exception as e:
        app.logger.error(f"创建缩略图失败 for {image_path}: {e}", exc_info=True)
        # 返回默认图片
        default_img = Image.new('RGB', size, (220, 220, 220))
        return cache.save_cache(image_path, size, default_img)

def organize_images(source_folder):
    """整理图片：按格式分类"""
    source = Path(source_folder)
    moved_files = []
    
    for file in source.iterdir():
        if file.is_file():
            ext = file.suffix.lower()
            if ext in CONFIG['image_extensions']:
                # 创建分类文件夹
                folder_name = ext[1:].upper()  # 去掉点号，转为大写
                if ext in CONFIG['raw_extensions']:
                    folder_name = 'RAW'
                
                target_folder = source / folder_name
                target_folder.mkdir(exist_ok=True)
                
                # 移动文件
                target_path = target_folder / file.name
                if not target_path.exists():
                    shutil.move(str(file), str(target_path))
                    moved_files.append({
                        'original': str(file),
                        'new': str(target_path)
                    })
    
    # 保存整理记录
    if moved_files:
        record_file = source / '_organize_record.json'
        if record_file.exists():
            with open(record_file, 'r') as f:
                records = json.load(f)
        else:
            records = []
        
        records.append({
            'time': datetime.now().isoformat(),
            'files': moved_files
        })
        
        with open(record_file, 'w') as f:
            json.dump(records, f, indent=2)
    
    return moved_files

def revert_organization(source_folder):
    """还原整理操作"""
    source = Path(source_folder)
    record_file = source / '_organize_record.json'
    
    if not record_file.exists():
        return []
    
    with open(record_file, 'r') as f:
        records = json.load(f)
    
    if not records:
        return []
    
    # 获取最新的整理记录
    last_record = records[-1]
    reverted_files = []
    
    for file_info in last_record['files']:
        original = Path(file_info['original'])
        new_path = Path(file_info['new'])
        
        if new_path.exists():
            # 移动回原位置
            if not original.exists():
                shutil.move(str(new_path), str(original))
                reverted_files.append(str(original))
            
            # 如果目标文件夹为空，删除文件夹
            folder = new_path.parent
            if folder.exists() and not any(folder.iterdir()):
                folder.rmdir()
    
    # 删除整理记录
    record_file.unlink()
    
    return reverted_files

def mark_image(filename, source_folder, mark=True):
    """标记/取消标记图片"""
    app.logger.info(f"mark_image called: filename={filename}, folder={source_folder}, mark={mark}")
    source = Path(source_folder)
    marked_folder = source / CONFIG['marked_folder_name']
    marked_folder.mkdir(exist_ok=True)
    
    # 找到所有同名文件（不同扩展名）
    base_name = Path(filename).stem
    # 尝试转义特殊字符，防止 glob 匹配错误
    escaped_base_name = glob.escape(base_name)
    related_files = []
    
    app.logger.info(f"Searching for files matching: {escaped_base_name}.* in {source}")
    
    # 在当前目录及子目录中查找
    for file in source.rglob(f"{escaped_base_name}.*"):
        # 排除 _marked_images 和 _trash 目录中的文件，防止重复处理
        if CONFIG['marked_folder_name'] in file.parts or CONFIG['trash_folder_name'] in file.parts:
            continue

        if file.suffix.lower() in CONFIG['image_extensions']:
            related_files.append(file)
    
    app.logger.info(f"Found {len(related_files)} related files: {[str(f) for f in related_files]}")
    
    if mark:
        # 标记：复制到标记文件夹
        for file in related_files:
            try:
                ext_folder = marked_folder / file.suffix[1:].upper()
                ext_folder.mkdir(exist_ok=True)
                
                target = ext_folder / file.name
                if not target.exists():
                    shutil.copy2(str(file), str(target))
                    app.logger.info(f"Copied {file} to {target}")
                else:
                    app.logger.info(f"Target {target} already exists, skipping copy.")
            except Exception as e:
                app.logger.error(f"Failed to copy {file}: {e}")
    else:
        # 取消标记：从标记文件夹删除
        for file in related_files:
            try:
                ext_folder = marked_folder / file.suffix[1:].upper()
                target = ext_folder / file.name
                
                if target.exists():
                    target.unlink()
                    app.logger.info(f"Removed {target}")
                
                # 如果文件夹为空，删除文件夹
                if ext_folder.exists() and not any(ext_folder.iterdir()):
                    ext_folder.rmdir()
            except Exception as e:
                app.logger.error(f"Failed to remove {file} from marked: {e}")
    
    return len(related_files)

def delete_image(filename, source_folder, permanent=False):
    """删除图片（移动到回收站）"""
    source = Path(source_folder)
    trash_folder = source / CONFIG['trash_folder_name']
    
    # 找到所有同名文件
    base_name = Path(filename).stem
    related_files = []
    
    for file in source.rglob(f"{base_name}.*"):
        if file.suffix.lower() in CONFIG['image_extensions']:
            related_files.append(file)
    
    deleted_files = []
    
    if permanent:
        # 永久删除
        for file in related_files:
            if file.exists():
                file.unlink()
                deleted_files.append(str(file))
    else:
        # 移动到回收站
        trash_folder.mkdir(exist_ok=True)
        current_trash_files = []
        
        for file in related_files:
            if file.exists():
                # 创建时间戳副本名，避免重名
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                trash_name = f"{file.stem}_{timestamp}{file.suffix}"
                trash_path = trash_folder / trash_name
                
                shutil.move(str(file), str(trash_path))
                deleted_files.append(str(file))
                current_trash_files.append(str(trash_path))
        
        # 管理回收站大小 (在添加新文件后执行，确保不超过限制)
        manage_trash_size(trash_folder)
        
        # 保存删除记录
        record = {
            'time': datetime.now().isoformat(),
            'original_name': filename,
            'trash_files': current_trash_files
        }
        
        record_file = trash_folder / '_delete_record.json'
        if record_file.exists():
            with open(record_file, 'r') as f:
                records = json.load(f)
        else:
            records = []
        
        records.append(record)
        
        # 只保留最近30条记录
        if len(records) > CONFIG['max_trash_size']:
            records = records[-CONFIG['max_trash_size']:]
        
        with open(record_file, 'w') as f:
            json.dump(records, f, indent=2)
    
    # 也删除标记文件夹中的副本
    marked_folder = source / CONFIG['marked_folder_name']
    for file in related_files:
        marked_path = marked_folder / file.suffix[1:].upper() / file.name
        if marked_path.exists():
            marked_path.unlink()
    
    return deleted_files

def restore_image(filename, source_folder):
    """从回收站恢复图片"""
    source = Path(source_folder)
    trash_folder = source / CONFIG['trash_folder_name']
    
    # 查找最新的删除记录
    record_file = trash_folder / '_delete_record.json'
    if not record_file.exists():
        return False
    
    with open(record_file, 'r') as f:
        records = json.load(f)
    
    # 反向查找匹配的记录
    for record in reversed(records):
        if record['original_name'] == filename:
            # 恢复文件
            for trash_file in record['trash_files']:
                trash_path = Path(trash_file)
                if trash_path.exists():
                    # 提取原始文件名（去掉时间戳）
                    # 时间戳格式为 YYYYMMDD_HHMMSS，包含一个下划线，且与文件名之间也有一个下划线
                    # 所以 split('_') 后，最后两个部分是时间戳
                    parts = trash_path.stem.split('_')
                    if len(parts) >= 3:
                        original_stem = '_'.join(parts[:-2])
                    else:
                        # Fallback if something is wrong with the name format
                        original_stem = '_'.join(parts[:-1])
                        
                    original_name = f"{original_stem}{trash_path.suffix}"
                    original_path = source / original_name
                    
                    # 确保目标文件夹存在 (例如 RAW 文件夹)
                    if 'RAW' in trash_path.parts or trash_path.suffix.lower() in CONFIG['raw_extensions']:
                         # 简单判断，如果原文件是在子文件夹中，这里可能需要更复杂的逻辑
                         # 但目前的 delete_image 是从 source_folder 查找的，
                         # 如果文件原本在 source/RAW/ 下，delete_image 也是在 source 下查找
                         # 恢复时，我们假设它回到 source 下。
                         # 等等，organize_images 会把文件移动到子文件夹。
                         # delete_image 是通过 rglob 查找的。
                         # 如果文件在 RAW/ 下，它被移动到 _trash/ 下。
                         # 恢复时，original_path = source / original_name。
                         # 这会把文件恢复到根目录，而不是 RAW/ 目录！
                         pass
                    
                    # 尝试恢复到原始位置的逻辑优化
                    # 如果文件名后缀对应某个分类文件夹，且该文件夹存在，则恢复进去
                    target_folder = source
                    ext = trash_path.suffix.lower()
                    if ext in CONFIG['raw_extensions']:
                        possible_raw_folder = source / 'RAW'
                        if possible_raw_folder.exists():
                            target_folder = possible_raw_folder
                    elif ext in CONFIG['image_extensions']:
                         # 检查是否有对应的扩展名文件夹
                         possible_ext_folder = source / ext[1:].upper()
                         if possible_ext_folder.exists():
                             target_folder = possible_ext_folder
                    
                    original_path = target_folder / original_name
                    
                    shutil.move(str(trash_path), str(original_path))
            
            # 从记录中移除
            records.remove(record)
            
            with open(record_file, 'w') as f:
                json.dump(records, f, indent=2)
            
            return True
    
    return False

def manage_trash_size(trash_folder):
    """管理回收站大小"""
    trash_folder = Path(trash_folder)
    
    if not trash_folder.exists():
        return
    
    # 获取所有文件
    all_files = []
    for file in trash_folder.iterdir():
        if file.is_file() and file.name != '_delete_record.json':
            all_files.append((file, file.stat().st_mtime))
    
    # 按修改时间排序
    all_files.sort(key=lambda x: x[1])
    
    # 删除最旧的文件，只保留最近的30个
    if len(all_files) > CONFIG['max_trash_size']:
        for file, _ in all_files[:-CONFIG['max_trash_size']]:
            file.unlink()

def get_marked_images(source_folder):
    """获取已标记的图片"""
    source = Path(source_folder)
    marked_folder = source / CONFIG['marked_folder_name']
    marked_images = []
    
    if marked_folder.exists():
        for ext_folder in marked_folder.iterdir():
            if ext_folder.is_dir():
                for file in ext_folder.iterdir():
                    if file.is_file():
                        marked_images.append(file.name)
    
    return marked_images

# Flask路由
@app.route('/')
def index():
    """主页面"""
    return render_template('index.html')

@app.route('/api/dirs')
def get_dirs():
    """获取子目录列表"""
    path = request.args.get('path', '')
    
    try:
        if not path:
            # 返回驱动器列表
            return jsonify({
                'current': '',
                'dirs': get_drives(),
                'is_root': True
            })
        
        p = Path(path).resolve()
        if not p.exists():
            return jsonify({'error': '路径不存在'}), 404
            
        dirs = []
        # 添加上级目录
        if p.parent != p:
            dirs.append('..')
            
        for item in p.iterdir():
            try:
                if item.is_dir() and not item.name.startswith('.') and not item.name.startswith('_'):
                    dirs.append(item.name)
            except PermissionError:
                continue
                
        return jsonify({
            'current': str(p),
            'dirs': dirs,
            'is_root': False
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/images')
def get_images():
    """获取图片列表"""
    folder = request.args.get('folder', '.')
    show_raw = request.args.get('show_raw', 'false') == 'true'
    images = get_all_image_files(folder, show_raw)
    return jsonify(images)


@app.route('/api/aesthetic')
def api_aesthetic():
    """审美提升模式：获取随机混排的作品列表"""
    folder = request.args.get('folder', '.')
    shuffle = request.args.get('shuffle', 'true') == 'true'
    author = request.args.get('author')  # 可选
    work = request.args.get('work')      # 可选

    if aesthetic_scan_items is None:
        # 兼容旧版本，若未导入模块则尝试使用内置的简化扫描（若存在）
        try:
            items = scan_aesthetic_items(folder, shuffle)
        except Exception:
            items = []
    else:
        items = aesthetic_scan_items(
            folder,
            CONFIG['image_extensions'],
            CONFIG['raw_extensions'],
            shuffle=shuffle,
            author=author,
            work=work,
        )

    return jsonify({'count': len(items), 'items': items})

@app.route('/api/image/<path:filename>')
def get_image_info_api(filename):
    """获取图片详细信息"""
    info = get_image_info(filename)
    return jsonify(info)

@app.route('/api/thumbnail/<path:filename>')
def get_thumbnail(filename):
    """获取缩略图"""
    # Flask 路由中的 <path:filename> 已经自动解码了 URL
    # 再次 unquote 会导致带有 % 的文件名（如 "100% hot.jpg"）被错误解码
    # decoded_filename = urllib.parse.unquote(filename)
    decoded_filename = filename
    
    app.logger.info(f"GET /api/thumbnail - Filename: {filename}")
    
    try:
        # 使用路径创建缩略图
        thumbnail_path = create_thumbnail(decoded_filename)
        
        if thumbnail_path:
            p = Path(thumbnail_path)
            if p.exists():
                app.logger.info(f"Serving thumbnail: {p}")
                response = send_from_directory(str(p.parent), p.name, mimetype='image/jpeg')
                # 开启浏览器缓存 (1年)
                response.headers['Cache-Control'] = 'public, max-age=31536000'
                return response
            else:
                app.logger.error(f"Thumbnail file missing: {p}")
        else:
            app.logger.error("Thumbnail generation returned None")
            
    except Exception as e:
        app.logger.error(f"Error in get_thumbnail route: {e}", exc_info=True)
    
    app.logger.warning(f"Thumbnail generation failed for {decoded_filename}, returning 404.")
    return "Thumbnail generation failed", 404

@app.route('/api/preview/<path:filename>')
def get_preview(filename):
    """获取预览图"""
    try:
        preview_path = create_thumbnail(filename, CONFIG['preview_size'])
        if preview_path:
            p = Path(preview_path)
            if p.exists():
                response = send_from_directory(str(p.parent), p.name, mimetype='image/jpeg')
                # 开启浏览器缓存 (1年)
                response.headers['Cache-Control'] = 'public, max-age=31536000'
                return response
    except Exception as e:
        print(f"Error serving preview: {e}")
    
    return "Preview generation failed", 404

@app.route('/api/original/<path:filename>')
def get_original(filename):
    """获取原始图片"""
    return send_file(str(filename))

@app.route('/api/organize', methods=['POST'])
def api_organize():
    """整理图片"""
    data = request.json
    folder = data.get('folder', '.')
    moved = organize_images(folder)
    return jsonify({
        'success': True,
        'moved_count': len(moved),
        'message': f'已整理 {len(moved)} 个文件'
    })

@app.route('/api/revert', methods=['POST'])
def api_revert():
    """还原整理"""
    data = request.json
    folder = data.get('folder', '.')
    reverted = revert_organization(folder)
    return jsonify({
        'success': True,
        'reverted_count': len(reverted),
        'message': f'已还原 {len(reverted)} 个文件'
    })

@app.route('/api/mark', methods=['POST'])
def api_mark():
    """标记图片"""
    data = request.json
    filename = data.get('filename')
    folder = data.get('folder', '.')
    mark = data.get('mark', True)
    
    count = mark_image(filename, folder, mark)
    action = '标记' if mark else '取消标记'
    
    return jsonify({
        'success': True,
        'count': count,
        'message': f'{action}了 {count} 个相关文件'
    })

@app.route('/api/delete', methods=['POST'])
def api_delete():
    """删除图片"""
    data = request.json
    filename = data.get('filename')
    folder = data.get('folder', '.')
    permanent = data.get('permanent', False)
    
    deleted = delete_image(filename, folder, permanent)
    action = '永久删除' if permanent else '移动到回收站'
    
    return jsonify({
        'success': True,
        'count': len(deleted),
        'message': f'{action}了 {len(deleted)} 个相关文件'
    })

@app.route('/api/restore', methods=['POST'])
def api_restore():
    """恢复图片"""
    data = request.json
    filename = data.get('filename')
    folder = data.get('folder', '.')
    
    app.logger.info(f"API Restore called: filename={filename}, folder={folder}")
    
    success = restore_image(filename, folder)
    
    if success:
        app.logger.info(f"Restore successful for {filename}")
        return jsonify({
            'success': True,
            'message': '文件恢复成功'
        })
    else:
        app.logger.error(f"Restore failed for {filename}")
        return jsonify({
            'success': False,
            'message': '恢复失败，文件不存在于回收站'
        })

@app.route('/api/marked')
def api_get_marked():
    """获取已标记图片"""
    folder = request.args.get('folder', '.')
    marked = get_marked_images(folder)
    return jsonify(marked)

@app.route('/api/trash')
def api_get_trash():
    """获取回收站状态"""
    folder = request.args.get('folder', '.')
    trash_folder = Path(folder) / CONFIG['trash_folder_name']
    
    items = []
    if trash_folder.exists():
        record_file = trash_folder / '_delete_record.json'
        if record_file.exists():
            try:
                with open(record_file, 'r') as f:
                    records = json.load(f)
                # Return records in reverse order (newest first)
                for r in reversed(records):
                    items.append({
                        'original_name': r['original_name'],
                        'time': r['time'],
                        'file_count': len(r['trash_files'])
                    })
            except Exception as e:
                app.logger.error(f"Error reading trash records: {e}")
    
    return jsonify({
        'count': len(items),
        'items': items
    })

@app.route('/api/clear-cache', methods=['POST'])
def api_clear_cache():
    """清除缓存"""
    # 使用全局 cache 对象的路径，确保一致性
    cache_dir = cache.cache_dir
    app.logger.info(f"Clearing cache directory: {cache_dir}")
    
    deleted_count = 0
    if cache_dir.exists():
        for file in cache_dir.iterdir():
            # 只删除文件，且严格排除 _marked_images 文件夹（虽然它不应该在这里）
            # 确保只清理缩略图缓存，不影响用户数据
            if file.is_file() and CONFIG['marked_folder_name'] not in file.name:
                try:
                    file.unlink()
                    deleted_count += 1
                except Exception as e:
                    app.logger.error(f"Failed to delete {file}: {e}")
    
    return jsonify({
        'success': True,
        'message': f'缓存已清除 (共删除 {deleted_count} 个缩略图文件)'
    })

# 静态文件服务
@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory('static', filename)

# System Tray Logic
def create_tray_icon_image():
    # Generate a simple icon if none exists
    width = 64
    height = 64
    color1 = (52, 152, 219) # Blue
    color2 = (255, 255, 255) # White
    
    image = Image.new('RGB', (width, height), color1)
    dc = ImageDraw.Draw(image)
    dc.rectangle((width // 4, width // 4, width * 3 // 4, height * 3 // 4), fill=color2)
    
    return image

def on_tray_open(icon, item):
    webbrowser.open('http://localhost:5000')

def on_tray_exit(icon, item):
    icon.stop()
    os._exit(0)

def run_tray_icon():
    try:
        import pystray
        from pystray import MenuItem as item
        
        image = create_tray_icon_image()
        
        # Try to load app_icon.ico if it exists
        if getattr(sys, 'frozen', False):
             base_dir = sys._MEIPASS
        else:
             base_dir = os.path.dirname(os.path.abspath(__file__))
             
        icon_path = os.path.join(base_dir, 'app_icon.ico')
        if os.path.exists(icon_path):
            try:
                image = Image.open(icon_path)
            except Exception:
                pass

        menu = (
            item('打开浏览器', on_tray_open, default=True),
            item('退出', on_tray_exit)
        )
        
        icon = pystray.Icon("image_manager", image, "图片管理工具", menu)
        icon.run()
        
    except ImportError:
        print("pystray not installed. Running without system tray.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

if __name__ == '__main__':
    print("图片管理工具启动中...")
    if rawpy:
        print("RAW处理模块 (rawpy) 已加载")
    else:
        print("警告: 未检测到 rawpy 模块，RAW 图片将无法预览。请运行: pip install rawpy imageio")
        
    print(f"访问地址: http://localhost:5000")
    print("功能说明:")
    print("1. 整理: 自动按格式分类图片")
    print("2. 审阅: 点击缩略图查看大图，可标记图片")
    print("3. 删除: 删除图片及相关文件")
    print("4. 复原: 恢复整理或删除的文件")
    
    # Start Flask in a separate thread
    flask_thread = threading.Thread(target=lambda: app.run(debug=False, host='0.0.0.0', port=5000, use_reloader=False))
    flask_thread.daemon = True
    flask_thread.start()
    
    print("服务已在后台启动。请检查系统托盘图标。")
    
    # Open browser
    webbrowser.open('http://localhost:5000')
    
    # Run system tray (blocking)
    run_tray_icon()