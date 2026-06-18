---
name: unit-converter
description: >-
  Convert between common measurement units: length, weight, temperature, volume, speed.
  Use when user asks to convert units like miles to km, pounds to kg, Fahrenheit to Celsius, etc.
  NOT for currency conversion or time zone conversion.
allowed_tools: [bash]
---

# Unit Converter

通用单位换算工具，支持长度、重量、温度、体积、速度。

## 执行方式

```bash
python scripts/convert.py <value> <from_unit> <to_unit>
```

### 示例

```bash
python scripts/convert.py 100 mile km
python scripts/convert.py 72 fahrenheit celsius
python scripts/convert.py 5.5 pound kg
python scripts/convert.py 10 gallon liter
python scripts/convert.py 60 mph kmh
```

## 支持的单位

### 长度 (Length)

| 单位 | 缩写 | 换算到基准 (meter) |
|------|------|-------------------|
| 毫米 | mm | 0.001 |
| 厘米 | cm | 0.01 |
| 米 | m | 1 |
| 公里 | km | 1000 |
| 英寸 | inch | 0.0254 |
| 英尺 | foot | 0.3048 |
| 码 | yard | 0.9144 |
| 英里 | mile | 1.8 |

### 重量 (Weight)

| 单位 | 缩写 | 换算到基准 (gram) |
|------|------|-------------------|
| 毫克 | mg | 0.001 |
| 克 | g | 1 |
| 千克 | kg | 1000 |
| 磅 | pound | 453.592 |
| 盎司 | oz | 28.3495 |

### 温度 (Temperature)

温度使用特殊公式换算，不走倍率：
- C → F: `F = C × 9/5 + 32`
- F → C: `C = (F - 32) × 5/9`
- C → K: `K = C + 273.15`

### 体积 (Volume)

| 单位 | 缩写 | 换算到基准 (liter) |
|------|------|-------------------|
| 毫升 | ml | 0.001 |
| 升 | liter | 1 |
| 加仑 | gallon | 3.78541 |
| 品脱 | pint | 0.473176 |

### 速度 (Speed)

| 单位 | 缩写 | 换算到基准 (m/s) |
|------|------|-------------------|
| 米/秒 | ms | 1 |
| 公里/时 | kmh | 0.277778 |
| 英里/时 | mph | 0.44704 |
| 节 | knot | 0.514444 |

## 输出格式

```
100 mile = 180.0 km
```

## 注意事项

- 所有换算通过基准单位中转：from → base → to
- 结果保留 6 位有效数字
- 不支持的单位组合会报错
