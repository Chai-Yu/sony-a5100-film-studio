# 离线测试结果

## 测试环境

- 分支：`feature/raw-jpeg-a7r2`
- 平台：Windows 11 / PowerShell 7 / Python 3
- 关键缺失材料：`inputs/base.apk`（Ricoh v1.1.4 基础包）、
  `inputs/apktool.jar`、`inputs/luts/`、`profiles/*.json`（构建产物）

## 已执行测试

### 1. 语法检查

```
python -c "import ast; ast.parse(open('tools/build_apk.py',encoding='utf-8').read())"
python -c "import ast; ast.parse(open('tools/check_combined.py',encoding='utf-8').read())"
```

结果：两文件 AST 解析通过。

### 2. `lookup_method` 名称表注册验证

```
python -c "from build_apk import lookup_method, RAW_LABELS; \
  m=lookup_method('getFilterName',profiles,'name'); assert 'setPictureStorageFormat_rawjpeg' in m"
```

结果：`getFilterName`/`getFilterGuide` 生成的方法体均包含
`setPictureStorageFormat_rawjpeg` 与 `setPictureStorageFormat_raw` 的
ItemId 分支，返回 `RAW与JPEG`/`RAW`（unicode 转义形式）。

### 3. `patch_quality_menu` 单元测试（构造最小 MenuData）

输入含 `setPictureStorageFormat` Layer1 与既有 `fine` 项的骨架 XML。

结果：

- 首次调用追加 `setPictureStorageFormat_rawjpeg`(`Value=rawjpeg`) 与
  `setPictureStorageFormat_raw`(`Value=raw`)；`ConfigClass` 指向
  `PictureQualityController`；既有 `fine` 项保留。
- 二次调用返回空列表，无重复注入（幂等）。

### 4. `patch_quality_controller` 单元测试（构造最小 smali）

输入含上游锚点 `invoke-static {v6} ...isAvailable... move-result v6` 的
骨架 smali。

结果：

- 首次调用在 `move-result v6` 后注入
  `invoke-static {v3, v6}, L.../RicohHook;->filterQualityAvailability(...)`
  + `move-result v6`，与上游 v1.6.0 字节级一致。
- 二次调用返回 False（幂等）。
- 锚点缺失/不唯一时抛 `ValueError`（不静默）。

### 5. `QUALITY_FILTER_METHOD` 渲染核对

打印渲染结果与上游 `v1.6.0:RicohHook.smali` 中
`filterQualityAvailability` 方法逐字符比对 —— 一致（f-string 转义正确）。

## 未执行测试（缺材料）

以下测试需要基础 APK、Apktool、LUT 数据与签名私钥，当前 `inputs/` 为空，
**未执行**：

- `tools/build_apk.py` 完整构建（`--input inputs/base.apk` …）
- `tools/check_combined.py` 对真实解码产物的全套断言（含本次新增 RAW 断言）
- `tools/check_build.py` 签名/对齐/参数边界检查
- APK 安装与实机拍摄

## 结论

- ✅ 代码审计通过（上游机制确认、锚点同源）
- ✅ 静态单元验证通过（菜单注入、控制器注入、查表注册、幂等性）
- ⏸ 静态集成测试（`check_combined.py`）—— 待基础 APK
- ⏸ APK 构建 —— 待基础 APK + apktool + LUT + 签名私钥
- ⏸ 相机安装 / RAW 实际保存 / ARW 解码 —— 待实机
