#!/usr/bin/env node
const fs = require("fs");
const path = require("path");

// ==================== 依赖检查 ====================
function checkDeps() {
  const required = ["pptxgenjs", "react", "react-dom", "react-icons", "sharp"];
  const missing = required.filter((m) => {
    try { require.resolve(m); return false; } catch { return true; }
  });
  if (missing.length) {
    const { execSync } = require("child_process");
    console.log("Installing:", missing.join(", "));
    execSync(`npm install --no-save ${missing.join(" ")}`, {
      stdio: "inherit", cwd: __dirname,
    });
  }
}
checkDeps();

const pptxgen = require("pptxgenjs");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");

// ==================== 图标缓存 ====================
const _iconCache = new Map();

const HIGH_FREQ_ICONS = [
  "FaChartLine", "FaChartBar", "FaTrophy", "FaStar", "FaRocket",
  "FaUsers", "FaBuilding", "FaCog", "FaBolt", "FaCar", "FaFlask",
  "FaMicrochip", "FaServer", "FaMobile", "FaCloud", "FaShieldAlt",
  "FaGlobe", "FaDollarSign", "FaBullseye", "FaHandshake", "FaBrain",
  "FaIndustry", "FaAndroid", "FaImage", "FaHistory", "FaBook",
  "FaCamera", "FaFlag", "FaMapMarkerAlt", "FaPlane", "FaShip",
];

async function warmIconCache() {
  const colors = ["#FFFFFF"];
  await Promise.all(
    HIGH_FREQ_ICONS.flatMap((icon) =>
      colors.map((color) => resolveIcon(icon, color))
    )
  );
}

// ==================== 图标工具 ====================
function renderIconSvg(IconComponent, color = "#000000", size = 256) {
  return ReactDOMServer.renderToStaticMarkup(
    React.createElement(IconComponent, { color, size: String(size) })
  );
}
async function iconToBase64Png(IconComponent, color, size = 256) {
  const svg = renderIconSvg(IconComponent, color, size);
  const pngBuffer = await sharp(Buffer.from(svg)).png().toBuffer();
  return "image/png;base64," + pngBuffer.toString("base64");
}
async function resolveIcon(iconName, color) {
  const cacheKey = `${iconName}|${color}`;
  if (_iconCache.has(cacheKey)) return _iconCache.get(cacheKey);
  try {
    const iconSets = [
      () => require("react-icons/fa"),
      () => require("react-icons/md"),
      () => require("react-icons/hi"),
      () => require("react-icons/bi"),
    ];
    for (const loadSet of iconSets) {
      const set = loadSet();
      if (set[iconName]) {
        const result = await iconToBase64Png(set[iconName], color);
        _iconCache.set(cacheKey, result);
        return result;
      }
    }
  } catch (e) {}
  return null;
}

// ==================== 颜色工具 ====================
function hexToRgb(hex) {
  const h = hex.replace(/^#/, "");
  return {
    r: parseInt(h.substring(0, 2), 16),
    g: parseInt(h.substring(2, 4), 16),
    b: parseInt(h.substring(4, 6), 16),
  };
}
function rgbToHex(r, g, b) {
  return [r, g, b].map((c) => Math.max(0, Math.min(255, Math.round(c))).toString(16).padStart(2, "0")).join("").toUpperCase();
}
function mixHex(hexA, hexB, t) {
  const a = hexToRgb(hexA);
  const b = hexToRgb(hexB);
  return rgbToHex(a.r + (b.r - a.r) * t, a.g + (b.g - a.g) * t, a.b + (b.b - a.b) * t);
}
function lighten(hex, amount) { return mixHex(hex, "FFFFFF", amount); }
function darken(hex, amount) { return mixHex(hex, "000000", amount); }

/** 生成丰富的派生色 */
function deriveCardColors(p) {
  return [
    p.accent,
    darken(p.accent, 0.2),
    p.primary,
    lighten(p.primary, 0.35),
    mixHex(p.accent, p.primary, 0.5),
    lighten(p.accent, 0.25),
    darken(p.primary, 0.15),
    mixHex(p.primary, "0EA5E9", 0.4),
  ];
}
/** 柔和行背景色 */
function deriveCardBgColors(p) {
  return [
    lighten(p.accent, 0.93),
    lighten(p.primary, 0.93),
    "F0F9FF", "FFF7ED", "F0FDF4", "FDF4FF", "FFFBEB", "F1F5F9",
  ];
}

/** 创建颜色循环器：getColor(i) 返回第 i 项派生色 */
function createColorCycler(p) {
  const colors = deriveCardColors(p);
  return (i) => colors[i % colors.length];
}

// ==================== 装饰系统 ====================
const DecoStyles = [
  "cornerAccent",   // 左上角 L 形 accent
  "topDualLine",    // 顶部双色线
  "bottomFade",     // 底部渐变带
  "sideStripe",     // 右侧窄色带
  "cornerDots",     // 角落装饰点
  "diagonalCorner", // 对角装饰
  "topBracket",     // 顶部双角 bracket
  "waveBottom",     // 底部波浪装饰
  "subtleFrame",    // 四角框架
];

let _decoIndex = 0;
function nextDeco() {
  return DecoStyles[_decoIndex++ % DecoStyles.length];
}

function applyDeco(slide, style, p, pres) {
  if (style === "cornerAccent") {
    // L 形：竖 + 横
    slide.addShape(pres.shapes.RECTANGLE, {
      x: 0, y: 0, w: 0.06, h: 0.65, fill: { color: p.accent },
    });
    slide.addShape(pres.shapes.RECTANGLE, {
      x: 0, y: 0, w: 0.55, h: 0.04, fill: { color: p.accent },
    });
  } else if (style === "topDualLine") {
    slide.addShape(pres.shapes.RECTANGLE, {
      x: 0, y: 0, w: 10, h: 0.035, fill: { color: p.primary },
    });
    slide.addShape(pres.shapes.RECTANGLE, {
      x: 0, y: 0.035, w: 3.5, h: 0.025, fill: { color: p.accent },
    });
  } else if (style === "bottomFade") {
    slide.addShape(pres.shapes.RECTANGLE, {
      x: 0, y: 5.35, w: 10, h: 0.035, fill: { color: lighten(p.accent, 0.5) },
    });
    slide.addShape(pres.shapes.RECTANGLE, {
      x: 0, y: 5.39, w: 10, h: 0.035, fill: { color: lighten(p.accent, 0.2) },
    });
    slide.addShape(pres.shapes.RECTANGLE, {
      x: 0, y: 5.43, w: 10, h: 0.06, fill: { color: p.accent },
    });
  } else if (style === "sideStripe") {
    slide.addShape(pres.shapes.RECTANGLE, {
      x: 9.72, y: 0, w: 0.28, h: 5.625, fill: { color: p.accent, transparency: 88 },
    });
    slide.addShape(pres.shapes.RECTANGLE, {
      x: 9.86, y: 0, w: 0.14, h: 5.625, fill: { color: p.accent, transparency: 55 },
    });
  } else if (style === "cornerDots") {
    const dotColor = lighten(p.accent, 0.60);
    for (let r = 0; r < 3; r++) {
      for (let c = 0; c < 3; c++) {
        slide.addShape(pres.shapes.OVAL, {
          x: 0.25 + c * 0.22, y: 0.25 + r * 0.22,
          w: 0.09, h: 0.09, fill: { color: dotColor },
        });
      }
    }
  } else if (style === "diagonalCorner") {
    // 右下角装饰三角 (用两个矩形模拟)
    slide.addShape(pres.shapes.RECTANGLE, {
      x: 9.2, y: 5.2, w: 0.8, h: 0.04, fill: { color: p.accent, transparency: 40 },
    });
    slide.addShape(pres.shapes.RECTANGLE, {
      x: 9.6, y: 5.0, w: 0.04, h: 0.6, fill: { color: p.accent, transparency: 40 },
    });
    // 左上小 accent 点
    slide.addShape(pres.shapes.OVAL, {
      x: 0.3, y: 0.3, w: 0.12, h: 0.12, fill: { color: p.accent },
    });
  } else if (style === "topBracket") {
    // 顶部双角 bracket：左上 + 右上 L 形双色
    const bc = lighten(p.accent, 0.35);
    // 左上 bracket
    slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: 0.3, y: 0.2, w: 0.06, h: 0.55, fill: { color: p.accent }, rectRadius: 0.03,
    });
    slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: 0.3, y: 0.2, w: 0.55, h: 0.045, fill: { color: p.accent }, rectRadius: 0.02,
    });
    // 右上 bracket (lighter)
    slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: 9.64, y: 0.2, w: 0.06, h: 0.55, fill: { color: bc }, rectRadius: 0.03,
    });
    slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: 9.15, y: 0.2, w: 0.55, h: 0.045, fill: { color: bc }, rectRadius: 0.02,
    });
  } else if (style === "waveBottom") {
    // 底部波浪装饰 — 一系列半透明圆角矩形/椭圆
    const rows = [
      { y: 5.08, w: 10, h: 0.04, color: lighten(p.accent, 0.55), transp: 0 },
      { y: 5.14, w: 10, h: 0.05, color: lighten(p.accent, 0.3), transp: 10 },
      { y: 5.22, w: 8.5, h: 0.05, color: p.accent, transp: 25 },
      { y: 5.30, w: 6.0, h: 0.06, color: p.accent, transp: 45 },
    ];
    for (const r of rows) {
      slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x: (10 - r.w) / 2, y: r.y, w: r.w, h: r.h,
        fill: { color: r.color }, rectRadius: 0.02,
      });
    }
    // 点缀小圆
    slide.addShape(pres.shapes.OVAL, {
      x: 0.9, y: 5.06, w: 0.14, h: 0.14, fill: { color: p.accent, transparency: 60 },
    });
    slide.addShape(pres.shapes.OVAL, {
      x: 9.0, y: 5.18, w: 0.1, h: 0.1, fill: { color: lighten(p.accent, 0.3), transparency: 50 },
    });
  } else if (style === "subtleFrame") {
    // 四角框架 — 四个角的微妙 accent 圆角方块
    const corners = [
      [0.2, 0.2], [9.5, 0.2], [0.2, 5.0], [9.5, 5.0],
    ];
    for (const [cx, cy] of corners) {
      slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x: cx, y: cy, w: 0.3, h: 0.3,
        fill: { color: p.accent, transparency: 78 }, rectRadius: 0.06,
      });
    }
    // 四角连接短线
    const lines = [
      [0.2, 0.35, 0.8], [9.2, 0.35, 0.8],
      [0.2, 5.3, 0.8], [9.2, 5.3, 0.8],
    ];
    for (const [lx, ly, lw] of lines) {
      slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x: lx, y: ly, w: lw, h: 0.025,
        fill: { color: p.accent, transparency: 75 }, rectRadius: 0.01,
      });
    }
  }
}

