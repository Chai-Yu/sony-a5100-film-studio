# RAW 功能上游分析（sony-pmca-ricoh-mod v1.6.0）

## 来源定位

上游仓库 `bonyback1/sony-pmca-ricoh-mod` 的 RAW + JPEG / 纯 RAW 功能由单个 commit
引入：

- Commit：`6df18827bf219ef04a993eeb41ca3e699226ac58`
- Tag：`v1.6.0`（B2.4），日期 2026-09-18
- 涉及文件：`tools/update_menu_data.py`、`tools/patch_apk.py`、
  `tools/generate_ricoh_hook.py`、`src/smali/RicohHook.smali`，以及说明文档。

本项目 `build_apk.py` 的 `EXPECTED`（SHA-256
`80cb4a541f5f3dd49e8f53ffb1905048097fec17209fc9cb595a00681e65e8ea`）锁定的是
**上游 Ricoh mod v1.1.4 的成品 APK** `PictureEffectPlus_Ricoh.apk`——即上游
patch 管线对 Sony 官方 `pictureeffectplus` 应用打完所有 Ricoh patch 后的
产物。`build_apk.py` 反编译该成品后直接 patch 已存在的 `RicohHook.smali`，
这一事实印证了"输入即 Ricoh 成品"。

上游 v1.6.0（`6df1882`，B2.4）是在**同一个 Ricoh v1.1.4 成品基础**之上迭代的
patch 集合；其 GitHub Release 附带的 `PictureEffectPlus_Ricoh.apk`（SHA-256
`5fc34156c4f07fc05e5713bd9acad66b9cc67b7ba98e9fb7b79db9caba8bce54`）是
v1.6.0 patch 后的新成品，并非干净的基础包。

因此 v1.1.4 与 v1.6.0 之间的差异是**同一 Sony 底座上两组不同 patch 的差集**，
RAW 功能（`filterQualityAvailability` + 菜单项 + 查表项）属于该差集的一部分，
注入锚点（`AvailableInfo.isAvailable`、`setPictureStorageFormat` 菜单）在两个
版本间保持一致，可直接移植。

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
`ExecType="SET_VALUE"`。图标与 caution 资源直接引用 Sony 底座中已有的
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

- **机制可信（链路层面）**：RAW 值走 Sony 原生
  `CameraSetting.setPictureStorageFormat` → `CameraEx` 落盘，不经过 JPEG
  重封装；菜单 value 与 Sony 内部值一致（`rawjpeg`/`raw`），非伪造。
  **但"链路存在"不等于"A7R2 机身真的落 ARW"**——上游 Release 自述
  「14-bit 无损 ARW」「19/19 自动化测试 100% PASS」，未附 ARW 样本或可独立
  核对的输出证据，最终必须 A7R2 实机验证。
- **可用性强制是"乐观"的**：`filterQualityAvailability` 只改 UI 层答案，
  机身硬件是否真正落 ARW 仍需实机验证；若某场景原生判断"不可用"是硬限制
  （如录像中），强制放行可能导致菜单可选但拍摄失败。
- **与 A7R2 钩子的关系**：两者改的是不同文件（画质控制器 vs
  `pictureeffectplus` 应用钩子），不冲突；但 A7R2 的
  `getFilterName`/`getFilterGuide` 整体重写后，RAW 显示名必须并入本项目
  的 `lookup_method` 表，否则菜单项显示为空白。
- **未被上游覆盖的检查项**：切换滤镜时画质是否被重置、录像模式与纯 RAW
  的互斥、异常退出后的恢复 —— 上游未做特殊处理，依赖原生控制器状态机，
  属待实机验证项。

## 方案对比记录（2026-09-22 修订）

| | 方案 A | 方案 B（已实施） |
|---|---|---|
| 输入 | Ricoh **v1.6.0** 成品 APK（SHA `5fc34156…`） | Ricoh **v1.1.4** 成品 APK（SHA `80cb4a54…`） |
| 做法 | 把 A7R2 全套 patch 适配到 v1.6.0 反编译产物 | 保留 v1.1.4 底座，只移植 RAW 相关 patch |
| 差异 | v1.6.0 除 RAW 外还含 v1.2.0–v1.5.0 的理光 LUT 重写、菜单/触发器/安装脚本改动等，需逐一甄别哪些是 A7R2 已覆盖、哪些会冲突 | 只引入 RAW 三件套，其余 v1.6.0 改动不引入 |
| 工作量 | 需重新验证 `build_apk.py` 全部 smali 锚点在 v1.6.0 smali 上的命中情况 | 仅新增 2 函数 + 1 smali 方法 + 断言 |
| 风险 | v1.6.0 自带一套理光滤镜实现，与 A7R2 的富士/徕卡管线语义重叠，存在双重 patch/重复类/菜单冲突风险 | 不触碰既有 patch，回归面最小 |

**当前选择：方案 B**（commit `56b6c0f`）。方案 A 作为后续可研究的架构升级
路线保留——需先取得 v1.6.0 成品 APK 做反编译对比（锚点存活率扫描、
RicohHook/PictureEffectPlusController/PictureQualityController 三个类的
结构差异），再评估适配工作量；在缺少反编译对比与测试证据前，不预设其
可行或不可行。
