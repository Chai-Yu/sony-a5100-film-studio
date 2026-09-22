# 离线测试结果

## 测试环境

- 分支：`feature/raw-jpeg-a7r2`
- 平台：Windows 11 / PowerShell 7 / Python 3
- 关键缺失材料：`inputs/luts/`（富士 GFX ETERNA 55 v1.10 + 徕卡 SL2-S 官方
  LUT 包，受限）、`profiles/*.json`（待 LUT 拟合生成）
- 已就位：`inputs/base.apk`（Ricoh v1.1.4 成品，SHA-256 `80cb4a54…` ✅ 匹配）、
  `inputs/apktool.jar`（Apktool 2.12.1 ✅）、`inputs/upstream`（`7c56589`，
  hook SHA `2db88c8e…` ✅）、java 21 / adb / openssl 3.5.4（Git 自带）

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

### 6. 真实 base.apk 端到端验证（2026-09-22 新增）

对 Ricoh v1.1.4 成品 APK（SHA `80cb4a54…`）执行 `apktool d -r` 反编译后，
在真实解码产物上跑了完整 patch 链路（`patch_hook` → `patch_quality_menu` →
`patch_quality_controller` → `rename_package`），结果：

| 验证点 | 结果 |
|---|---|
| `PictureQualityController.smali` 存在 | ✅ |
| `AvailableInfo.isAvailable` 锚点 | ✅ 恰好 1 处命中，唯一 |
| 注入位置 | ✅ `aput-object` 存值 → `isAvailable` → `move-result v6` → **注入** `invoke-static {v3,v6} RicohHook->filterQualityAvailability` + `move-result v6` → `if-eqz v6` → `ArrayList.add` |
| `Layer1[setPictureStorageFormat]` 菜单 | ✅ 存在，注入 `rawjpeg`/`raw` 两项成功，`ConfigClass=PictureQualityController` 与同级项一致 |
| `uncompressed_raw(_j)` 图标 drawable | ✅ 存在于 `drawable-{long,nodpi,notlong}-nodpi` |
| `RicohHook.smali` 已存在于 base | ✅（证实输入为 Ricoh 成品） |
| `filterQualityAvailability` 追加 | ✅ 恰好 1 个方法，签名 `(Ljava/lang/String;Z)Z` |
| ItemId 查表注册 | ✅ `setPictureStorageFormat_rawjpeg`/`_raw` 均入 `getFilterName`/`getFilterGuide` |
| 幂等性 | ✅ 菜单/控制器二次调用无重复注入 |
| `rename_package` 改写 | ✅ 控制器中引用改写为 `Lcom/yuki/.../RicohHook`，hook 文件同步移至 `com/yuki` 路径，`filterQualityAvailability` 保留 |

**逻辑链路验证**：RAW 候选值（`v3`）经 `isAvailable` 得原生答案（`v6`）后，
注入的 `filterQualityAvailability(v3,v6)` 重写 `v6`，`if-eqz v6` 据此把
`rawjpeg`/`raw` 加入可用列表——与上游 v1.6.0 的解锁机制完全一致。

## 未执行测试（缺材料）

以下测试需要**富士/徕卡官方 LUT**（生成 `profiles/`）与签名私钥，**未执行**：

- `tools/fit_luts.py` + `tools/calibrate_camera.py` —— 需富士 GFX ETERNA 55
  v1.10 LUT 包（11 个 `.cube`）；`calibrate_camera.py` 可用 `--factor` 标量
  缩放替代实测场景，仍须 LUT 作为拟合目标
- `tools/fit_leica.py` —— 需徕卡 SL2-S LUT；可用 `--no-leica` 跳过徕卡家族
- `tools/build_apk.py` 完整构建（profiles 是硬门槛，先于 smali patch）
- `tools/check_combined.py` / `check_build.py` 对真实构建产物
- APK 安装与实机拍摄

> ⚠️ **静态验证 ≠ RAW 能保存**：上述第 6 项只证明 patch 在真实 smali 上
> 注入正确、调用链闭合；RAW 是否真正落 ARW 只能 A7R2 实机验证。

## 结论

- ✅ 代码审计通过（上游机制确认、锚点同源）
- ✅ 静态单元验证通过（菜单注入、控制器注入、查表注册、幂等性）
- ✅ **真实 base.apk 端到端 patch 验证通过**（注入位置/调用链/重命名/幂等）
- ⏸ 完整 APK 构建 —— **唯一阻塞：富士/徕卡官方 LUT 包**（生成 profiles 的前置）
- ⏸ 相机安装 / RAW 实际保存 / ARW 解码 —— 待实机