/** 内容页微妙背景 — 超浅色大圆 + 小形状，打破纯白单调 */
function addContentBg(slide, p, pres, variant) {
  const v = (variant || 0) % 6;
  if (v === 0) {
    // 右下超浅大圆
    slide.addShape(pres.shapes.OVAL, {
      x: 7.5, y: 3.5, w: 4.5, h: 4.5,
      fill: { color: lighten(p.secondary, 0.65), transparency: 70 },
    });
  } else if (v === 1) {
    // 左上超浅大圆
    slide.addShape(pres.shapes.OVAL, {
      x: -2.5, y: -2.5, w: 5.5, h: 5.5,
      fill: { color: lighten(p.accent, 0.85), transparency: 75 },
    });
  } else if (v === 2) {
    // 右上角淡色块
    slide.addShape(pres.shapes.OVAL, {
      x: 8.0, y: -1.5, w: 3.5, h: 3.5,
      fill: { color: lighten(p.secondary, 0.55), transparency: 80 },
    });
  } else if (v === 3) {
    // 左下角淡色块
    slide.addShape(pres.shapes.OVAL, {
      x: -1.5, y: 4.0, w: 3.0, h: 3.0,
      fill: { color: lighten(p.accent, 0.80), transparency: 80 },
    });
  } else if (v === 4) {
    // 对角双圆 — 左上 + 右下
    slide.addShape(pres.shapes.OVAL, {
      x: -1.8, y: -1.8, w: 4.0, h: 4.0,
      fill: { color: lighten(p.secondary, 0.6), transparency: 75 },
    });
    slide.addShape(pres.shapes.OVAL, {
      x: 7.8, y: 3.3, w: 3.5, h: 3.5,
      fill: { color: lighten(p.accent, 0.88), transparency: 78 },
    });
  } else {
    // 水平条带 + 小圆 — 顶部淡色带
    slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: 0, y: 0, w: 10, h: 0.9,
      fill: { color: lighten(p.secondary, 0.7), transparency: 68 },
      rectRadius: 0,
    });
    slide.addShape(pres.shapes.OVAL, {
      x: 8.5, y: 3.8, w: 2.5, h: 2.5,
      fill: { color: lighten(p.accent, 0.90), transparency: 82 },
    });
  }
}

// ==================== 辅助函数 ====================
const makeShadow = (opts = {}) => ({
  type: "outer", color: "000000", blur: 6, offset: 2, angle: 135, opacity: 0.10, ...opts,
});
const softShadow = (opts = {}) => makeShadow({ blur: 10, offset: 3, opacity: 0.07, ...opts });

/** 页面标题 + accent 短线 */
function addPageTitle(slide, title, p, pres) {
  slide.addText(title, {
    x: 0.6, y: 0.35, w: 8.8, h: 0.60,
    fontSize: 28, fontFace: p.fonts.title, color: p.textDark, bold: true, margin: 0,
  });
  const lineW = Math.min(2.2, Math.max(0.8, title.length * 0.18));
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 0.6, y: 0.98, w: lineW, h: 0.045,
    fill: { color: p.accent }, rectRadius: 0.02,
  });
}

/** 统一双层圆圈绘制：外圈半透明 + 内圈实色 */
function drawColoredCircle(slide, pres, opts) {
  const r = opts.radius;
  const he = opts.haloExtra != null ? opts.haloExtra : 0.06;
  slide.addShape(pres.shapes.OVAL, {
    x: opts.cx - r - he, y: opts.cy - r - he,
    w: (r + he) * 2, h: (r + he) * 2,
    fill: { color: opts.color, transparency: opts.haloTransparency != null ? opts.haloTransparency : 80 },
  });
  slide.addShape(pres.shapes.OVAL, {
    x: opts.cx - r, y: opts.cy - r,
    w: r * 2, h: r * 2,
    fill: { color: opts.color },
  });
}

/** 彩色圆圈 + 可选图标。iconName 为空时返回 null，不绘制任何内容（无空圆圈） */
async function addIconInCircle(slide, pres, opts) {
  if (!opts.iconName) return null;
  drawColoredCircle(slide, pres, opts);
  const iconData = await resolveIcon(opts.iconName, opts.iconColor || "#FFFFFF");
  if (iconData) {
    const inner = opts.radius * (opts.iconScale || 0.68);
    const px = opts.cx - inner / 2;
    const py = opts.cy - inner / 2 + opts.radius * 0.02;
    slide.addImage({ data: iconData, x: px, y: py, w: inner, h: inner });
  }
  return opts.radius;
}

/** 同步绘制圆圈，返回图标规格（供批处理使用） */

/** 同步绘制圆圈，返回图标规格（供批处理使用） */
function drawIconCircle(slide, pres, opts) {
  if (!opts.iconName) return null;
  drawColoredCircle(slide, pres, opts);
  return opts;
}

/** 批量并行解析图标并添加图像 */
async function batchAddIconImages(slide, pres, iconSpecs) {
  const validSpecs = iconSpecs.filter(Boolean);
  if (!validSpecs.length) return;
  const resolved = await Promise.all(
    validSpecs.map((s) => resolveIcon(s.iconName, s.iconColor || "#FFFFFF"))
  );
  for (let i = 0; i < resolved.length; i++) {
    const iconData = resolved[i];
    const opts = validSpecs[i];
    if (!iconData) continue;
    const inner = opts.radius * (opts.iconScale || 0.68);
    const px = opts.cx - inner / 2;
    const py = opts.cy - inner / 2 + opts.radius * 0.02;
    slide.addImage({ data: iconData, x: px, y: py, w: inner, h: inner });
  }
}

/** 内容页标准初始化：背景 + 装饰 + 背景变体 + 页面标题 */
function contentSlideSetup(slide, data, p, pres) {
  slide.background = { color: p.bg };
  const deco = nextDeco();
  addContentBg(slide, p, pres, _bgVariant++);
  applyDeco(slide, deco, p, pres);
  addPageTitle(slide, data.title, p, pres);
}

/** 智能文本 — 超长自动缩字，不截断 */
function fitText(text, maxChars, minFontSize, defaultFontSize) {
  if (!text) return { text: text || "", fontSize: defaultFontSize };
  const len = text.length;
  if (len <= maxChars) return { text, fontSize: defaultFontSize };
  const ratio = maxChars / len;
  const fs = Math.max(minFontSize, Math.round(defaultFontSize * Math.sqrt(ratio)));
  return { text, fontSize: fs };
}

function statValueFontSize(value, cols) {
  const len = String(value).length;
  const availW = (9.0 - 0.3 * (cols - 1)) / cols - 0.35;
  const hasCN = /[\u4e00-\u9fff]/.test(String(value));
  const charW = hasCN ? 0.8 : 0.5;
  const maxPt = Math.floor((availW * 72) / (len * charW));
  const caps = { 2: 48, 3: 38, 4: 26 };
  return Math.min(caps[cols] || 38, Math.max(18, maxPt));
}

// ==================== 全局计数器（内容页背景变体）====================
let _bgVariant = 0;

