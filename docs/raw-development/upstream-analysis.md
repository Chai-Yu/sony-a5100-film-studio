# RAW 功能上游分析（sony-pmca-ricoh-mod v1.6.0）

## 来源定位

上游仓库 `bonyback1/sony-pmca-ricoh-mod` 的 RAW + JPEG / 纯 RAW 功能由单个 commit
引入：

- Commit：`6df18827bf219ef04a993eeb41ca3e699226ac58`
- Tag：`v1.6.0`（B2.4），日期 2026-09-18
- 涉及文件：`tools/update_menu_data.py`、`tools/patch_apk.py`、
  `tools/generate_ricoh_hook.py`、`src/smali/RicohHook.smali`，以及说明文档。

本项目 pin 的上游 `RicohHook.smali`（v1.1.4，`7c56589…`，SHA-256
`2db88c8e…`）是该 commit 的祖先，即二者基于同一 Sony 基础应用
`com.sony.imaging.app.pictureeffectplus`，注入锚点兼容。

## 真实机制

Sony 的 Picture Effect+ 应用本身具备 RAW 保存链路：

```
MenuData.xml  Layer1[ItemId="setPictureStorageFormat"]
      └─ Layer2  Value="jpeg" | "rawjpeg" | "raw"
                    ↓
PictureQualityController (com.sony.imaging.app.base.shooting.camera)
   getAvailableValue → AvailableInfo.isAvailable([values]) 逐项过滤
   setValue → CameraSetting.setPictureStorageFormat(value)
                    ↓
Sony CameraEx / 原生拍摄执行器
                    ↓
              DSC0001.JPG / .ARW
```

RAW 之所以在原始应用里不可选，是因为 `AvailableInfo.isAvailable` 把
`rawjpeg`/`raw` 标记为不可用，菜单项被隐藏/拒绝。**上游并未新增任何保存
通道** —— 它只是：(a) 把菜单项加回来；(b) 强制可用性答案为 true；(c) 提供
本地化显示名。

## v1.6.0 的三处改动

### 1. `MenuData.xml`（`tools/update_menu_data.py`）

在 `Layer1[ItemId="setPictureStorageFormat"]` 内部插入两个 `Layer2`：

| ItemId | Value | Title | CautionID | IconRes |
|---|---|---|---|---|
| `setPictureStorageFormat_rawjpeg` | `rawjpeg` | RAW & JPEG | `CAUTION_GRP_ID_STILL_IMAGE_QUALITY_RAW_JPEG_INVALID_GUIDE` | `drawable/p_16_dd_parts_5w_shoot_icon_imgquality_uncompressed_raw_j` |
| `setPictureStorageFormat_raw` | `raw` | RAW | `CAUTION_GRP_ID_STILL_IMAGE_QUALITY_RAW_INVALID_GUIDE` | `drawable/p_16_dd_parts_5w_shoot_icon_imgquality_uncompressed_raw` |

两项均 `ConfigClass="com.sony.imaging.app.base.shooting.camera.PictureQualityController"`、
`ExecType="SET_VALUE"`。图标与 caution 资源直接引用基础包已有的
`uncompressed_raw` 素材，不新增资源。

### 2. `PictureQualityController.smali`（`tools/patch_apk.py`）

在 `getAvailableValue` 中 `AvailableInfo.isAvailable` 的结果上叠加一次过滤：

```smali
invoke-static {v6}, Lcom/sony/imaging/app/util/AvailableInfo;->isAvailable([Ljava/lang/Object;)Z
move-result v6
+invoke-static {v3, v6}, L.../RicohHook;->filterQualityAvailability(Ljava/lang/String;Z)Z
+move-result v6
```

`v3` 持有当前候选值字符串，`v6` 是原生可用性答案。

### 3. `RicohHook.smali`（`tools/generate_ricoh_hook.py` 同步生成）

新增方法：

```smali
.method public static filterQualityAvailability(Ljava/lang/String;Z)Z
```

逻辑：`p1==false`（原生不可用）时直接返回原值；为 true 且 `p0` 是
`rawjpeg`/`raw` 时强制返回 true；其余透传。同时在 `getFilterName` /
`getFilterGuide` 注册两个 ItemId 的英/繁/简显示名。

## 结论与风险

- **机制可信**：RAW 值走 Sony 原生 `CameraSetting.setPictureStorageFormat` →
  `CameraEx` 落盘，不经过 JPEG 重封装；菜单 value 与 Sony 内部值一致
  （`rawjpeg`/`raw`），非伪造。
- **可用性强制是"乐观"的**：`filterQualityAvailability` 只改 UI 层答案，
  机身硬件是否真正落 ARW 仍需实机验证；若某场景原生判断"不可用"是硬限制
  （如录像中），强制放行可能导致菜单可选但拍摄失败。
- **与 A7R2 钩子的关系**：两者改的是不同文件（画质控制器 vs 画质控制器
  所属的 `pictureeffectplus` 应用钩子），不冲突；但 A7R2 的
  `getFilterName`/`getFilterGuide` 整体重写后，RAW 显示名必须并入本项目
  的 `lookup_method` 表，否则菜单项显示为空白。
- **未被上游覆盖的检查项**：切换滤镜时画质是否被重置、录像模式与纯 RAW
  的互斥、异常退出后的恢复 —— 上游未做特殊处理，依赖原生控制器状态机，
  属待实机验证项。
