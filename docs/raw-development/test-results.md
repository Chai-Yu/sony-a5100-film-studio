# 离线测试结果

## 测试环境

- 分支：`feature/raw-jpeg-a7r2`
- 平台：Windows 11 / PowerShell 7 / Python 3
- 全部材料就位：`inputs/base.apk`（Ricoh v1.1.4 成品，SHA `80cb4a54…` ✅）、
  `inputs/apktool.jar`（2.12.1）、`inputs/upstream`（`7c56589`，hook SHA
  `2db88c8e…` ✅）、富士 GFX ETERNA 55 v1.10 LUT、徕卡 SL2-S LUT、
  java 21 / adb / openssl 3.5.4 / numpy 2.3.5

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

## 未执行测试（待实机）

- APK 安装与实机拍摄（RAW 是否真正落 ARW、滤镜画质、录像/回放稳定性）

> ⚠️ **静态验证 ≠ RAW 能保存**：全部静态检查只证明 patch 注入正确、
> 调用链闭合、签名有效；RAW 是否真正写 ARW 只能 A7R2 实机验证。

## 完整构建验证（2026-09-22 补全）

材料齐备后按 INSTALL 流程端到端跑通：

### LUT 拟合

- `fit_luts.py`（富士 GFX ETERNA 55 v1.10，`inputs/.../F-Log2`）：
  10 胶片 profiles，MAE 0.06–0.13，p95 ~0.2–0.32 → `profiles/fuji_official_approx.json`
- `calibrate_camera.py --factor 0.75`：chroma 缩放补偿机身 Standard 色偏 →
  `domain_calibration` 写入
- `fit_leica.py`（徕卡 SL2-S，`Classic_Rec709`/`Natural_Rec709`，Film V6.3 /
  Modern V6.2 Rec709 Gamma 2.4）：Classic MAE 0.0157、Natural MAE 0.0136 →
  `profiles/leica_look_approx.json`

### 构建

```
python tools/build_apk.py --input inputs/base.apk --apktool inputs/apktool.jar \
  --upstream-hook inputs/upstream/src/smali/RicohHook.smali \
  --work build-local/decoded-021 --movie
```

产物：`output/FilmStudio-0.2.2-alpha-movie.apk`（3,844,975 bytes，17 滤镜，
684 文件 RSA 签名）。SHA-256 `4a60bf92d43e24afd98bde1c05cf3603b261d6f405500c0b03a558c5627cad1e`。

### `check_combined.py`（对最终签名 APK 解码）

全通过：17 profiles、136 编译数组、菜单/查表 ID 一致、Ricoh 上游参数逐位
精确、movie 快捷方式存在、0 占位 drawable、manifest 0.2.2。**含 RAW 断言**：
`setPictureStorageFormat_rawjpeg`/`_raw` 菜单项存在且 Value 正确、
`ConfigClass=PictureQualityController`、`filterQualityAvailability` 恰好 1 个
方法、白名单 `"rawjpeg"`/`"raw"` + `return p1` 透传、`PictureQualityController`
注入点恰好 1 处且位于 `isAvailable` 之后。

### `check_build.py`

`cube_axis_test` / `profile_bounds_test` / `exported_cube_grid_roundtrip` /
签名（`openssl smime -verify` 684 条目）/ `resources.arsc` 对齐 全部通过。

> **上游既有缺陷修复**：`check_build.py` 原用 `p['matrix']`（校准后）对比
> `SonyProxy_*.cube`（`fit_luts.py` 以校准前矩阵写出），校准改变了 matrix
> 而 cube 未重写 → 恒失败。改为对比 `p['matrix_fit']`（cube 实际对应的拟合
> 矩阵），与「导出 cube == 拟合模型」的本意一致。与 RAW 改动无关。
>
> **`--no-leica` 是上游死路径**：`PRESET_COUNT=17` 与 `NATIVE_LOOK`（17 键）
> 硬编码，`leica=[]` 时 id 集合必不等 → 必然抛错。徕卡 LUT 实为必需。

### 最终 APK 内 RAW 产物实测

- `MenuData.xml`：`rawjpeg`/`raw` 两项，`DisplayName="RAW与JPEG"`/`"RAW"`，
  图标 `uncompressed_raw_j`/`_raw` 存在
- `RicohHook.smali`（`com/yuki` 路径）：`filterQualityAvailability` 方法体
  `if-nez p1 → 白名单 → return 1 / return p1` 逻辑正确
- `PictureQualityController.smali`：`aput-object` 存值 → `isAvailable` →
  `move-result v6` → `invoke-static {v3,v6} RicohHook->filterQualityAvailability`，
  恰好 1 处
- 签名证书：自签 `CN=SonyPMCADebug, O=Community, C=US`，有效期至 2054

## 结论

- ✅ 代码审计通过（上游机制确认、锚点同源）
- ✅ 静态单元验证通过（菜单注入、控制器注入、查表注册、幂等性）
- ✅ 真实 base.apk 端到端 patch 验证通过
- ✅ **完整 17 滤镜构建通过**，`check_combined`/`check_build` 全绿，APK 已签名
- ⏸ 相机安装 / RAW 实际保存 / ARW 解码 —— **待实机**（离线验证完成，可交付测试）