// ==================== Layout 渲染器 ====================
const renderers = {

  // ==================== cover ====================
  async cover(slide, data, p, pres) {
    slide.background = { color: p.primary };

    // --- 装饰层 ---
    // 超大右上角光晕
    slide.addShape(pres.shapes.OVAL, {
      x: 6.5, y: -2.5, w: 6.5, h: 6.5,
      fill: { color: p.textLight, transparency: 95 },
    });
    // 中等右上内圈
    slide.addShape(pres.shapes.OVAL, {
      x: 7.5, y: -1.5, w: 4.5, h: 4.5,
      fill: { color: p.textLight, transparency: 93 },
    });
    // 左下 accent 球
    slide.addShape(pres.shapes.OVAL, {
      x: -1.2, y: 3.2, w: 3.0, h: 3.0,
      fill: { color: p.accent, transparency: 82 },
    });
    // 右中小球
    slide.addShape(pres.shapes.OVAL, {
      x: 8.8, y: 3.5, w: 0.7, h: 0.7,
      fill: { color: p.secondary, transparency: 65 },
    });
    // 左上微型点
    slide.addShape(pres.shapes.OVAL, {
      x: 0.6, y: 0.5, w: 0.15, h: 0.15,
      fill: { color: p.accent, transparency: 50 },
    });

    // 底部 accent 渐变带
    slide.addShape(pres.shapes.RECTANGLE, {
      x: 0, y: 5.10, w: 10, h: 0.05, fill: { color: p.accent, transparency: 50 },
    });
    slide.addShape(pres.shapes.RECTANGLE, {
      x: 0, y: 5.18, w: 10, h: 0.08, fill: { color: p.accent, transparency: 25 },
    });
    slide.addShape(pres.shapes.RECTANGLE, {
      x: 0, y: 5.28, w: 10, h: 0.12, fill: { color: p.accent },
    });

    // --- 内容 ---
    slide.addText(data.title, {
      x: 0.8, y: 0.9, w: 8.4, h: 1.7,
      fontSize: 44, fontFace: p.fonts.title, color: p.textLight,
      bold: true, align: "center", valign: "middle",
    });
    // accent 分隔线
    slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: 3.3, y: 2.72, w: 3.4, h: 0.05,
      fill: { color: p.accent }, rectRadius: 0.025,
    });
    if (data.subtitle) slide.addText(data.subtitle, {
      x: 1.2, y: 3.0, w: 7.6, h: 0.7,
      fontSize: 20, fontFace: p.fonts.body, color: p.secondary,
      align: "center", valign: "middle",
    });
    if (data.footnote) slide.addText(data.footnote, {
      x: 1.2, y: 4.3, w: 7.6, h: 0.45,
      fontSize: 11, fontFace: p.fonts.body, color: p.textMuted,
      align: "center", transparency: 25,
    });
  },

  // ==================== toc ====================
  async toc(slide, data, p, pres) {
    contentSlideSetup(slide, data, p, pres);

    const items = data.items || [];
    const maxItems = Math.min(items.length, 8);
    const cols = 2;
    const startY = 1.45;
    const cardW = 4.15;
    const cardH = 0.78;
    const gapX = 0.4;
    const gapY = 0.13;
    const totalW = cardW * cols + gapX;
    const startX = (10 - totalW) / 2;
    const getColor = createColorCycler(p);
    const bgColors = deriveCardBgColors(p);
    const rows = Math.ceil(maxItems / cols);

    for (let i = 0; i < maxItems; i++) {
      const col = i % cols;
      const row = Math.floor(i / cols);
      const isLastRow = row === rows - 1;
      const itemsInLastRow = maxItems - (rows - 1) * cols;
      const offsetX = isLastRow && itemsInLastRow === 1 ? (cardW + gapX) / 2 : 0;
      const x = startX + col * (cardW + gapX) + offsetX;
      const y = startY + row * (cardH + gapY);
      const cc = getColor(i);

      slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x, y, w: cardW, h: cardH,
        fill: { color: bgColors[i % bgColors.length] },
        shadow: makeShadow({ blur: 3, opacity: 0.035 }),
        rectRadius: 0.1,
      });

      const circleSize = 0.46;
      const circleX = x + 0.16;
      const circleY = y + (cardH - circleSize) / 2;
      drawColoredCircle(slide, pres, {
        cx: circleX + circleSize / 2, cy: circleY + circleSize / 2,
        radius: circleSize / 2, color: cc,
        haloExtra: 0.03, haloTransparency: 75,
      });
      slide.addText(String(i + 1), {
        x: circleX, y: circleY, w: circleSize, h: circleSize,
        fontSize: 15, fontFace: p.fonts.title, color: p.textLight,
        bold: true, align: "center", valign: "middle", margin: 0,
      });

      const parts = items[i].split(/[·•]/);
      const mainTitle = (parts[0] || items[i]).trim();
      const subTitle = parts[1] ? parts[1].trim() : "";
      const textX = x + 0.82;
      const textW = cardW - 1.1;

      if (subTitle) {
        slide.addText(mainTitle, {
          x: textX, y: y + 0.1, w: textW, h: 0.32,
          fontSize: 15, fontFace: p.fonts.title, color: p.textDark,
          bold: true, valign: "bottom", margin: 0,
        });
        slide.addText(subTitle, {
          x: textX, y: y + 0.43, w: textW, h: 0.28,
          fontSize: 11, fontFace: p.fonts.body, color: p.textMuted,
          valign: "top", margin: 0,
        });
      } else {
        slide.addText(items[i], {
          x: textX, y, w: textW, h: cardH,
          fontSize: 15, fontFace: p.fonts.title, color: p.textDark,
          bold: true, valign: "middle", margin: 0,
        });
      }
    }
  },

  // ==================== section ====================
  async section(slide, data, p, pres) {
    slide.background = { color: p.primary };

    // 顶部细 accent 线
    slide.addShape(pres.shapes.RECTANGLE, {
      x: 0, y: 0, w: 10, h: 0.04, fill: { color: p.accent },
    });

    // 大水印编号
    if (data.number) {
      slide.addText(data.number, {
        x: -1.0, y: -0.2, w: 6.0, h: 5.5,
        fontSize: 220, fontFace: p.fonts.title, color: p.textLight,
        bold: true, align: "left", valign: "middle",
        transparency: 94,
      });
    }
    // 装饰圆 — 四层，更丰富
    slide.addShape(pres.shapes.OVAL, {
      x: 7.5, y: -2.2, w: 5.5, h: 5.5,
      fill: { color: p.accent, transparency: 90 },
    });
    slide.addShape(pres.shapes.OVAL, {
      x: 8.3, y: -1.0, w: 3.5, h: 3.5,
      fill: { color: p.accent, transparency: 84 },
    });
    slide.addShape(pres.shapes.OVAL, {
      x: 7.0, y: 3.0, w: 2.2, h: 2.2,
      fill: { color: p.textLight, transparency: 94 },
    });
    slide.addShape(pres.shapes.OVAL, {
      x: -0.8, y: 4.0, w: 1.6, h: 1.6,
      fill: { color: p.textLight, transparency: 92 },
    });
    slide.addShape(pres.shapes.OVAL, {
      x: 8.8, y: 4.8, w: 0.6, h: 0.6,
      fill: { color: p.accent, transparency: 70 },
    });

    // 左侧双竖线 — accent + 辅助色
    slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: 1.0, y: 1.6, w: 0.07, h: 2.2,
      fill: { color: p.accent }, rectRadius: 0.035,
    });
    slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: 1.22, y: 1.85, w: 0.025, h: 1.7,
      fill: { color: p.secondary, transparency: 55 }, rectRadius: 0.01,
    });

    // 标题
    slide.addText(data.title, {
      x: 1.5, y: 1.6, w: 7.0, h: 1.1,
      fontSize: 38, fontFace: p.fonts.title, color: p.textLight,
      bold: true, align: "left", valign: "middle",
    });
    if (data.subtitle) {
      // 副标题上方细线
      slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x: 1.5, y: 2.78, w: 1.8, h: 0.025,
        fill: { color: p.accent, transparency: 40 }, rectRadius: 0.01,
      });
      slide.addText(data.subtitle, {
        x: 1.5, y: 2.88, w: 7.0, h: 0.55,
        fontSize: 15, fontFace: p.fonts.body, color: p.secondary,
        align: "left", valign: "top", lineSpacingMultiple: 1.4,
      });
    }

    // 底部 accent 双带
    slide.addShape(pres.shapes.RECTANGLE, {
      x: 0, y: 5.20, w: 10, h: 0.04, fill: { color: p.accent, transparency: 45 },
    });
    slide.addShape(pres.shapes.RECTANGLE, {
      x: 0, y: 5.28, w: 10, h: 0.06, fill: { color: p.accent },
    });
  },

  // ==================== text_image ====================
  async text_image(slide, data, p, pres) {
    contentSlideSetup(slide, data, p, pres);

    const isRight = (data.imagePosition || "right") === "right";
    const textX = isRight ? 0.6 : 5.3;
    const textW = 4.2;
    const imgX = isRight ? 5.5 : 0.4;
    const textAreaTop = 1.35;

    let curY = textAreaTop;

    // Text area left accent line
    slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: textX - 0.1, y: textAreaTop, w: 0.04, h: 3.5,
      fill: { color: p.accent, transparency: 45 }, rectRadius: 0.02,
    });

    if (data.body) {
      const { text, fontSize } = fitText(data.body, 80, 10, 13);
      const bodyH = 1.0;
      slide.addText(text, {
        x: textX, y: curY, w: textW, h: bodyH,
        fontSize, fontFace: p.fonts.body, color: p.textDark,
        lineSpacingMultiple: 1.5, valign: "top",
      });
      curY += bodyH + 0.35;
    }

    if (data.bullets && data.bullets.length) {
      const safeBullets = data.bullets.slice(0, 5);
      const bulletsH = textAreaTop + 3.9 - curY;
      slide.addText(
        safeBullets.map((b, i) => ({
          text: b,
          options: {
            bullet: { code: "25CF" },
            breakLine: i < safeBullets.length - 1,
            fontSize: 12, fontFace: p.fonts.body, color: p.textDark,
            bulletColor: p.accent,
          },
        })),
        { x: textX, y: curY, w: textW, h: bulletsH, valign: "top", paraSpaceAfter: 8 }
      );
    }

    // Image area — real image or layered decorative frame
    const imgW = 3.9;
    const imgH = 3.6;
    const imgY = 1.25;
    const hasSrc = !!(data.src && fs.existsSync(data.src));

    if (hasSrc) {
      // White card + shadow + real image
      slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x: imgX - 0.04, y: imgY - 0.04, w: imgW + 0.08, h: imgH + 0.08,
        fill: { color: "FFFFFF" },
        shadow: softShadow({ blur: 10, opacity: 0.1 }),
        rectRadius: 0.12,
      });
      slide.addImage({
        path: data.src,
        x: imgX, y: imgY, w: imgW, h: imgH,
        sizing: { type: "contain", w: imgW, h: imgH },
      });
    } else {
      // Outer subtle frame
      slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x: imgX - 0.06, y: imgY - 0.06, w: imgW + 0.12, h: imgH + 0.12,
        fill: { color: p.accent, transparency: 93 }, rectRadius: 0.14,
      });
      // Shadow offset layer
      slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x: imgX + 0.07, y: imgY + 0.07, w: imgW, h: imgH,
        fill: { color: p.accent, transparency: 84 }, rectRadius: 0.12,
      });
      // Main placeholder
      slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x: imgX, y: imgY, w: imgW, h: imgH,
        fill: { color: lighten(p.secondary, 0.5) },
        rectRadius: 0.12, shadow: softShadow(),
        line: { color: lighten(p.accent, 0.55), width: 0.8 },
      });

      if (data.imagePlaceholder) {
        const iconData = await resolveIcon(data.imageIcon || "FaImage", "#" + p.accent);
        if (iconData) {
          slide.addImage({
            data: iconData, x: imgX + 1.45, y: imgY + 0.85, w: 1.0, h: 1.0,
          });
        }
        // Decorative lines above and below caption
        slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
          x: imgX + 0.8, y: imgY + 2.0, w: 2.3, h: 0.02,
          fill: { color: p.accent, transparency: 65 }, rectRadius: 0.01,
        });
        slide.addText(data.imagePlaceholder, {
          x: imgX + 0.3, y: imgY + 2.1, w: imgW - 0.6, h: 0.7,
          fontSize: 10, fontFace: p.fonts.body, color: p.accent,
          align: "center", valign: "top",
        });
        slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
          x: imgX + 0.8, y: imgY + 2.85, w: 2.3, h: 0.02,
          fill: { color: p.accent, transparency: 65 }, rectRadius: 0.01,
        });
      }
    }
  },

  // ==================== cards ====================
  async cards(slide, data, p, pres) {
    contentSlideSetup(slide, data, p, pres);

    const cards = data.cards || [];
    const cols = Math.min(data.columns || cards.length, 4);
    const gap = 0.25;
    const totalW = 9.0;
    const cardW = (totalW - gap * (cols - 1)) / cols;
    const cardH = cols >= 4 ? 3.5 : 3.15;
    const startY = 1.35;
    const getColor = createColorCycler(p);
    const iconSpecs = [];

    for (let i = 0; i < cards.length; i++) {
      const col = i % cols;
      const row = Math.floor(i / cols);
      const x = 0.5 + col * (cardW + gap);
      const y = startY + row * (cardH + gap);
      const card = cards[i];
      const cc = getColor(i);

      slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x, y, w: cardW, h: cardH,
        fill: { color: "FFFFFF" },
        shadow: softShadow(),
        rectRadius: 0.12,
      });
      slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x: x + cardW * 0.12, y, w: cardW * 0.76, h: 0.05,
        fill: { color: cc }, rectRadius: 0.025,
      });

      const isHorizontal = data.cardStyle === "horizontal" && cols <= 3;
      if (isHorizontal) {
        const iconSize = cols === 3 ? 0.50 : 0.58;
        const iconX = x + 0.22;
        const iconCenterY = y + cardH / 2;
        iconSpecs.push(drawIconCircle(slide, pres, {
          cx: iconX + iconSize / 2, cy: iconCenterY,
          radius: iconSize / 2, color: cc,
          iconName: card.icon, iconColor: card.iconColor || "#FFFFFF",
          haloExtra: 0.05, haloTransparency: 80,
        }));
        const textX = iconX + iconSize + 0.18;
        const textW = x + cardW - textX - 0.15;
        slide.addText(card.title, {
          x: textX, y: y + 0.25, w: textW, h: 0.42,
          fontSize: 14, fontFace: p.fonts.title,
          color: p.textDark,
          bold: true, align: "left", valign: "middle", margin: 0,
        });
        if (card.desc) {
          const maxDesc = cols === 3 ? 35 : 45;
          const { text, fontSize } = fitText(card.desc, maxDesc, 8, 11);
          slide.addText(text, {
            x: textX, y: y + 0.72, w: textW,
            h: y + cardH - (y + 0.72) - 0.18,
            fontSize, fontFace: p.fonts.body,
            color: p.textMuted,
            align: "left", valign: "top", lineSpacingMultiple: 1.35,
          });
        }
      } else {
        const iconSize = cols >= 4 ? 0.48 : 0.56;
        const titleFs = cols >= 4 ? 13 : 15;
        const defaultDescFs = cols >= 4 ? 10 : 11;
        const descLines = card.desc ? Math.ceil(card.desc.length / (cols >= 4 ? 8 : 12)) : 0;
        const descH = Math.max(0.4, descLines * 0.25);

        const contentH = iconSize + 0.15 + 0.42 + (card.desc ? 0.1 + descH : 0);
        const contentStartY = y + (cardH - contentH) / 2;

        const iconY = contentStartY;
        iconSpecs.push(drawIconCircle(slide, pres, {
          cx: x + cardW / 2, cy: iconY + iconSize / 2,
          radius: iconSize / 2, color: cc,
          iconName: card.icon, iconColor: card.iconColor || "#FFFFFF",
          haloExtra: 0.06, haloTransparency: 80,
        }));

        const titleTop = iconY + iconSize + 0.15;
        slide.addText(card.title, {
          x: x + 0.12, y: titleTop, w: cardW - 0.24, h: 0.42,
          fontSize: titleFs, fontFace: p.fonts.title,
          color: p.textDark,
          bold: true, align: "center", margin: 0,
        });

        if (card.desc) {
          const maxDesc = cols >= 4 ? 25 : 40;
          const { text, fontSize } = fitText(card.desc, maxDesc, 8, defaultDescFs);
          const descTop = titleTop + 0.52;
          slide.addText(text, {
            x: x + 0.15, y: descTop, w: cardW - 0.3,
            h: y + cardH - descTop - 0.15,
            fontSize, fontFace: p.fonts.body, color: p.textMuted,
            align: "center", valign: "top", lineSpacingMultiple: 1.35,
          });
        }
      }
    }
    await batchAddIconImages(slide, pres, iconSpecs);
  },

  // ==================== stats ====================
  async stats(slide, data, p, pres) {
    contentSlideSetup(slide, data, p, pres);
    const stats = data.stats || [];
    const cols = Math.min(stats.length, 4);
    const gap = 0.3;
    const boxW = (9.0 - gap * (cols - 1)) / cols;
    const boxH = 3.0;
    const boxY = 1.5;
    const getColor = createColorCycler(p);
    const iconSpecs = [];

    for (let i = 0; i < stats.length; i++) {
      const stat = stats[i];
      const cc = getColor(i);
      const x = 0.5 + i * (boxW + gap);

      slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x, y: boxY, w: boxW, h: boxH,
        fill: { color: "FFFFFF" },
        shadow: softShadow(),
        rectRadius: 0.12,
      });
      slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x: x + boxW * 0.15, y: boxY + boxH - 0.08, w: boxW * 0.7, h: 0.06,
        fill: { color: cc }, rectRadius: 0.03,
      });

      const iconBg = 0.58;
      iconSpecs.push(drawIconCircle(slide, pres, {
        cx: x + boxW / 2, cy: boxY + 0.25 + iconBg / 2,
        radius: iconBg / 2, color: cc,
        iconName: stat.icon, iconColor: stat.iconColor || "#FFFFFF",
        haloExtra: 0.05, haloTransparency: 80,
      }));

      const numFs = statValueFontSize(stat.value, cols);
      slide.addText(String(stat.value), {
        x, y: boxY + 1.0, w: boxW, h: 0.85,
        fontSize: numFs,
        fontFace: p.fonts.title,
        color: p.primary,
        bold: true, align: "center", valign: "middle", margin: 0,
      });

      slide.addText(stat.label, {
        x: x + 0.1, y: boxY + 1.95, w: boxW - 0.2, h: 0.55,
        fontSize: 11,
        fontFace: p.fonts.body,
        color: p.textMuted,
        align: "center", valign: "top", margin: 0, lineSpacingMultiple: 1.2,
      });

      // 可选配图区
      if (stat.imagePlaceholder) {
        const imgSize = 0.35;
        const imgData = await resolveIcon(stat.imageIcon || "FaImage", "#" + p.secondary);
        if (imgData) {
          slide.addImage({ data: imgData,
            x: x + boxW / 2 - imgSize / 2, y: boxY + 2.55, w: imgSize, h: imgSize,
          });
        }
      }
    }
    await batchAddIconImages(slide, pres, iconSpecs);
  },

  // ==================== comparison ====================
  async comparison(slide, data, p, pres) {
    contentSlideSetup(slide, data, p, pres);

    const rows = data.rows || [];
    const colW = 3.3;
    const dimW = 2.0;
    const rowH = 0.50;
    const tableX = 0.6;
    const tableY = 1.45;
    const maxRows = Math.min(rows.length, 8);

    // 表头
    slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: tableX, y: tableY, w: 8.8, h: 0.52,
      fill: { color: p.primary }, rectRadius: 0.08,
    });
    slide.addText(data.leftLabel || "A", {
      x: tableX + dimW, y: tableY + 0.02, w: colW, h: 0.48,
      fontSize: 14, fontFace: p.fonts.title, color: p.textLight,
      bold: true, align: "center", margin: 0,
    });
    slide.addText(data.rightLabel || "B", {
      x: tableX + dimW + colW + 0.2, y: tableY + 0.02, w: colW, h: 0.48,
      fontSize: 14, fontFace: p.fonts.title, color: p.accent,
      bold: true, align: "center", margin: 0,
    });

    for (let i = 0; i < maxRows; i++) {
      const row = rows[i];
      const y = tableY + 0.60 + i * rowH;
      slide.addShape(pres.shapes.RECTANGLE, {
        x: tableX, y, w: 8.8, h: rowH,
        fill: { color: i % 2 === 0 ? "F8FAFC" : "FFFFFF" },
      });
      slide.addShape(pres.shapes.RECTANGLE, {
        x: tableX, y: y + 0.06, w: 0.035, h: rowH - 0.12,
        fill: { color: p.accent },
      });
      slide.addText(row.dimension, {
        x: tableX + 0.12, y, w: dimW - 0.2, h: rowH,
        fontSize: 12, fontFace: p.fonts.body, color: p.textDark,
        bold: true, valign: "middle", margin: 0,
      });
      slide.addText(row.left, {
        x: tableX + dimW, y, w: colW, h: rowH,
        fontSize: 12, fontFace: p.fonts.body, color: p.textDark,
        align: "center", valign: "middle", margin: 0,
      });
      slide.addText(row.right, {
        x: tableX + dimW + colW + 0.2, y, w: colW, h: rowH,
        fontSize: 12, fontFace: p.fonts.body, color: p.textDark,
        align: "center", valign: "middle", margin: 0,
      });
    }
  },

  // ==================== timeline ====================
  async timeline(slide, data, p, pres) {
    contentSlideSetup(slide, data, p, pres);

    const steps = data.steps || [];
    const count = steps.length;
    const compact = count >= 5;
    const getColor = createColorCycler(p);

    if (compact) {
      const lineY = 2.75;
      const startX = 0.8;
      const endX = 9.2;
      const totalLen = endX - startX;
      const nodeW = 1.55;
      slide.addShape(pres.shapes.LINE, {
        x: startX, y: lineY, w: totalLen, h: 0,
        line: { color: lighten(p.secondary, 0.15), width: 2.5 },
      });
      for (let i = 0; i < count; i++) {
        const step = steps[i];
        const cx = count === 1 ? startX + totalLen / 2 : startX + (totalLen / (count - 1)) * i;
        const cc = getColor(i);
        slide.addShape(pres.shapes.OVAL, {
          x: cx - 0.14, y: lineY - 0.14, w: 0.28, h: 0.28,
          fill: { color: cc, transparency: 60 },
        });
        slide.addShape(pres.shapes.OVAL, {
          x: cx - 0.09, y: lineY - 0.09, w: 0.18, h: 0.18,
          fill: { color: cc },
        });
        slide.addText(step.time || "", {
          x: cx - nodeW / 2, y: lineY - 0.72, w: nodeW, h: 0.35,
          fontSize: 11, fontFace: p.fonts.title, color: cc,
          bold: true, align: "center", margin: 0,
        });
        slide.addText(step.title || "", {
          x: cx - nodeW / 2, y: lineY + 0.22, w: nodeW, h: 0.32,
          fontSize: 10, fontFace: p.fonts.title, color: p.textDark,
          bold: true, align: "center", margin: 0,
        });
        if (step.desc) slide.addText(step.desc, {
          x: cx - nodeW / 2, y: lineY + 0.55, w: nodeW, h: 0.80,
          fontSize: 9, fontFace: p.fonts.body, color: p.textMuted,
          align: "center", valign: "top", margin: 0, lineSpacingMultiple: 1.2,
        });
      }
    } else {
      // 宽松模式 — 时间轴下移，避免与标题冲突
      const lineY = 3.1;
      const startX = 1.3;
      const endX = 8.7;
      const totalLen = endX - startX;
      slide.addShape(pres.shapes.LINE, {
        x: startX, y: lineY, w: totalLen, h: 0,
        line: { color: lighten(p.secondary, 0.2), width: 2.5 },
      });

      const cardW = 1.9;
      const cardH = 1.4;
      for (let i = 0; i < count; i++) {
        const step = steps[i];
        const cx = count === 1 ? startX + totalLen / 2 : startX + (totalLen / (count - 1)) * i;
        const above = i % 2 === 0;
        const cc = getColor(i);

        const connY = above ? lineY - 0.32 : lineY + 0.08;
        slide.addShape(pres.shapes.LINE, {
          x: cx, y: connY, w: 0, h: 0.32,
          line: { color: cc, width: 1.5 },
        });
        slide.addShape(pres.shapes.OVAL, {
          x: cx - 0.13, y: lineY - 0.13, w: 0.26, h: 0.26,
          fill: { color: cc, transparency: 55 },
        });
        slide.addShape(pres.shapes.OVAL, {
          x: cx - 0.09, y: lineY - 0.09, w: 0.18, h: 0.18,
          fill: { color: cc },
        });

        const cardY = above ? lineY - 0.42 - cardH : lineY + 0.42;
        slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
          x: cx - cardW / 2, y: cardY, w: cardW, h: cardH,
          fill: { color: "FFFFFF" }, shadow: softShadow(), rectRadius: 0.1,
        });
        slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
          x: cx - cardW / 2 + 0.2, y: cardY, w: cardW - 0.4, h: 0.045,
          fill: { color: cc }, rectRadius: 0.02,
        });
        slide.addText(step.time || "", {
          x: cx - cardW / 2, y: cardY + 0.1, w: cardW, h: 0.28,
          fontSize: 11, fontFace: p.fonts.title, color: cc,
          bold: true, align: "center", margin: 0,
        });
        slide.addText(step.title || "", {
          x: cx - cardW / 2 + 0.08, y: cardY + 0.38, w: cardW - 0.16, h: 0.28,
          fontSize: 10, fontFace: p.fonts.title, color: p.textDark,
          bold: true, align: "center", margin: 0,
        });
        if (step.desc) slide.addText(step.desc, {
          x: cx - cardW / 2 + 0.08, y: cardY + 0.68, w: cardW - 0.16, h: cardH - 0.78,
          fontSize: 9, fontFace: p.fonts.body, color: p.textMuted,
          align: "center", valign: "top", margin: 0, lineSpacingMultiple: 1.15,
        });
      }
    }
  },

  // ==================== chart ====================
  async chart(slide, data, p, pres) {
    contentSlideSetup(slide, data, p, pres);

    const chartTypeMap = {
      bar: pres.charts.BAR, line: pres.charts.LINE,
      pie: pres.charts.PIE, doughnut: pres.charts.DOUGHNUT,
    };
    const ct = chartTypeMap[data.chartType || "bar"] || pres.charts.BAR;
    const cd = (data.data.series || []).map((s) => ({
      name: s.name, labels: data.data.labels, values: s.values,
    }));
    const isPie = ["pie", "doughnut"].includes(data.chartType);
    const colors = [p.primary, p.accent, "0EA5E9", "8B5CF6", "F59E0B", "10B981"];

    slide.addChart(ct, cd, {
      x: 0.5, y: 1.2, w: 9, h: 3.8, barDir: "col",
      chartColors: isPie ? [p.primary, p.accent, "0EA5E9", "8B5CF6", "94A3B8", "CBD5E1"] : colors,
      chartArea: { fill: { color: p.bg }, roundedCorners: true },
      catAxisLabelColor: p.textMuted, valAxisLabelColor: p.textMuted,
      catAxisLabelFontSize: 10, valAxisLabelFontSize: 10,
      valGridLine: { color: "E2E8F0", size: 0.5 }, catGridLine: { style: "none" },
      showValue: !isPie, showPercent: isPie,
      dataLabelPosition: isPie ? "bestFit" : "outEnd",
      dataLabelColor: p.textDark,
      showLegend: cd.length > 1 || isPie, legendPos: "b", legendFontSize: 10,
    });
    if (data.note) slide.addText(data.note, {
      x: 0.6, y: 5.1, w: 8.8, h: 0.35, fontSize: 10, fontFace: p.fonts.body,
      color: p.textMuted, italic: true,
    });
  },

  // ==================== quote ====================
  async quote(slide, data, p, pres) {
    slide.background = { color: p.primary };

    // 多层装饰圆
    slide.addShape(pres.shapes.OVAL, {
      x: -1.2, y: -2.2, w: 5.0, h: 5.0,
      fill: { color: p.textLight, transparency: 96 },
    });
    slide.addShape(pres.shapes.OVAL, {
      x: 7.5, y: 2.8, w: 4.5, h: 4.5,
      fill: { color: p.accent, transparency: 88 },
    });
    slide.addShape(pres.shapes.OVAL, {
      x: 8.5, y: -0.8, w: 1.5, h: 1.5,
      fill: { color: p.textLight, transparency: 93 },
    });
    slide.addShape(pres.shapes.OVAL, {
      x: 0.5, y: 4.2, w: 0.6, h: 0.6,
      fill: { color: p.accent, transparency: 70 },
    });

    // 底部 accent
    slide.addShape(pres.shapes.RECTANGLE, {
      x: 0, y: 5.28, w: 10, h: 0.06, fill: { color: p.accent },
    });

    // 大开引号
    slide.addText("\u201C", {
      x: 0.6, y: -0.3, w: 2.0, h: 2.0,
      fontSize: 120, fontFace: "Georgia", color: p.accent,
      bold: true, align: "left", valign: "top", margin: 0,
      transparency: 15,
    });
    // 小闭引号
    slide.addText("\u201D", {
      x: 7.5, y: 3.0, w: 1.5, h: 1.5,
      fontSize: 80, fontFace: "Georgia", color: p.accent,
      bold: true, align: "right", valign: "bottom", margin: 0,
      transparency: 30,
    });

    const { text: quoteText, fontSize: quoteFontSize } = fitText(data.body, 60, 16, 24);
    slide.addText(quoteText, {
      x: 1.5, y: 1.0, w: 7.0, h: 2.2,
      fontSize: quoteFontSize, fontFace: p.fonts.body, color: p.textLight,
      italic: true, align: "center", valign: "middle",
      lineSpacingMultiple: 1.5,
    });

    slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: 3.6, y: 3.4, w: 2.8, h: 0.04,
      fill: { color: p.accent }, rectRadius: 0.02,
    });
    if (data.author) slide.addText(data.author, {
      x: 1.5, y: 3.6, w: 7.0, h: 0.5,
      fontSize: 13, fontFace: p.fonts.body, color: p.secondary,
      align: "center", valign: "top", margin: 0,
    });
  },

  // ==================== big_number ====================
  async big_number(slide, data, p, pres) {
    slide.background = { color: p.bg };
    const deco = nextDeco();
    addContentBg(slide, p, pres, _bgVariant++);
    applyDeco(slide, deco, p, pres);

    // 装饰
    slide.addShape(pres.shapes.OVAL, {
      x: -1.5, y: 1.2, w: 3.2, h: 3.2,
      fill: { color: p.secondary, transparency: 72 },
    });
    slide.addShape(pres.shapes.OVAL, {
      x: 8.3, y: 0.5, w: 3.0, h: 3.0,
      fill: { color: p.accent, transparency: 88 },
    });

    if (data.title) slide.addText(data.title, {
      x: 0.6, y: 0.35, w: 8.8, h: 0.60,
      fontSize: 28, fontFace: p.fonts.title, color: p.textDark, bold: true,
      align: "center", margin: 0,
    });

    const valLen = String(data.value).length;
    let valFs;
    if (valLen > 10) valFs = 48;
    else if (valLen > 8) valFs = 56;
    else if (valLen > 6) valFs = 66;
    else valFs = 80;
    slide.addText(String(data.value), {
      x: 1.2, y: 1.2, w: 7.6, h: 1.8,
      fontSize: valFs, fontFace: p.fonts.title, color: p.accent,
      bold: true, align: "center", valign: "middle", margin: 0,
    });
    slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: 3.0, y: 3.1, w: 4.0, h: 0.045,
      fill: { color: p.accent, transparency: 40 }, rectRadius: 0.02,
    });
    if (data.label) slide.addText(data.label, {
      x: 1.2, y: 3.3, w: 7.6, h: 0.55,
      fontSize: 16, fontFace: p.fonts.body, color: p.textDark,
      align: "center", valign: "middle", margin: 0,
    });
    if (data.desc) slide.addText(data.desc, {
      x: 1.2, y: 3.9, w: 7.6, h: 0.65,
      fontSize: 12, fontFace: p.fonts.body, color: p.textMuted,
      align: "center", valign: "top", lineSpacingMultiple: 1.3,
    });
  },

  // ==================== process ====================
  async process(slide, data, p, pres) {
    contentSlideSetup(slide, data, p, pres);

    const steps = data.steps || [];
    const count = Math.min(steps.length, 6);
    const startX = 1.2;
    const startY = 1.4;
    const stepH = Math.min(0.76, 3.8 / count);
    const gap = 0.1;
    const getColor = createColorCycler(p);

    for (let i = 0; i < count; i++) {
      const step = steps[i];
      const y = startY + i * (stepH + gap);
      const isLast = i === count - 1;
      const cc = getColor(i);

      if (!isLast) {
        slide.addShape(pres.shapes.LINE, {
          x: startX + 0.24, y: y + 0.52, w: 0, h: stepH + gap - 0.1,
          line: { color: lighten(p.secondary, 0.3), width: 2 },
        });
      }
      const cs = 0.48;
      const circleColor = i === 0 ? p.accent : cc;
      drawColoredCircle(slide, pres, {
        cx: startX + cs / 2, cy: y + stepH / 2,
        radius: cs / 2, color: circleColor,
        haloExtra: 0.03, haloTransparency: 72,
      });
      slide.addText(`${i + 1}`, {
        x: startX, y: y + stepH / 2 - cs / 2, w: cs, h: cs,
        fontSize: 15, fontFace: p.fonts.title, color: p.textLight,
        bold: true, align: "center", valign: "middle",
      });

      const cardX = startX + 0.78;
      const cardW = 7.0;
      slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x: cardX, y: y + 0.02, w: cardW, h: stepH - 0.04,
        fill: { color: "FFFFFF" }, shadow: softShadow({ blur: 5, opacity: 0.05 }),
        rectRadius: 0.1,
      });
      slide.addShape(pres.shapes.RECTANGLE, {
        x: cardX, y: y + 0.1, w: 0.04, h: stepH - 0.2,
        fill: { color: cc },
      });
      slide.addText(step.title, {
        x: cardX + 0.2, y: y + 0.02, w: cardW - 0.4, h: stepH * 0.5,
        fontSize: 14, fontFace: p.fonts.title, color: p.textDark,
        bold: true, valign: "middle", margin: 0,
      });
      if (step.desc) {
        const { text, fontSize } = fitText(step.desc, 50, 9, 11);
        slide.addText(text, {
          x: cardX + 0.2, y: y + stepH * 0.46, w: cardW - 0.4, h: stepH * 0.54,
          fontSize, fontFace: p.fonts.body, color: p.textMuted,
          valign: "top", margin: 0, lineSpacingMultiple: 1.25,
        });
      }
    }
  },

  // ==================== numbered_list ====================
  async numbered_list(slide, data, p, pres) {
    contentSlideSetup(slide, data, p, pres);

    const items = data.items || [];
    const maxItems = Math.min(items.length, 7);
    const startY = 1.3;
    const rowH = Math.min(0.60, 3.9 / maxItems);
    const gap = 0.08;
    const getColor = createColorCycler(p);
    const bgColors = deriveCardBgColors(p);

    for (let i = 0; i < maxItems; i++) {
      const item = items[i];
      const y = startY + i * (rowH + gap);
      const cc = getColor(i);

      slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x: 0.6, y, w: 8.8, h: rowH,
        fill: { color: bgColors[i % bgColors.length] },
        shadow: makeShadow({ blur: 2, opacity: 0.03 }),
        rectRadius: 0.08,
      });
      slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x: 0.6, y: y + 0.06, w: 0.04, h: rowH - 0.12,
        fill: { color: cc }, rectRadius: 0.02,
      });
      slide.addShape(pres.shapes.OVAL, {
        x: 0.95, y: y + rowH / 2 - 0.17, w: 0.34, h: 0.34,
        fill: { color: cc },
      });
      slide.addText(`${i + 1}`, {
        x: 0.95, y: y + rowH / 2 - 0.17, w: 0.34, h: 0.34,
        fontSize: 11, fontFace: p.fonts.title, color: p.textLight,
        bold: true, align: "center", valign: "middle",
      });
      const textX = 1.55;
      if (item.title) {
        slide.addText(item.title, {
          x: textX, y, w: item.desc ? 3.5 : 7.5, h: rowH,
          fontSize: 14, fontFace: p.fonts.body, color: p.textDark,
          bold: true, valign: "middle", margin: 0,
        });
      }
      if (item.desc) {
        const descX = item.title ? 5.2 : textX;
        const descW = item.title ? 4.0 : 7.5;
        const maxDesc = item.title ? 28 : 60;
        const { text, fontSize } = fitText(item.desc, maxDesc, 8, 12);
        slide.addText(text, {
          x: descX, y, w: descW, h: rowH,
          fontSize, fontFace: p.fonts.body, color: p.textMuted,
          valign: "middle", margin: 0,
        });
      }
    }
  },

  // ==================== two_column ====================
  async two_column(slide, data, p, pres) {
    contentSlideSetup(slide, data, p, pres);

    const left = data.left || {};
    const right = data.right || {};
    const colW = 4.1;
    const startY = 1.35;

    slide.addShape(pres.shapes.LINE, {
      x: 5.0, y: startY + 0.1, w: 0, h: 3.6,
      line: { color: lighten(p.secondary, 0.3), width: 1 },
    });

    for (const [colData, colX] of [[left, 0.55], [right, 5.3]]) {
      let curY = startY;
      if (colData.subtitle) {
        slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
          x: colX, y: curY, w: colW, h: 0.38,
          fill: { color: lighten(p.accent, 0.88) }, rectRadius: 0.06,
        });
        slide.addText(colData.subtitle, {
          x: colX + 0.15, y: curY, w: colW - 0.3, h: 0.38,
          fontSize: 14, fontFace: p.fonts.title, color: p.accent,
          bold: true, valign: "middle", margin: 0,
        });
        curY += 0.48;
      }
      if (colData.body) {
        slide.addText(colData.body, {
          x: colX, y: curY, w: colW, h: 0.9,
          fontSize: 12, fontFace: p.fonts.body, color: p.textDark,
          lineSpacingMultiple: 1.5, valign: "top",
        });
        curY += 0.95;
      }
      if (colData.bullets && colData.bullets.length) {
        slide.addText(
          colData.bullets.map((b, i) => ({
            text: b,
            options: {
              bullet: { code: "25CF" },
              breakLine: i < colData.bullets.length - 1,
              fontSize: 11, fontFace: p.fonts.body, color: p.textDark,
              bulletColor: p.accent,
            },
          })),
          {
            x: colX, y: curY, w: colW,
            h: Math.max(0.5, startY + 4.0 - curY),
            valign: "top", paraSpaceAfter: 6,
          }
        );
      }
    }
  },

  // ==================== icon_list ====================
  async icon_list(slide, data, p, pres) {
    contentSlideSetup(slide, data, p, pres);

    const items = data.items || [];
    const maxItems = Math.min(items.length, 6);
    const startY = 1.35;
    const gap = maxItems >= 5 ? 0.05 : 0.08;
    const rowH = Math.min(0.78, 3.85 / maxItems);
    const getColor = createColorCycler(p);
    const iconSpecs = [];

    for (let i = 0; i < maxItems; i++) {
      const item = items[i];
      const y = startY + i * (rowH + gap);
      const cc = getColor(i);

      slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x: 0.6, y, w: 8.8, h: rowH,
        fill: { color: "FFFFFF" },
        shadow: softShadow({ blur: 5, opacity: 0.05 }),
        rectRadius: 0.1,
      });

      const iconSize = 0.52;
      const iconCX = 0.9 + iconSize / 2;
      const iconCY = y + rowH / 2;
      iconSpecs.push(drawIconCircle(slide, pres, {
        cx: iconCX, cy: iconCY, radius: iconSize / 2, color: cc,
        iconName: item.icon, iconColor: item.iconColor || "#FFFFFF",
        haloExtra: 0.04, haloTransparency: 75,
      }));

      const textX = 1.7;
      slide.addText(item.title || "", {
        x: textX, y, w: 7.5, h: item.desc ? rowH * 0.52 : rowH,
        fontSize: 14, fontFace: p.fonts.title, color: p.textDark,
        bold: true, valign: item.desc ? "bottom" : "middle", margin: 0,
      });
      if (item.desc) {
        const maxDescChars = maxItems >= 5 ? 35 : 50;
        const { text, fontSize } = fitText(item.desc, maxDescChars, 8, 11);
        slide.addText(text, {
          x: textX, y: y + rowH * 0.5, w: 7.5, h: rowH * 0.5,
          fontSize, fontFace: p.fonts.body, color: p.textMuted,
          valign: "top", margin: 0,
        });
      }
    }
    await batchAddIconImages(slide, pres, iconSpecs);
  },

  // ==================== highlight_box ====================
  async highlight_box(slide, data, p, pres) {
    contentSlideSetup(slide, data, p, pres);

    const boxX = 0.8;
    const boxY = 1.5;
    const boxW = 8.4;
    const boxH = 3.2;
    // 阴影底层
    slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: boxX + 0.06, y: boxY + 0.06, w: boxW, h: boxH,
      fill: { color: darken(p.primary, 0.3), transparency: 70 },
      rectRadius: 0.18,
    });
    // 主卡片
    slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: boxX, y: boxY, w: boxW, h: boxH,
      fill: { color: p.primary },
      shadow: softShadow({ blur: 18, opacity: 0.12 }),
      rectRadius: 0.18,
    });
    // 左侧 accent 竖线
    slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: boxX + 0.3, y: boxY + 0.5, w: 0.06, h: boxH - 1.0,
      fill: { color: p.accent }, rectRadius: 0.03,
    });

    if (data.icon) {
      const iconData = await resolveIcon(data.icon, "#" + p.accent);
      if (iconData) slide.addImage({
        data: iconData, x: boxX + 0.65, y: boxY + 0.5, w: 0.65, h: 0.65,
      });
    }

    const textX = data.icon ? boxX + 1.5 : boxX + 0.7;
    const textW = boxW - (textX - boxX) - (data.imagePlaceholder ? 1.8 : 0.4);
    slide.addText(data.heading || "", {
      x: textX, y: boxY + 0.35, w: textW, h: 0.7,
      fontSize: 22, fontFace: p.fonts.title, color: p.textLight,
      bold: true, valign: "middle", margin: 0,
    });
    if (data.body) slide.addText(data.body, {
      x: textX, y: boxY + 1.15, w: textW, h: boxH - 1.6,
      fontSize: 13, fontFace: p.fonts.body, color: p.secondary,
      valign: "top", lineSpacingMultiple: 1.5, margin: 0,
    });
    // 可选右侧配图区
    if (data.imagePlaceholder) {
      const imgSize = 1.0;
      const imgX = boxX + boxW - 1.6;
      const imgY = boxY + (boxH - imgSize) / 2;
      const imgIcon = await resolveIcon(data.imageIcon || "FaImage", "#" + p.accent);
      if (imgIcon) {
        slide.addImage({ data: imgIcon, x: imgX, y: imgY, w: imgSize, h: imgSize });
      }
      slide.addText(data.imagePlaceholder, {
        x: imgX - 0.2, y: imgY + 1.05, w: imgSize + 0.4, h: 0.4,
        fontSize: 9, fontFace: p.fonts.body, color: p.secondary, align: "center",
      });
    }
    if (data.footnote) slide.addText(data.footnote, {
      x: boxX + 0.3, y: boxY + boxH - 0.45, w: boxW - 0.6, h: 0.35,
      fontSize: 10, fontFace: p.fonts.body, color: p.textMuted,
      align: "right", valign: "middle", margin: 0,
    });
  },

  // ==================== image ====================
  async image(slide, data, p, pres) {
    slide.background = { color: p.bg };

    const hasSrc = !!(data.src && fs.existsSync(data.src));
    const hasTitle = !!data.title;

    // 布局参数：无标题时图片区域占满全页
    const margin = 0.4;
    const imgX = margin;
    const imgW = 10 - margin * 2; // 9.2"
    let imgY = margin;
    let imgH = 5.625 - margin * 2; // ~4.825"

    if (hasTitle) {
      // 小标题栏 — 仅占 0.55"，图片区下移
      slide.addText(data.title, {
        x: 0.6, y: 0.2, w: 8.8, h: 0.45,
        fontSize: 18, fontFace: p.fonts.title, color: p.textDark,
        bold: true, valign: "middle",
      });
      imgY = 0.75;
      imgH = 5.625 - imgY - margin - 0.05; // ~4.425"
    }

    // 有 caption 时底部预留空间
    if (data.caption) {
      imgH -= 0.55;
    }

    if (hasSrc) {
      // 白色卡片 + 阴影 + 嵌入图片
      slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x: imgX - 0.03, y: imgY - 0.03, w: imgW + 0.06, h: imgH + 0.06,
        fill: { color: "FFFFFF" },
        shadow: softShadow({ blur: 12, opacity: 0.12 }),
        rectRadius: 0.06,
      });
      slide.addImage({
        path: data.src,
        x: imgX, y: imgY, w: imgW, h: imgH,
        sizing: { type: "contain", w: imgW, h: imgH },
      });
    } else {
      // 占位模式
      slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x: imgX - 0.05, y: imgY - 0.05, w: imgW + 0.1, h: imgH + 0.1,
        fill: { color: p.secondary, transparency: 75 },
        rectRadius: 0.08,
      });
      slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x: imgX + 0.03, y: imgY + 0.03, w: imgW, h: imgH,
        fill: { color: lighten(p.primary, 0.88), transparency: 55 },
        rectRadius: 0.06,
      });
      slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x: imgX, y: imgY, w: imgW, h: imgH,
        fill: { color: lighten(p.bg, 0.97) },
        line: { color: p.secondary, width: 1.2, transparency: 60 },
        rectRadius: 0.06,
        shadow: softShadow({ blur: 8, opacity: 0.06 }),
      });

      const iconName = data.imageIcon || "FaImage";
      const iconData = await resolveIcon(iconName, "#" + p.accent);
      if (iconData) {
        slide.addImage({
          data: iconData,
          x: imgX + imgW / 2 - 0.6, y: imgY + imgH / 2 - 0.85,
          w: 1.2, h: 1.2,
        });
      }
      if (data.imagePlaceholder) {
        slide.addText(data.imagePlaceholder, {
          x: imgX + 1.0, y: imgY + imgH / 2 + 0.45, w: imgW - 2.0, h: 0.55,
          fontSize: 14, fontFace: p.fonts.body, color: p.textMuted,
          align: "center", valign: "top",
        });
      }
    }

    // 底部说明
    if (data.caption) {
      slide.addText(data.caption, {
        x: imgX, y: imgY + imgH + 0.1, w: imgW, h: 0.45,
        fontSize: 12, fontFace: p.fonts.body, color: p.textMuted,
        align: "center", italic: true,
      });
    }
  },

  // ==================== ending ====================
  async ending(slide, data, p, pres) {
    slide.background = { color: p.primary };

    // 多层装饰
    slide.addShape(pres.shapes.OVAL, {
      x: 6.8, y: -2.0, w: 5.5, h: 5.5,
      fill: { color: p.textLight, transparency: 95 },
    });
    slide.addShape(pres.shapes.OVAL, {
      x: 7.5, y: -1.0, w: 4.0, h: 4.0,
      fill: { color: p.textLight, transparency: 93 },
    });
    slide.addShape(pres.shapes.OVAL, {
      x: -1.2, y: 3.5, w: 3.0, h: 3.0,
      fill: { color: p.accent, transparency: 83 },
    });
    slide.addShape(pres.shapes.OVAL, {
      x: 0.3, y: -0.8, w: 1.2, h: 1.2,
      fill: { color: p.textLight, transparency: 94 },
    });
    slide.addShape(pres.shapes.OVAL, {
      x: 9.0, y: 4.2, w: 0.6, h: 0.6,
      fill: { color: p.accent, transparency: 60 },
    });

    // 顶底 accent
    slide.addShape(pres.shapes.RECTANGLE, {
      x: 0, y: 0, w: 10, h: 0.055, fill: { color: p.accent },
    });
    slide.addShape(pres.shapes.RECTANGLE, {
      x: 0, y: 5.28, w: 10, h: 0.055, fill: { color: p.accent },
    });

    slide.addText(data.title || "Thank You", {
      x: 1, y: 1.0, w: 8, h: 1.4,
      fontSize: 46, fontFace: p.fonts.title, color: p.textLight,
      bold: true, align: "center", valign: "middle",
    });
    slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: 3.0, y: 2.55, w: 4.0, h: 0.045,
      fill: { color: p.accent }, rectRadius: 0.02,
    });
    if (data.subtitle) slide.addText(data.subtitle, {
      x: 1, y: 2.8, w: 8, h: 0.65,
      fontSize: 20, fontFace: p.fonts.body, color: p.secondary,
      align: "center", charSpacing: 6,
    });
    if (data.contact) slide.addText(data.contact, {
      x: 1, y: 3.8, w: 8, h: 0.5,
      fontSize: 14, fontFace: p.fonts.body, color: p.textMuted, align: "center",
    });
  },
};

