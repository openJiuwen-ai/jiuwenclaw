import sys
import os
import glob
from io import BytesIO
from PIL import Image


def normalize(path):
    try:
        img = Image.open(path)
        orig_format = img.format or "unknown"
        orig_mode = img.mode
        orig_size = os.path.getsize(path)

        if img.mode in ('RGBA', 'P', 'LA'):
            img = img.convert('RGB')

        buf = BytesIO()
        img.save(buf, format='JPEG', quality=90)
        out = os.path.splitext(path)[0] + '.jpg'
        with open(out, 'wb') as f:
            f.write(buf.getvalue())

        new_size = os.path.getsize(out)
        renamed = out != path

        if renamed:
            print(f"  🔄 {os.path.basename(path)} → {os.path.basename(out)}"
                  f"  {orig_format}/{orig_mode} → JPEG/RGB"
                  f"  {orig_size//1024}KB → {new_size//1024}KB")
        else:
            print(f"  ✅ {os.path.basename(out)}: JPEG"
                  f"  {orig_size//1024}KB → {new_size//1024}KB")

        return out
    except Exception as e:
        print(f"  ⚠️  {os.path.basename(path)}: 跳过 ({e})")
        return path

if __name__ == '__main__':
    target = sys.argv[1] if len(sys.argv) > 1 else '.'
    files = []
    for ext in ('*.jpg', '*.jpeg', '*.png', '*.webp', '*.heic', '*.heif', '*.mpo'):
        files.extend(glob.glob(os.path.join(target, ext)))

    if not files:
        print("⚠️  未找到图片文件")
        sys.exit(0)

    print(f"📦 发现 {len(files)} 张图片，开始预处理...")
    ok, fail = 0, 0
    for f in files:
        result = normalize(f)
        if result != f or os.path.exists(result):
            ok += 1
        else:
            fail += 1

    print(f"✅ 图片预处理完成: {ok} 成功" + (f", {fail} 失败" if fail else ""))