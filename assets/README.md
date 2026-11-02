# assets/ — 图标

| 文件 | 用途 |
| --- | --- |
| `icon-source.jpg` | 源图(512×512,白底圆形徽章) |
| `app-icon.ico` | 成品图标:网页 favicon + 仓库内引用 |
| `app-icon_256.png` | README 里展示用的 256 px 预览 |

## 重新生成

```bash
python tools/make_icon.py assets/icon-source.jpg assets/app-icon.ico
```

脚本做三件事:按**圆的几何**把圆外白底抠透明(不做全局「白→透明」,否则徽章上的白字会被一起镂空)、
生成 16/24/32/48/64/128 六个尺寸、再部署一份到 `%LOCALAPPDATA%\aspen-insight\` 供桌面快捷方式使用。

**别直接「另存为 .ico」**:Pillow 会写 PNG 压缩条目,资源管理器(GDI)只认 BMP 条目,
桌面图标会变成白块。原因与实测过程写在 `tools/make_icon.py` 顶部。

## 注意

桌面快捷方式里存的是**绝对路径**,指向 `%LOCALAPPDATA%` 的副本;项目搬家或副本被删后,
重跑上面的命令,再把快捷方式的 `IconLocation` 指过去。