// ==================== 内置主题配色 ====================
const THEMES = {
  academic: {
    primary: "1E2761", secondary: "CADCFC", accent: "F96167",
    bg: "FAFAFA", textDark: "1E293B", textLight: "FFFFFF", textMuted: "64748B",
    fonts: { title: "Georgia", body: "Calibri" },
  },
  business: {
    primary: "1E2761", secondary: "CADCFC", accent: "F96167",
    bg: "FFFFFF", textDark: "1E293B", textLight: "FFFFFF", textMuted: "64748B",
    fonts: { title: "Arial Black", body: "Arial" },
  },
  tech: {
    primary: "065A82", secondary: "1C7293", accent: "02C39A",
    bg: "F5FAFC", textDark: "0F172A", textLight: "FFFFFF", textMuted: "475569",
    fonts: { title: "Arial Black", body: "Arial" },
  },
  ink: {
    primary: "2C3E50", secondary: "D4C5A9", accent: "B84040",
    bg: "FAF7F2", textDark: "2C2416", textLight: "FFFFFF", textMuted: "8B7355",
    fonts: { title: "Georgia", body: "Calibri" },
  },
  politics: {
    primary: "8B0000", secondary: "F5E6D3", accent: "DAA520",
    bg: "FFFAF5", textDark: "2C1810", textLight: "FFFFFF", textMuted: "8B6914",
    fonts: { title: "Arial Black", body: "Arial" },
  },
  minimal: {
    primary: "36454F", secondary: "F2F2F2", accent: "212121",
    bg: "FFFFFF", textDark: "1A1A1A", textLight: "FFFFFF", textMuted: "6B7280",
    fonts: { title: "Trebuchet MS", body: "Calibri" },
  },
};

