# RAW 功能移植说明

## 移植范围

只提取上游 v1.6.0 的 RAW 相关逻辑，不合并其余 Ricoh mod 改动。未执行
`git merge`，未整文件复制 `RicohHook.smali`，未改动 `build_apk.py` 既有
胶片模拟路径。

## 变更文件

### `tools/build_apk.py`

新增三个模块级常量与两个函数，并在两处既有函数内各加一行挂钩：

| 名称 | 作用 | 对应上游位置 |
|---|---|---|
| `QUALITY_MENU_ID` | `'setPictureStorageFormat'` | `update_menu_data.py` 中正则的 ItemId |
| `RAW_MENU_ENTRIES` | 两个 `Layer2` 菜单项的完整属性 | `update_menu_data.py` 第 88–115 行内联 XML |
| `RAW_LABELS` | `ItemId → (名称, guide)`，键为 ItemId | `generate_ricoh_hook.py`/`RicohHook.smali` 的查表项 |
| `QUALITY_FILTER_METHOD` | `filterQualityAvailability` 完整 smali 方法体 | `RicohHook.smali` v1.6.0 新增方法 |
| `patch_quality_menu(base)` | ElementTree 方式向画质菜单追加两项，幂等 | `update_menu_data.py` 的 Layer1 注入 |
| `patch_quality_controller(base)` | 在 `isAvailable` 锚点后注入一次 hook 调用，幂等 | `patch_apk.py:patch_picture_quality_smali` |

挂钩点：

- `patch_menu` 在写完 `ApplicationTop` 滤镜菜单后调用
  `patch_quality_menu` + `patch_quality_controller`。
- `patch_hook` 在拼接 `native_hook` 之后追加 `QUALITY_FILTER_METHOD`。
- `lookup_method` 的 `labels_map` 合并 `RAW_LABELS`，让
  `getFilterName`/`getFilterGuide` 对两个 ItemId 返回中文名称。

### `tools/check_combined.py`

在菜单与 hook 检查段追加断言：

- `setPictureStorageFormat` 菜单下存在 `setPictureStorageFormat_rawjpeg`
  （`Value="rawjpeg"`）与 `setPictureStorageFormat_raw`（`Value="raw"`），
  且 `ConfigClass` 指向 `PictureQualityController`。
- `getFilterName`/`getFilterGuide` 方法体含两个 RAW ItemId。
- `RicohHook` 中 `filterQualityAvailability(Ljava/lang/String;Z)Z` 恰好
  出现一次，含 `rawjpeg`/`raw` 白名单与 `return p1` 透传。
- `PictureQualityController.smali` 中注入点恰好一次，且位于
  `AvailableInfo.isAvailable` 的 `move-result v6` 之后。

## 与上游实现的差异（有意为之）

1. **菜单写入方式**：上游用正则字符串替换；本项目用 ElementTree 解析
   `MenuData.xml`，与 `patch_menu` 重写 `ApplicationTop` 的方式一致，且天然
   幂等（按 ItemId 去重）。
2. **注入锚点**：上游用 `str.replace`（不校验唯一性）；本项目
   `patch_quality_controller` 要求锚点恰好出现一次，否则抛错 —— 防止
   静默漏注或双注入。
3. **显示名注册**：上游把 RAW 名称写进 `RicohHook.smali` 的查表方法；
   本项目这两个方法由 `lookup_method` 生成，因此把标签并入 `RAW_LABELS`
   字典，随其它菜单项一起输出。`MenuData.xml` 的 `Title`/`DisplayName`
   直接写中文作 fallback。
4. **未引入上游的非 RAW 改动**：`getFilterName`/`getFilterGuide` 中除两个
   RAW ItemId 外的所有上游改动（理光预设名称等）不移植 —— 本项目的查表
   已由 `profiles` 驱动。

## 寄存器与签名核对

- 注入调用读 `v3`（候选 value 字符串）与 `v6`（可用性结果），写 `v6`；
  与上游一致，均在方法原有寄存器范围内，不新增 `.locals` 压力。
- `filterQualityAvailability` 签名 `(Ljava/lang/String;Z)Z` 与上游逐字符
  一致；`HOOK` 常量在 patch 阶段解析为
  `Lcom/sony/imaging/app/pictureeffectplus/shooting/camera/RicohHook;`，
  随后 `rename_package` 统一改写为 `com/yuki/...` 包名，与 hook 文件一致。

## 兼容性

- 不触碰 `applyHook`/`resetHook`/`native_hook`，RGB Matrix、Extended
  Gamma、滤镜强度、胶片预设机制完全不受影响。
- `PictureQualityController` 是框架类（`base.shooting.camera`），不在
  `rename_package` 的 `pictureeffectplus` 改名范围内，注入的引用会随
  `RicohHook` 一起正确改写为 `com.yuki`。
- 全部新增逻辑幂等：重复运行 `patch_quality_menu`/`patch_quality_controller`
  不会重复注入。
