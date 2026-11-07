# assets/ — 图标

| 文件 | 用途 |
| --- | --- |
| `icon-source.jpg` | 源图(512×512,白底圆形徽章) |
| `app-icon.ico` | 成品图标:网页 favicon 与桌面快捷方式共用 |
| `app-icon_256.png` | README 里展示用的 256 px 预览 |

## 重新生成

```bash
python tools/make_icon.py assets/icon-source.jpg assets/app-icon.ico
```

脚本按**圆的几何**把圆外白底抠透明(不做全局「白→透明」,否则徽章上的白字会被一起镂空),
并生成 16 / 24 / 32 / 48 / 64 / 128 六个尺寸。

注意:请用脚本重新生成,不要直接用图片工具「另存为 .ico」——部分工具写的是 PNG 压缩条目,
而 Windows 资源管理器只认 BMP 条目,桌面图标会显示成白块。

脚本还会把成品复制一份到 `%LOCALAPPDATA%\Casebook\`(供桌面快捷方式引用);只跑源码不看快捷方式的话可以忽略。