// ==================== JSON 预处理 ====================
function sanitizeJson(raw) {
  let fixed = raw;
  // 中文引号修复：LLM 生成的 JSON 中，中文字段值内常使用 ASCII " 作为中文引号（如 "被称为"死亡走廊"")
  // 关键洞察：中文引号内部不含 JSON 结构符（, : [ ] { }），以此区分正当 JSON 边界
  // 模式1：中文引号两侧均为中文字符（如 为"死" → 为「死」）
  const p1 = /([\u4e00-\u9fff])"([^"',:\[\]{}]{1,30})"([\u4e00-\u9fff])/g;
  // 模式2：中文引号右侧紧接 JSON " 分隔符（如 廊"" → 廊」"）
  const p2 = /([\u4e00-\u9fff])"([^"',:\[\]{}]{1,30})""/g;
  // 循环直到收敛（处理重叠匹配如 "从"甲"到"乙"的"）
  let prev;
  do { prev = fixed; fixed = fixed.replace(p1, "$1「$2」$3"); } while (fixed !== prev);
  do { prev = fixed; fixed = fixed.replace(p2, '$1「$2」"'); } while (fixed !== prev);
  return fixed;
}

// ==================== 主渲染函数 ====================
async function renderPPT(jsonPath, outputPath) {
  const raw = fs.readFileSync(jsonPath, "utf-8");
  let input;
  try {
    input = JSON.parse(sanitizeJson(raw));
  } catch (e) {
    // 定位错误行号并输出上下文
    const posMatch = e.message.match(/position\s+(\d+)/);
    if (posMatch) {
      const pos = parseInt(posMatch[1]);
      const lines = raw.substring(0, pos).split("\n");
      const lineNum = lines.length;
      const col = lines[lines.length - 1].length + 1;
      const allLines = raw.split("\n");
      const ctxStart = Math.max(0, lineNum - 3);
      const ctxEnd = Math.min(allLines.length, lineNum + 2);
      console.error(`\nJSON 解析错误: 第 ${lineNum} 行, 第 ${col} 列\n`);
      for (let i = ctxStart; i < ctxEnd; i++) {
        const marker = i === lineNum - 1 ? ">>>" : "   ";
        console.error(`${marker} ${i + 1} | ${allLines[i]}`);
      }
      console.error(`\n错误信息: ${e.message}`);
      console.error("💡 提示: 如错误由中文引号引起，sanitizeJson() 已自动修复常见模式，但可能需要手动检查。");
    } else {
      console.error("JSON 解析错误:", e.message);
    }
    process.exit(1);
  }
  const meta = input.meta;
  const theme = THEMES[meta.theme] || {};
  const palette = {
    primary:   meta.palette?.primary   || theme.primary   || "1E2761",
    secondary: meta.palette?.secondary || theme.secondary || "CADCFC",
    accent:    meta.palette?.accent    || theme.accent    || "F96167",
    bg:        meta.palette?.bg        || theme.bg        || "FFFFFF",
    textDark:  meta.palette?.textDark  || theme.textDark  || "1E293B",
    textLight: meta.palette?.textLight || theme.textLight || "FFFFFF",
    textMuted: meta.palette?.textMuted || theme.textMuted || "64748B",
    fonts: { ...theme.fonts, ...meta.fonts },
  };
  const pres = new pptxgen();
  pres.layout = "LAYOUT_16x9";
  pres.author = meta.author || "";
  pres.title = meta.title || "Presentation";

  _decoIndex = 0;
  _bgVariant = 0;

  await warmIconCache();

  for (let i = 0; i < input.slides.length; i++) {
    const sd = input.slides[i];
    const slide = pres.addSlide();
    const fn = renderers[sd.layout];
    if (fn) {
      await fn(slide, sd, palette, pres);
    } else {
      slide.addText(`Unknown layout: ${sd.layout}`, {
        x: 1, y: 2, w: 8, h: 1, fontSize: 24, color: "FF0000",
      });
    }
  }
  await pres.writeFile({ fileName: outputPath });
  console.log("PPT saved:", outputPath);
}

const args = process.argv.slice(2);
renderPPT(args[0] || "slides.json", args[1] || "output.pptx").catch(console.error);