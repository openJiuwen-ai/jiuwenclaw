import io

import pypdf
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from fonts import get_font_path


def create_watermark_pdf(output_path, text="机密", font_size=50, opacity=0.3,
                         angle=45, color=(0.8, 0.8, 0.8), position='center'):
    """
    创建水印PDF文件

    参数:
        output_path: 输出水印文件路径
        text: 水印文字内容
        font_size: 字体大小
        opacity: 透明度（0-1）
        angle: 旋转角度（度）
        color: RGB颜色值，范围0-1，例如(0.8,0.8,0.8)为浅灰色
        position: 水印位置，可选值：'center'（页面中心，带旋转）、'bottom-center'（底部居中，无旋转）
    """
    # 注册鸿蒙黑体字体

    pdfmetrics.registerFont(TTFont('HarmonyHeiTi', get_font_path('Harmony-Bold.ttf')))
    font_name = 'HarmonyHeiTi'

    # 创建A4大小的水印页面
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=A4)

    # 设置透明度（通过设置fillAlpha）
    c.setFillColorRGB(*color)
    c.setFillAlpha(opacity)

    # 设置字体
    c.setFont(font_name, font_size)

    # 计算文本宽度和高度（用于居中）
    text_width = c.stringWidth(text, font_name, font_size)
    text_height = font_size

    # 移动到页面中心
    page_width, page_height = A4

    # 根据位置参数绘制水印
    if position == 'center':
        # 旋转并绘制水印在页面中心
        c.saveState()
        c.translate(page_width / 2, page_height / 2)  # 移动到中心
        c.rotate(angle)  # 旋转指定角度

        # 绘制文字（从中心点开始，向左偏移一半宽度，向下偏移一半高度）
        c.drawString(-text_width / 2, -text_height / 2, text)

        c.restoreState()
    elif position == 'bottom-center':
        # 绘制水印在页面底部居中，无旋转
        x = (page_width - text_width) / 2
        y = 20  # 距离底部20单位
        c.drawString(x, y, text)
    else:
        raise ValueError(f"未知的位置参数: {position}，可选值：'center'、'bottom-center'")

    c.save()

    # 保存水印文件
    packet.seek(0)
    with open(output_path, 'wb') as f:
        f.write(packet.getvalue())

    print(f"水印文件已生成：{output_path}")
    print(f"水印内容：{text}，字体大小：{font_size}，透明度：{opacity}，位置：{position}")
    if position == 'center':
        print(f"旋转角度：{angle}°")

    return output_path


def add_watermark_to_pdf(source_pdf, watermark_pdf, output_pdf):
    """
    将水印应用到PDF文件
    """
    # 读取水印文件
    watermark_reader = pypdf.PdfReader(watermark_pdf)
    watermark_page = watermark_reader.pages[0]

    # 读取源文件
    reader = pypdf.PdfReader(source_pdf)
    writer = pypdf.PdfWriter()

    # 为每一页添加水印
    for page in reader.pages:
        page.merge_page(watermark_page)
        writer.add_page(page)

    # 保存文件
    with open(output_pdf, 'wb') as f:
        writer.write(f)

    print(f"已为PDF添加水印：{output_pdf}")
